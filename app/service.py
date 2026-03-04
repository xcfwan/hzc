import asyncio
import datetime as dt
from app.config import settings
from app.hetzner_client import HetznerClient
from app.telegram_bot import Tg
from app.qb_client import QBClient
from app.qb_store import QBStore
from app.auto_policy_store import AutoPolicyStore

# Keep same unit behavior as Hetzner panel (binary TiB, though UI labels TB)
BYTES_IN_TB = 1024**4


class MonitorService:
    def __init__(self):
        self.client = HetznerClient(settings.hetzner_token)
        self.tg = Tg(settings.telegram_bot_token, settings.telegram_chat_id)
        self.qb = QBClient(settings.qb_url, settings.qb_username, settings.qb_password)
        self.qb_store = QBStore(settings.qb_store_path)
        self.auto_policy = AutoPolicyStore(settings.auto_policy_path)
        self.last_snapshot = []

    async def meta(self):
        types = await self.client.list_server_types()
        locations = await self.client.list_locations()
        try:
            snapshots = await self.client.list_snapshots()
        except Exception:
            snapshots = []
        return {
            "app_version": settings.app_version,
            "server_types": [
                {
                    "name": t.get("name"),
                    "cores": t.get("cores"),
                    "memory": t.get("memory"),
                    "disk": t.get("disk"),
                    "prices": t.get("prices", []),
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

    async def collect(self):
        servers = await self.client.list_servers()
        rows = []

        qb_nodes = self.qb_store.get_all()
        policies = self.auto_policy.all()
        qb_tasks = {}
        for sid, node in qb_nodes.items():
            qb_tasks[str(sid)] = asyncio.create_task(QBClient.fetch_stats(node.get("url", ""), node.get("username", ""), node.get("password", "")))

        for s in servers:
            # Billing-consistent logic: Hetzner official traffic OUT (external upload only)
            outbound = int(s.get("outgoing_traffic") or 0)
            used_tb = outbound / BYTES_IN_TB
            used_gb = outbound / (1024**3)
            included_tb = (int(s.get("included_traffic") or 0) / BYTES_IN_TB) or settings.traffic_limit_tb
            pct = used_tb / included_tb if included_tb > 0 else 0
            try:
                daily = await self.client.get_outbound_daily(s["id"], days=2)
            except Exception:
                daily = []
            today_gb = 0.0
            if daily:
                today_gb = (daily[-1].get("bytes", 0) / (1024**3))
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
                "today_bytes": int(daily[-1].get("bytes", 0) if daily else 0),
                "limit_tb": round(included_tb, 8),
                "ratio": round(pct, 4),
                "over_threshold": pct >= float(pol.get("threshold", settings.rotate_threshold)),
                "qb": qbs,
                "auto_policy": pol,
            }
            rows.append(row)
        self.last_snapshot = rows
        return rows

    async def rotate_if_needed(self):
        rows = await self.collect()
        for row in rows:
            pol = row.get("auto_policy") or {}
            enabled = bool(pol.get("enabled", False))
            if not enabled:
                continue
            if row["over_threshold"]:
                if settings.safe_mode:
                    await self.tg.send(f"⚠️ SAFE_MODE 告警: {row['name']} 达到自动阈值 {pol.get('threshold', settings.rotate_threshold)}，仅通知不执行")
                    continue
                image_id = pol.get("image_id")
                if not image_id:
                    await self.tg.send(f"⚠️ {row['name']} 达到阈值，但未配置重建快照ID，已跳过")
                    continue
                await self.rebuild_with_snapshot_manual(row["id"], int(image_id))

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

    async def create_server_manual(self, name: str, server_type: str, location: str, image):
        created = await self.client.create_server(name=name, server_type=server_type, location=location, image=image)
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
            return await self.client.server_action(server_id, 'rebuild', {'image': image})
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

    async def rebuild_with_snapshot_manual(self, server_id: int, image_id: int):
        # Rebuild in-place: keep same server object (and public IP), do NOT auto-create new snapshot
        res = await self.client.server_action(server_id, 'rebuild', {'image': int(image_id)})
        await self.tg.send(f"♻️ 重建已提交\n服务器ID: {server_id}\n快照ID: {image_id}\n说明: 原地重建，保留原服务器IP")
        return {"ok": True, "server_id": server_id, "image_id": int(image_id), "action": res.get("action", res)}

    async def rename_server_manual(self, server_id: int, name: str):
        data = await self.client.rename_server(server_id, name)
        await self.tg.send(f"✏️ Server renamed: {server_id} -> {name}")
        return {"ok": True, "server_id": server_id, "name": name, "raw": data}

    async def qb_status(self):
        return await self.qb.stats()

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

    def auto_policy_set(self, server_id: int, enabled: bool, threshold: float, image_id: int | None = None):
        p = {
            "enabled": bool(enabled),
            "threshold": float(threshold),
            "image_id": int(image_id) if image_id else None,
        }
        self.auto_policy.set(server_id, p)
        return {"ok": True, "server_id": server_id, "policy": p}

    def auto_policy_delete(self, server_id: int):
        self.auto_policy.delete(server_id)
        return {"ok": True, "server_id": server_id}


monitor = MonitorService()
