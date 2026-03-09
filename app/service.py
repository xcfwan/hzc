import asyncio
import datetime as dt
import httpx
from app.config import settings
from app.hetzner_client import HetznerClient
from app.telegram_bot import Tg
from app.qb_client import QBClient
from app.qb_store import QBStore
from app.auto_policy_store import AutoPolicyStore
from app.runtime_config import RuntimeConfig

# Keep same unit behavior as Hetzner panel (binary TiB, though UI labels TB)
BYTES_IN_TB = 1024**4


class MonitorService:
    def __init__(self):
        self.client = HetznerClient(settings.hetzner_token)
        self.tg = Tg(settings.telegram_bot_token, settings.telegram_chat_id)
        self.qb = QBClient(settings.qb_url, settings.qb_username, settings.qb_password)
        self.qb_store = QBStore(settings.qb_store_path)
        self.auto_policy = AutoPolicyStore(settings.auto_policy_path)
        self.runtime = RuntimeConfig(settings.runtime_config_path)
        self.last_snapshot = []
        self._collect_cache = []
        self._collect_cache_ts = 0.0
        self._collect_cache_ttl = 6.0

    async def meta(self):
        types = await self.client.list_server_types()
        locations = await self.client.list_locations()
        try:
            snapshots = await self.client.list_snapshots()
        except Exception:
            snapshots = []
        try:
            pips = await self.client.list_primary_ips()
        except Exception:
            pips = []
        try:
            servers = await self.client.list_servers()
        except Exception:
            servers = []

        used_ipv4 = {}
        used_ipv6 = {}
        for s in servers:
            sid = s.get("id")
            sname = s.get("name")
            net = s.get("public_net") or {}
            v4id = ((net.get("ipv4") or {}).get("id"))
            v6id = ((net.get("ipv6") or {}).get("id"))
            if v4id:
                used_ipv4[int(v4id)] = {"server_id": sid, "server_name": sname}
            if v6id:
                used_ipv6[int(v6id)] = {"server_id": sid, "server_name": sname}

        return {
            "app_version": settings.app_version,
            "app_commit": settings.app_commit,
            "server_types": [
                {
                    "name": t.get("name"),
                    "cores": t.get("cores"),
                    "memory": t.get("memory"),
                    "disk": t.get("disk"),
                    "prices": t.get("prices", []),
                    # API可售性（非实时库存）：根据 Hetzner server_types.prices 是否包含该 location
                    "sellable_locations": [p.get("location") for p in (t.get("prices") or []) if p.get("location")],
                }
                for t in types
            ],
            "locations": [{"name": l.get("name"), "city": l.get("city")} for l in locations],
            "snapshots": [
                {
                    "id": i.get("id"),
                    "name": i.get("description") or i.get("name"),
                    "size_gb": round(float(i.get("image_size") or 0), 2),
                    "created": i.get("created"),
                }
                for i in snapshots
            ],
            "primary_ipv4s": [
                {
                    "id": p.get("id"),
                    "ip": p.get("ip"),
                    "name": p.get("name") or f"ip-{p.get('id')}",
                    "location": ((p.get("datacenter") or {}).get("location") or {}).get("name") or (p.get("location") or {}).get("name"),
                    "datacenter": (p.get("datacenter") or {}).get("name"),
                    "occupied": bool((p.get("assignee") or {}).get("id") or used_ipv4.get(int(p.get("id") or 0))),
                    "occupied_by": used_ipv4.get(int(p.get("id") or 0)) or {
                        "server_id": ((p.get("assignee") or {}).get("id")),
                        "server_name": ((p.get("assignee") or {}).get("name")) or "unknown",
                    },
                }
                for p in pips
                if p.get("type") == "ipv4"
            ],
            "primary_ipv6s": [
                {
                    "id": p.get("id"),
                    "ip": p.get("ip"),
                    "name": p.get("name") or f"ip-{p.get('id')}",
                    "location": ((p.get("datacenter") or {}).get("location") or {}).get("name") or (p.get("location") or {}).get("name"),
                    "datacenter": (p.get("datacenter") or {}).get("name"),
                    "occupied": bool((p.get("assignee") or {}).get("id") or used_ipv6.get(int(p.get("id") or 0))),
                    "occupied_by": used_ipv6.get(int(p.get("id") or 0)) or {
                        "server_id": ((p.get("assignee") or {}).get("id")),
                        "server_name": ((p.get("assignee") or {}).get("name")) or "unknown",
                    },
                }
                for p in pips
                if p.get("type") == "ipv6"
            ],
        }

    async def daily_stats(self, days: int = 7):
        servers = await self.client.list_servers()
        result = []
        for s in servers:
            try:
                daily = await self.client.get_outbound_daily(s["id"], days=days)
            except Exception:
                daily = []
            result.append({"id": s["id"], "name": s["name"], "daily": daily})
        return result

    async def collect(self, use_cache: bool = True):
        if use_cache:
            now_ts = dt.datetime.utcnow().timestamp()
            if self._collect_cache and (now_ts - self._collect_cache_ts) < self._collect_cache_ttl:
                return self._collect_cache

        servers = await self.client.list_servers()
        rows = []

        qb_nodes = self.qb_store.get_all()
        policies = self.auto_policy.all()
        qb_tasks = {}
        for sid, node in qb_nodes.items():
            qb_tasks[str(sid)] = asyncio.create_task(QBClient.fetch_stats(node.get("url", ""), node.get("username", ""), node.get("password", "")))

        # parallelize today-bytes metrics to reduce end-to-end latency
        today_tasks = {}
        for s in servers:
            sid = s["id"]
            today_tasks[sid] = asyncio.create_task(self.client.get_outbound_today_bytes(sid, settings.timezone))

        for s in servers:
            # Billing-consistent logic: Hetzner official traffic OUT (external upload only)
            outbound = int(s.get("outgoing_traffic") or 0)
            used_tb = outbound / BYTES_IN_TB
            used_gb = outbound / (1024**3)
            included_tb = (int(s.get("included_traffic") or 0) / BYTES_IN_TB) or settings.traffic_limit_tb
            pct = used_tb / included_tb if included_tb > 0 else 0
            try:
                today_bytes = await today_tasks[s["id"]]
            except Exception:
                today_bytes = 0
            try:
                daily = await self.client.get_outbound_daily(s["id"], days=2)
            except Exception:
                daily = []
            today_gb = (today_bytes / (1024**3)) if today_bytes else 0.0
            qbs = {"enabled": False}
            t = qb_tasks.get(str(s["id"]))
            if t:
                try:
                    qbs = await t
                except Exception as e:
                    qbs = {"enabled": True, "error": str(e)}

            pol = policies.get(str(s["id"]), {})
            row = {
                "id": s["id"],
                "name": s["name"],
                "status": s["status"],
                "ip": s.get("public_net", {}).get("ipv4", {}).get("ip", ""),
                "server_type": s.get("server_type", {}).get("name", ""),
                "cores": s.get("server_type", {}).get("cores", 0),
                "memory_gb": s.get("server_type", {}).get("memory", 0),
                "disk_gb": s.get("server_type", {}).get("disk", 0),
                "used_tb": round(used_tb, 8),
                "used_gb": round(used_gb, 4),
                "today_gb": round(today_gb, 4),
                "used_bytes": int(outbound),
                "today_bytes": int(today_bytes),
                "limit_tb": round(included_tb, 8),
                "ratio": round(pct, 4),
                "over_threshold": pct >= float(pol.get("threshold", settings.rotate_threshold)),
                "qb": qbs,
                "auto_policy": pol,
            }
            rows.append(row)
        self.last_snapshot = rows
        self._collect_cache = rows
        self._collect_cache_ts = dt.datetime.utcnow().timestamp()
        return rows

    def get_safe_mode(self):
        rc = self.runtime.get()
        if "safe_mode" in rc:
            return bool(rc.get("safe_mode"))
        return bool(settings.safe_mode)

    def set_safe_mode(self, enabled: bool):
        self.runtime.update({"safe_mode": bool(enabled)})
        return {"ok": True, "safe_mode": bool(enabled)}

    async def rotate_if_needed(self):
        rows = await self.collect(use_cache=False)
        safe_mode = self.get_safe_mode()
        for row in rows:
            pol = row.get("auto_policy") or {}
            enabled = bool(pol.get("enabled", False))
            threshold = float(pol.get("threshold", settings.rotate_threshold))
            used_tb = float(row.get("used_tb", 0) or 0)
            over = bool(row.get("over_threshold", False))

            # 仅在“接近阈值/已超阈值”场景发策略日志，避免刷屏
            near_threshold = used_tb >= (threshold * 0.9)

            if not enabled:
                if over or near_threshold:
                    await self.tg.send(
                        f"ℹ️ 自动重建未执行: {row['name']} (ID:{row['id']})\n"
                        f"原因: 策略未启用\n"
                        f"当前: {used_tb:.2f} TB / 阈值: {threshold:.2f} TB"
                    )
                continue

            if over:
                if safe_mode:
                    await self.tg.send(
                        f"⚠️ SAFE_MODE 告警: {row['name']} (ID:{row['id']}) 达到自动阈值 {threshold:.2f} TB，"
                        f"当前 {used_tb:.2f} TB，仅通知不执行"
                    )
                    continue
                image_id = pol.get("image_id")
                if not image_id:
                    await self.tg.send(
                        f"⚠️ 自动重建未执行: {row['name']} (ID:{row['id']})\n"
                        f"原因: 未配置重建镜像/快照\n"
                        f"当前: {used_tb:.2f} TB / 阈值: {threshold:.2f} TB"
                    )
                    continue
                await self.tg.send(
                    f"🚀 自动重建开始: {row['name']} (ID:{row['id']})\n"
                    f"当前: {used_tb:.2f} TB / 阈值: {threshold:.2f} TB\n"
                    f"镜像/快照: {image_id}"
                )
                await self.rebuild_with_snapshot_manual(row["id"], image_id)

    async def rotate_server(self, server_id: int):
        servers = await self.client.list_servers()
        src = next((s for s in servers if s["id"] == server_id), None)
        if not src:
            return {"ok": False, "error": "server not found"}

        desc = f"auto-rotate-{src['name']}-{dt.datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
        snap = await self.client.create_snapshot(server_id, desc)
        action_id = snap["action"]["id"]

        image_id = None
        for _ in range(120):
            action = await self.client.get_action(action_id)
            if action.get("status") == "success":
                image_id = snap.get("image", {}).get("id")
                break
            if action.get("status") == "error":
                await self.tg.send(f"❌ Rotate failed for {src['name']}: snapshot action error")
                return {"ok": False, "error": "snapshot failed"}
            await asyncio.sleep(5)

        if not image_id:
            await self.tg.send(f"❌ Rotate timeout for {src['name']} during snapshot")
            return {"ok": False, "error": "snapshot timeout"}

        new_srv = await self.client.create_server_from_image(src, image_id)
        await self.client.delete_server(server_id)
        await self.tg.send(f"✅ Rotated {src['name']} -> {new_srv['server']['name']}")
        return {"ok": True, "new_server": new_srv}

    async def estimate_snapshot(self, server_id: int):
        servers = await self.client.list_servers()
        src = next((s for s in servers if s["id"] == server_id), None)
        if not src:
            return {"ok": False, "error": "server not found"}
        disk_gb = float(src.get("server_type", {}).get("disk", 0) or 0)

        # Better estimate: use average existing snapshot size if available, else 35% of disk
        try:
            snapshots = await self.client.list_snapshots()
        except Exception:
            snapshots = []
        sizes = [float(i.get("image_size") or 0) for i in snapshots if i.get("image_size")]
        avg_size = (sum(sizes) / len(sizes)) if sizes else 0
        est_size_gb = round(avg_size if avg_size > 0 else (disk_gb * 0.35), 2)
        est_size_gb = min(max(est_size_gb, 1.0), disk_gb if disk_gb > 0 else est_size_gb)

        est_monthly = round(est_size_gb * settings.snapshot_price_per_gb, 4)
        return {
            "ok": True,
            "server_id": server_id,
            "server_name": src.get("name"),
            "disk_gb": disk_gb,
            "estimated_snapshot_size_gb": est_size_gb,
            "snapshot_price_per_gb": settings.snapshot_price_per_gb,
            "estimated_monthly_eur": est_monthly,
            "estimation_note": "基于历史快照均值；无历史时按磁盘35%估算",
        }

    async def create_snapshot_manual(self, server_id: int, description: str | None = None):
        if not description:
            description = f"manual-snap-{server_id}-{dt.datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
        res = await self.client.create_snapshot(server_id, description)
        await self.tg.send(f"📸 Snapshot started for server {server_id}: {description}")
        return {"ok": True, "message": "snapshot job started", "action": res.get("action", {}), "description": description}

    @staticmethod
    def _extract_password(payload: dict):
        if not isinstance(payload, dict):
            return None
        # Common places where Hetzner may return temporary root password
        candidates = [
            payload.get("root_password"),
            (payload.get("action") or {}).get("root_password"),
            (payload.get("next_actions") or [{}])[0].get("root_password") if payload.get("next_actions") else None,
        ]
        for c in candidates:
            if c:
                return c
        return None

    async def reset_password_and_notify(self, server_id: int, server_name: str | None = None):
        res = await self.client.server_action(server_id, "reset_password")
        pwd = self._extract_password(res)
        if pwd:
            await self.tg.send(f"🔐 服务器密码已重置\nID: {server_id}\n名称: {server_name or '-'}\n新密码: {pwd}")
            return {"ok": True, "server_id": server_id, "new_password": pwd}
        await self.tg.send(f"⚠️ 服务器 {server_id} 已触发重置密码，但未在响应中拿到明文密码，请到 Hetzner Console 查看 Action 结果。")
        return {"ok": True, "server_id": server_id, "new_password": None, "note": "password not returned by api response"}

    async def create_server_manual(self, name: str, server_type: str, location: str, image, primary_ip_id: int | None = None, primary_ipv6_id: int | None = None):
        created = None
        try:
            # placement 波动下先按原参数重试几次
            last_err = None
            for i in range(3):
                try:
                    created = await self.client.create_server(
                        name=name,
                        server_type=server_type,
                        location=location,
                        image=image,
                        primary_ip_id=primary_ip_id,
                        primary_ipv6_id=primary_ipv6_id,
                    )
                    break
                except httpx.HTTPStatusError as e:
                    last_err = e
                    code = None
                    try:
                        code = (e.response.json().get("error") or {}).get("code")
                    except Exception:
                        pass
                    if e.response is not None and e.response.status_code == 412 and code == "resource_unavailable":
                        await asyncio.sleep(2 + i * 2)
                        continue
                    raise

            # 不允许跨机房兜底：placement 不可用时直接失败并返回明确错误
            if created is None and isinstance(last_err, httpx.HTTPStatusError):
                raise last_err

        except Exception as e:
            detail = str(e)
            if isinstance(e, httpx.HTTPStatusError) and e.response is not None:
                try:
                    detail = f"HTTP {e.response.status_code}: {e.response.text}"
                except Exception:
                    detail = str(e)
            return {
                "ok": False,
                "error": detail,
                "request": {
                    "name": name,
                    "server_type": server_type,
                    "location": location,
                    "image": image,
                    "primary_ip_id": primary_ip_id,
                    "primary_ipv6_id": primary_ipv6_id,
                },
            }

        srv = created.get("server", {})
        sid = srv.get("id")
        sname = srv.get("name", name)
        await self.tg.send(f"🆕 New server created: {sname} (ID: {sid})")

        # Try directly from create response, fallback to reset-password workflow.
        pwd = self._extract_password(created)
        if pwd:
            await self.tg.send(f"🔐 新服务器初始密码\nID: {sid}\n名称: {sname}\n密码: {pwd}")
            created["new_password"] = pwd
            return created

        if sid:
            rp = await self.reset_password_and_notify(sid, sname)
            created["password_reset"] = rp
        return created

    async def delete_server_manual(self, server_id: int, create_snapshot: bool = False, keep_ipv4: bool = False, keep_ipv6: bool = False, keep_mode: str = "safe"):
        srv = await self.client.get_server(server_id)
        if not srv:
            return {"ok": False, "error": "server not found"}

        snapshot_action = None
        snapshot_name = None
        if create_snapshot:
            snapshot_name = f"before-delete-{srv.get('name','server')}-{dt.datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
            snap_res = await self.client.create_snapshot(server_id, snapshot_name)
            snapshot_action = (snap_res or {}).get("action", {})
            action_id = snapshot_action.get("id")
            if action_id:
                for _ in range(180):  # up to ~15min
                    act = await self.client.get_action(action_id)
                    st = (act or {}).get("status")
                    if st == "success":
                        break
                    if st == "error":
                        return {"ok": False, "error": "snapshot failed", "action": act}
                    await asyncio.sleep(5)

        net = srv.get("public_net") or {}
        ipv4_id = ((net.get("ipv4") or {}).get("id"))
        ipv6_id = ((net.get("ipv6") or {}).get("id"))

        kept = {"ipv4": None, "ipv6": None}
        deleted_primary_ips = {"ipv4": None, "ipv6": None}

        # 勾选保留：先解绑并保留资源；未勾选：删除服务器后删除Primary IP资源
        if keep_ipv4 and ipv4_id:
            await self.client.unassign_primary_ip(int(ipv4_id))
            kept["ipv4"] = int(ipv4_id)
        if keep_ipv6 and ipv6_id:
            await self.client.unassign_primary_ip(int(ipv6_id))
            kept["ipv6"] = int(ipv6_id)

        await self.client.delete_server(server_id)

        if (not keep_ipv4) and ipv4_id:
            await self.client.delete_primary_ip(int(ipv4_id))
            deleted_primary_ips["ipv4"] = int(ipv4_id)
        if (not keep_ipv6) and ipv6_id:
            await self.client.delete_primary_ip(int(ipv6_id))
            deleted_primary_ips["ipv6"] = int(ipv6_id)

        await self.tg.send(
            f"🗑️ 服务器已删除\nID: {server_id}\n名称: {srv.get('name','-')}\n"
            f"快照: {'已创建' if create_snapshot else '未创建'}{f' ({snapshot_name})' if snapshot_name else ''}\n"
            f"保留IPv4: {'是' if bool(kept['ipv4']) else '否'}\n"
            f"保留IPv6: {'是' if bool(kept['ipv6']) else '否'}\n"
            f"已删除Primary IPv4: {'是' if bool(deleted_primary_ips['ipv4']) else '否'}\n"
            f"已删除Primary IPv6: {'是' if bool(deleted_primary_ips['ipv6']) else '否'}"
        )
        return {
            "ok": True,
            "deleted_server_id": server_id,
            "snapshot_created": bool(create_snapshot),
            "snapshot_name": snapshot_name,
            "kept_primary_ip_ids": kept,
            "deleted_primary_ip_ids": deleted_primary_ips,
        }

    async def delete_snapshot_manual(self, image_id: int):
        await self.client.delete_snapshot(image_id)
        await self.tg.send(f"🗑️ Snapshot deleted: {image_id}")
        return {"ok": True, "deleted": image_id}

    async def rename_snapshot_manual(self, image_id: int, description: str):
        data = await self.client.update_snapshot_description(image_id, description)
        await self.tg.send(f"✏️ Snapshot renamed: {image_id} -> {description}")
        return {"ok": True, "updated": image_id, "description": description, "raw": data}


    @staticmethod
    def _mini_bar(pct: float, width: int = 10):
        pct = max(0.0, min(100.0, pct))
        fill = int(round(pct / 100 * width))
        return "🟩" * fill + "⬜" * (width - fill)

    async def server_list_text(self):
        rows = await self.collect()
        if not rows:
            return "暂无服务器"
        lines = ["🖥️ 服务器列表"]
        for r in rows:
            pct = round((r.get('ratio', 0) or 0) * 100, 2)
            bar = self._mini_bar(pct)
            state = "🟢运行" if r.get('status') == 'running' else f"🟠{r.get('status')}"
            lines.append(
                f"\n<b>{r.get('name')}</b>  <code>{r.get('id')}</code>\n"
                f"{state} · 🌐 {r.get('ip','-')}\n"
                f"📤 {r.get('used_tb',0):.4f}/{r.get('limit_tb',20):.0f} TB ({pct}%)\n"
                f"{bar}"
            )
        return "\n".join(lines)

    async def traffic_text(self, server_id: int):
        rows = await self.collect()
        row = next((r for r in rows if r['id'] == server_id), None)
        if not row:
            return "未找到服务器"
        return f"{row['name']}\n本月已用: {row['used_tb']} TB / {row['limit_tb']} TB\n占比: {round(row['ratio']*100,2)}%"

    async def today_text(self, server_id: int):
        daily = await self.client.get_outbound_daily(server_id, days=2)
        if not daily:
            return "暂无今日流量数据"
        today = daily[-1]
        gb = today['bytes'] / 1024 / 1024 / 1024
        return f"服务器 {server_id} 今日出流量: {gb:.2f} GB"

    async def op_server(self, cmd: str, server_id: int, extra: str | None = None):
        if cmd == 'start':
            return await self.client.server_action(server_id, 'poweron')
        if cmd == 'stop':
            return await self.client.server_action(server_id, 'poweroff')
        if cmd == 'reboot':
            return await self.client.server_action(server_id, 'reboot')
        if cmd == 'rebuild':
            image = extra or 'debian-12'
            return await self.rebuild_with_snapshot_manual(server_id, image)
        if cmd == 'delete':
            return {'ok': await self.client.delete_server(server_id)}
        return {'ok': False, 'error': 'unsupported cmd'}

    async def get_action_status(self, action_id: int):
        return await self.client.get_action(action_id)

    async def hard_reboot(self, server_id: int):
        off = await self.client.server_action(server_id, 'poweroff')
        await asyncio.sleep(3)
        on = await self.client.server_action(server_id, 'poweron')
        return {
            "ok": True,
            "server_id": server_id,
            "poweroff_action": (off or {}).get("action", off),
            "poweron_action": (on or {}).get("action", on),
        }

    async def rebuild_with_snapshot_manual(self, server_id: int, image_id):
        # Rebuild by replacing server while keeping original Primary IP(s):
        # 1) unassign old server's primary IP(s) and wait action success
        # 2) delete old server
        # 3) create new server with same config + original primary IP(s)
        srv = await self.client.get_server(server_id)
        if not srv:
            return {"ok": False, "error": "server not found"}

        image = int(image_id) if str(image_id).isdigit() else str(image_id)

        name = srv.get("name", f"server-{server_id}")
        server_type = (srv.get("server_type") or {}).get("name")
        location = ((srv.get("datacenter") or {}).get("location") or {}).get("name")
        if not server_type or not location:
            return {"ok": False, "error": "missing source server type/location"}

        net = srv.get("public_net") or {}
        ipv4_id = ((net.get("ipv4") or {}).get("id"))
        ipv6_id = ((net.get("ipv6") or {}).get("id"))

        async def _wait_action_success(action_id: int, title: str):
            for _ in range(90):  # up to ~7.5 min
                act = await self.client.get_action(action_id)
                st = (act or {}).get("status")
                if st == "success":
                    return True
                if st == "error":
                    raise RuntimeError(f"{title} action failed: {act}")
                await asyncio.sleep(5)
            raise RuntimeError(f"{title} action timeout: {action_id}")

        try:
            # Hetzner 要求解绑 Primary IP 前服务器需处于关机状态
            pof = await self.client.server_action(server_id, 'poweroff')
            pof_id = ((pof or {}).get('action') or {}).get('id')
            if pof_id:
                await _wait_action_success(int(pof_id), f"poweroff server#{server_id}")

            if ipv4_id:
                r4 = await self.client.unassign_primary_ip(int(ipv4_id))
                a4 = ((r4 or {}).get("action") or {}).get("id")
                if a4:
                    await _wait_action_success(int(a4), f"unassign ipv4#{ipv4_id}")
            if ipv6_id:
                r6 = await self.client.unassign_primary_ip(int(ipv6_id))
                a6 = ((r6 or {}).get("action") or {}).get("id")
                if a6:
                    await _wait_action_success(int(a6), f"unassign ipv6#{ipv6_id}")

            await self.client.delete_server(server_id)

            # 412 常见于 Primary IP 资源状态尚未完成切换，做短暂重试
            created = None
            last_err = None
            for i in range(6):
                try:
                    created = await self.client.create_server(
                        name=name,
                        server_type=server_type,
                        location=location,
                        image=image,
                        primary_ip_id=int(ipv4_id) if ipv4_id else None,
                        primary_ipv6_id=int(ipv6_id) if ipv6_id else None,
                    )
                    break
                except httpx.HTTPStatusError as e:
                    last_err = e
                    if e.response is not None and e.response.status_code == 412:
                        await asyncio.sleep(3 + i * 2)
                        continue
                    raise
            if created is None and last_err is not None:
                raise last_err
        except Exception as e:
            detail = str(e)
            if isinstance(e, httpx.HTTPStatusError) and e.response is not None:
                try:
                    detail = f"HTTP {e.response.status_code}: {e.response.text}"
                except Exception:
                    detail = str(e)
            await self.tg.send(
                f"❌ 重建失败\n服务器ID: {server_id}\n镜像/快照: {image}\n错误: {detail[:900]}"
            )
            return {"ok": False, "server_id": server_id, "image_id": image, "error": detail}

        new_srv = created.get("server", {})
        await self.tg.send(
            f"♻️ 重建已完成（重置流量）\n"
            f"旧服务器ID: {server_id}\n"
            f"新服务器ID: {new_srv.get('id')}\n"
            f"IPv4: {new_srv.get('public_net',{}).get('ipv4',{}).get('ip','-')}\n"
            f"镜像/快照: {image}\n"
            f"说明: 已删除旧机，并使用原Primary IP创建同配置新机"
        )

        return {
            "ok": True,
            "old_server_id": server_id,
            "new_server": new_srv,
            "image_id": image,
            "kept_primary_ip_ids": {
                "ipv4": int(ipv4_id) if ipv4_id else None,
                "ipv6": int(ipv6_id) if ipv6_id else None,
            },
        }

    async def rebuild_full_manual(self, server_id: int, image_id):
        # Full rebuild: create a NEW server from selected image/snapshot then delete old one => new IP
        servers = await self.client.list_servers()
        src = next((s for s in servers if s["id"] == server_id), None)
        if not src:
            return {"ok": False, "error": "server not found"}

        image = int(image_id) if str(image_id).isdigit() else str(image_id)
        created = await self.client.create_server(
            name=src.get("name", f"server-{server_id}"),
            server_type=src.get("server_type", {}).get("name"),
            location=src.get("datacenter", {}).get("location", {}).get("name"),
            image=image,
        )
        await self.client.delete_server(server_id)
        new_srv = created.get("server", {})
        await self.tg.send(
            f"🧨 完全重建已完成（换IP）\n旧服务器ID: {server_id}\n新服务器ID: {new_srv.get('id')}\n新IP: {new_srv.get('public_net',{}).get('ipv4',{}).get('ip','-')}\n镜像/快照: {image}"
        )
        return {"ok": True, "old_server_id": server_id, "new_server": new_srv, "image_id": image}

    async def rename_server_manual(self, server_id: int, name: str):
        data = await self.client.rename_server(server_id, name)
        await self.tg.send(f"✏️ Server renamed: {server_id} -> {name}")
        return {"ok": True, "server_id": server_id, "name": name, "raw": data}

    async def qb_status(self):
        return await self.qb.stats()

    async def qb_realtime(self):
        nodes = self.qb_store.get_all()
        tasks = {}
        for sid, node in nodes.items():
            tasks[str(sid)] = asyncio.create_task(
                QBClient.fetch_stats(node.get("url", ""), node.get("username", ""), node.get("password", ""))
            )
        out = {}
        for sid, t in tasks.items():
            try:
                out[sid] = await t
            except Exception as e:
                out[sid] = {"enabled": True, "error": str(e)}
        return out

    def qb_nodes(self):
        return self.qb_store.get_all()

    async def qb_node_set(self, server_id: int, url: str, username: str, password: str):
        node = {"url": url, "username": username, "password": password}
        self.qb_store.set(server_id, node)
        # quick test
        try:
            st = await QBClient.fetch_stats(url, username, password)
            return {"ok": True, "server_id": server_id, "status": st}
        except Exception as e:
            return {"ok": False, "server_id": server_id, "error": str(e)}

    def qb_node_delete(self, server_id: int):
        self.qb_store.delete(server_id)
        return {"ok": True, "server_id": server_id}

    def auto_policies(self):
        return self.auto_policy.all()

    def auto_policy_set(self, server_id: int, enabled: bool, threshold: float, image_id=None):
        img = None
        if image_id is not None and str(image_id) != "":
            img = int(image_id) if str(image_id).isdigit() else str(image_id)
        p = {
            "enabled": bool(enabled),
            "threshold": float(threshold),
            "image_id": img,
        }
        self.auto_policy.set(server_id, p)
        return {"ok": True, "server_id": server_id, "policy": p}

    def auto_policy_delete(self, server_id: int):
        self.auto_policy.delete(server_id)
        return {"ok": True, "server_id": server_id}


monitor = MonitorService()
