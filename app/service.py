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
        self._daily_cache = {}
        self._daily_cache_ts = 0.0
        self._daily_cache_ttl = 30.0
        self._today_cache = {}
        self._today_cache_ts = {}
        self._today_cache_ttl = 120.0
        self._fast_cache = None
        self._fast_cache_ts = 0.0
        self._fast_cache_ttl = 3.0

    def _rollover_state(self):
        rc = self.runtime.get()
        st = rc.get("traffic_rollover") or {}
        if not isinstance(st, dict):
            st = {}
        return st

    def _rollover_totals(self):
        st = self._rollover_state()
        today = dt.datetime.utcnow().date().isoformat()
        month = today[:7]
        if st.get("month") != month:
            st["month"] = month
            st["month_bytes"] = 0
        if st.get("day") != today:
            st["day"] = today
            st["day_bytes"] = 0
        return {
            "month_bytes": int(st.get("month_bytes") or 0),
            "day_bytes": int(st.get("day_bytes") or 0),
            "daily_history": st.get("daily_history") or {},
        }

    def _merge_rollover_daily_history(self, daily_points: list[dict], exclude_today: bool = True):
        st = self._rollover_state()
        hist = st.get("daily_history") or {}
        if not isinstance(hist, dict):
            hist = {}

        today = dt.datetime.utcnow().date().isoformat()
        for p in (daily_points or []):
            d = str(p.get("date") or "")
            if not d:
                continue
            if exclude_today and d == today:
                continue
            try:
                b = int(p.get("bytes") or 0)
            except Exception:
                b = 0
            if b <= 0:
                continue
            hist[d] = int(hist.get(d) or 0) + b

        for k in sorted(list(hist.keys()))[:-35]:
            hist.pop(k, None)
        st["daily_history"] = hist
        self.runtime.update({"traffic_rollover": st})

    def _add_rollover(self, month_bytes: int, day_bytes: int, note: str = ""):
        st = self._rollover_state()
        today = dt.datetime.utcnow().date().isoformat()
        month = today[:7]
        if st.get("month") != month:
            st["month"] = month
            st["month_bytes"] = 0
        if st.get("day") != today:
            st["day"] = today
            st["day_bytes"] = 0

        st["month_bytes"] = int(st.get("month_bytes") or 0) + int(month_bytes or 0)
        st["day_bytes"] = int(st.get("day_bytes") or 0) + int(day_bytes or 0)

        hist = st.get("daily_history") or {}
        if not isinstance(hist, dict):
            hist = {}
        hist[today] = int(hist.get(today) or 0) + int(day_bytes or 0)
        for k in sorted(list(hist.keys()))[:-35]:
            hist.pop(k, None)
        st["daily_history"] = hist

        if note:
            events = st.get("events") or []
            if not isinstance(events, list):
                events = []
            events.append({"ts": int(dt.datetime.utcnow().timestamp()), "note": note, "month_bytes": int(month_bytes or 0), "day_bytes": int(day_bytes or 0)})
            st["events"] = events[-60:]

        self.runtime.update({"traffic_rollover": st})

    async def meta(self):
        # 允许在未配置 HETZNER_TOKEN 时仍返回基础元信息（版本号等）
        try:
            types = await self.client.list_server_types()
        except Exception:
            types = []
        try:
            locations = await self.client.list_locations()
        except Exception:
            locations = []
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

        server_types = []
        for t in (types or []):
            if not isinstance(t, dict):
                continue
            prices = [p for p in (t.get("prices") or []) if isinstance(p, dict)]
            server_types.append({
                "name": t.get("name"),
                "cores": t.get("cores"),
                "memory": t.get("memory"),
                "disk": t.get("disk"),
                "prices": prices,
                # API可售性（非实时库存）：根据 Hetzner server_types.prices 是否包含该 location
                "sellable_locations": [p.get("location") for p in prices if p.get("location")],
            })

        primary_ipv4s = []
        primary_ipv6s = []
        for p in (pips or []):
            if not isinstance(p, dict):
                continue
            pid = p.get("id")
            try:
                pid_key = int(pid)
            except Exception:
                pid_key = None
            occupied_map4 = used_ipv4.get(pid_key) if pid_key is not None else None
            occupied_map6 = used_ipv6.get(pid_key) if pid_key is not None else None

            row = {
                "id": pid,
                "ip": p.get("ip"),
                "name": p.get("name") or f"ip-{pid}",
                "location": ((p.get("datacenter") or {}).get("location") or {}).get("name") or (p.get("location") or {}).get("name"),
                "datacenter": (p.get("datacenter") or {}).get("name"),
            }

            if p.get("type") == "ipv4":
                row["occupied"] = bool((p.get("assignee") or {}).get("id") or occupied_map4)
                row["occupied_by"] = occupied_map4 or {
                    "server_id": ((p.get("assignee") or {}).get("id")),
                    "server_name": ((p.get("assignee") or {}).get("name")) or "unknown",
                }
                primary_ipv4s.append(row)
            elif p.get("type") == "ipv6":
                row["occupied"] = bool((p.get("assignee") or {}).get("id") or occupied_map6)
                row["occupied_by"] = occupied_map6 or {
                    "server_id": ((p.get("assignee") or {}).get("id")),
                    "server_name": ((p.get("assignee") or {}).get("name")) or "unknown",
                }
                primary_ipv6s.append(row)

        return {
            "app_version": settings.app_version,
            "app_commit": settings.app_commit,
            "server_types": server_types,
            "locations": [{"name": l.get("name"), "city": l.get("city")} for l in (locations or []) if isinstance(l, dict)],
            "snapshots": [
                {
                    "id": i.get("id"),
                    "name": i.get("description") or i.get("name"),
                    "size_gb": round(float(i.get("image_size") or 0), 2),
                    "created": i.get("created"),
                }
                for i in (snapshots or [])
                if isinstance(i, dict)
            ],
            "primary_ipv4s": primary_ipv4s,
            "primary_ipv6s": primary_ipv6s,
        }

    async def daily_stats(self, days: int = 7):
        now_ts = dt.datetime.utcnow().timestamp()
        cache_key = f"days:{int(days)}"
        if (now_ts - self._daily_cache_ts) < self._daily_cache_ttl and cache_key in self._daily_cache:
            return self._daily_cache.get(cache_key, [])

        servers = await self.client.list_servers()
        sem = asyncio.Semaphore(8)

        async def _fetch_one(s):
            async with sem:
                try:
                    daily = await self.client.get_outbound_daily(s["id"], days=days)
                except Exception:
                    daily = []
                return {"id": s["id"], "name": s.get("name", f"server-{s['id']}"), "daily": daily}

        result = await asyncio.gather(*[_fetch_one(s) for s in servers]) if servers else []

        rt = self._rollover_totals()
        hist = rt.get("daily_history") or {}
        if isinstance(hist, dict) and hist:
            days_list = sorted(hist.keys())[-int(days):]
            archived_daily = [{"date": d, "bytes": int(hist.get(d) or 0)} for d in days_list]
            if any(x.get("bytes", 0) > 0 for x in archived_daily):
                result.append({"id": "archived", "name": "已删除/重建累计", "daily": archived_daily})

        self._daily_cache[cache_key] = result
        self._daily_cache_ts = now_ts
        return result

    async def dashboard_fast(self):
        now_ts = dt.datetime.utcnow().timestamp()
        if self._fast_cache and (now_ts - self._fast_cache_ts) < self._fast_cache_ttl:
            return self._fast_cache

        rows = await self.collect(use_cache=True)
        payload = {
            "rows": rows,
            "rollover": self._rollover_totals(),
            "app_version": settings.app_version,
            "app_commit": settings.app_commit,
            "ts": int(now_ts),
        }
        self._fast_cache = payload
        self._fast_cache_ts = now_ts
        return payload

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
        # + timeout/cache fallback to avoid occasional blank/zero flashes
        now_ts = dt.datetime.utcnow().timestamp()
        today_tasks = {}
        for s in servers:
            sid = int(s["id"])
            last_ts = float(self._today_cache_ts.get(sid) or 0)
            if (now_ts - last_ts) < self._today_cache_ttl and sid in self._today_cache:
                continue
            today_tasks[sid] = asyncio.create_task(self.client.get_outbound_today_bytes(sid, settings.timezone))

        for s in servers:
            # Billing-consistent logic: Hetzner official traffic OUT (external upload only)
            outbound = int(s.get("outgoing_traffic") or 0)
            used_tb = outbound / BYTES_IN_TB
            used_gb = outbound / (1024**3)
            included_tb = (int(s.get("included_traffic") or 0) / BYTES_IN_TB) or settings.traffic_limit_tb
            pct = used_tb / included_tb if included_tb > 0 else 0
            sid = int(s["id"])
            if sid in today_tasks:
                try:
                    today_bytes = await asyncio.wait_for(today_tasks[sid], timeout=12)
                    self._today_cache[sid] = int(today_bytes or 0)
                    self._today_cache_ts[sid] = now_ts
                except Exception:
                    today_bytes = int(self._today_cache.get(sid, 0) or 0)
            else:
                today_bytes = int(self._today_cache.get(sid, 0) or 0)
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
        now = int(dt.datetime.utcnow().timestamp())

        rc = self.runtime.get()
        guard_state = rc.get("traffic_guard_state") or {}
        if not isinstance(guard_state, dict):
            guard_state = {}

        # 对“启用自动重建”的机器，使用 metrics 月累计做一次实时校准，避免 list_servers 的月流量滞后
        realtime_used_tb = {}
        enabled_rows = [r for r in rows if bool((r.get("auto_policy") or {}).get("enabled", False))]
        if enabled_rows:
            sem = asyncio.Semaphore(5)

            async def _calc_used_tb(r):
                sid = int(r.get("id"))
                limit_tb = float(r.get("limit_tb", 20) or 20)
                try:
                    async with sem:
                        b = await asyncio.wait_for(self.client.get_outbound_bytes_month(sid), timeout=20)
                    return sid, (b / BYTES_IN_TB), limit_tb
                except Exception:
                    # 失败回退到 collect 的值
                    return sid, float(r.get("used_tb", 0) or 0), limit_tb

            vals = await asyncio.gather(*[_calc_used_tb(r) for r in enabled_rows])
            for sid, used_tb, _limit_tb in vals:
                realtime_used_tb[int(sid)] = {"used_tb": float(used_tb)}

        for row in rows:
            sid = str(row.get("id"))
            pol = row.get("auto_policy") or {}
            enabled = bool(pol.get("enabled", False))
            threshold = float(pol.get("threshold", settings.rotate_threshold))
            used_tb = float(row.get("used_tb", 0) or 0)
            limit_tb = float(row.get("limit_tb", 20) or 20)
            rt = realtime_used_tb.get(int(row.get("id") or 0))
            if rt:
                used_tb = float(rt.get("used_tb", used_tb))
            over = bool(limit_tb > 0 and (used_tb / limit_tb) >= threshold)

            st = guard_state.get(sid) or {}
            if not isinstance(st, dict):
                st = {}

            # 需求变更：未开启重建策略时，
            # - >=19TB: 每10分钟提醒一次
            # - >=20TB: 自动关机（只执行一次并告警）
            if not enabled:
                if used_tb >= limit_tb:
                    if not st.get("auto_stopped"):
                        try:
                            await self.client.server_action(int(row["id"]), "poweroff")
                            st["auto_stopped"] = True
                            st["last_stop_ts"] = now
                            await self.tg.send(
                                f"🛑 自动保护已执行: {row['name']} (ID:{row['id']})\n"
                                f"当前: {used_tb:.2f} TB / 限额: {limit_tb:.2f} TB\n"
                                f"动作: 已自动关机（因未开启重建策略且达到限额）"
                            )
                        except Exception as e:
                            await self.tg.send(
                                f"❌ 自动关机失败: {row['name']} (ID:{row['id']})\n"
                                f"错误: {str(e)[:500]}"
                            )
                    guard_state[sid] = st
                    continue

                if used_tb >= (limit_tb - 1):  # 20TB 限额下即 19TB
                    last_warn = int(st.get("last_warn_ts") or 0)
                    if now - last_warn >= 600:  # 10分钟节流
                        await self.tg.send(
                            f"⚠️ 流量预警: {row['name']} (ID:{row['id']})\n"
                            f"当前: {used_tb:.2f} TB / 限额: {limit_tb:.2f} TB\n"
                            f"说明: 未开启自动重建策略，达到限额将自动关机"
                        )
                        st["last_warn_ts"] = now
                    guard_state[sid] = st
                    continue

                # 低于19TB时清理状态，避免陈旧节流状态长期保留
                if st:
                    guard_state[sid] = {k: v for k, v in st.items() if k not in ("auto_stopped", "last_stop_ts")}
                continue

            # 有策略时沿用原策略逻辑
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

        self.runtime.update({"traffic_guard_state": guard_state})

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

    async def _fetch_live_type_quote(self, server_type: str, location: str):
        quote = {
            "checked": False,
            "server_type": server_type,
            "location": location,
            "sellable": None,
            "monthly_gross_eur": None,
            "currency": "EUR",
        }
        try:
            types = await self.client.list_server_types()
            quote["checked"] = True
        except Exception as e:
            quote["error"] = f"list_server_types failed: {str(e)[:240]}"
            return quote

        target = next((t for t in (types or []) if isinstance(t, dict) and t.get("name") == server_type), None)
        if not target:
            quote["sellable"] = False
            quote["error"] = "server_type not found from live api"
            return quote

        prices = [p for p in (target.get("prices") or []) if isinstance(p, dict)]
        sellable_locations = [p.get("location") for p in prices if p.get("location")]
        quote["sellable_locations"] = sellable_locations

        price_row = next((p for p in prices if p.get("location") == location), None)
        quote["sellable"] = bool(price_row)
        if price_row:
            pm = (price_row.get("price_monthly") or {})
            try:
                quote["monthly_gross_eur"] = float(pm.get("gross")) if pm.get("gross") is not None else None
            except Exception:
                quote["monthly_gross_eur"] = None
            if pm.get("currency"):
                quote["currency"] = str(pm.get("currency"))

        return quote

    async def create_server_manual(self, name: str, server_type: str, location: str, image, primary_ip_id: int | None = None, primary_ipv6_id: int | None = None):
        live_quote = await self._fetch_live_type_quote(server_type, location)
        if live_quote.get("checked") and live_quote.get("sellable") is False:
            return {
                "ok": False,
                "error": f"机型 {server_type} 在机房 {location} 当前 API 不可售，请刷新后重试或更换机型/机房",
                "live_quote": live_quote,
                "request": {
                    "name": name,
                    "server_type": server_type,
                    "location": location,
                    "image": image,
                    "primary_ip_id": primary_ip_id,
                    "primary_ipv6_id": primary_ipv6_id,
                },
            }

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
                "live_quote": live_quote,
                "request": {
                    "name": name,
                    "server_type": server_type,
                    "location": location,
                    "image": image,
                    "primary_ip_id": primary_ip_id,
                    "primary_ipv6_id": primary_ipv6_id,
                },
            }

        created["live_quote"] = live_quote
        srv = created.get("server", {})
        sid = srv.get("id")
        sname = srv.get("name", name)
        fee = live_quote.get("monthly_gross_eur")
        fee_txt = f"\n参考月费: €{fee:.2f}" if isinstance(fee, (int, float)) else ""
        await self.tg.send(f"🆕 New server created: {sname} (ID: {sid}){fee_txt}")

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

        async def _wait_action_success(action_id: int, title: str):
            for _ in range(90):
                act = await self.client.get_action(action_id)
                st = (act or {}).get("status")
                if st == "success":
                    return True
                if st == "error":
                    raise RuntimeError(f"{title} failed: {act}")
                await asyncio.sleep(4)
            raise RuntimeError(f"{title} timeout: {action_id}")

        snapshot_name = None
        if create_snapshot:
            snapshot_name = f"before-delete-{srv.get('name','server')}-{dt.datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
            snap_res = await self.client.create_snapshot(server_id, snapshot_name)
            action_id = ((snap_res or {}).get("action") or {}).get("id")
            if action_id:
                await _wait_action_success(int(action_id), f"snapshot server#{server_id}")

        net = srv.get("public_net") or {}
        ipv4_id = ((net.get("ipv4") or {}).get("id"))
        ipv6_id = ((net.get("ipv6") or {}).get("id"))
        month_bytes_snapshot = int(srv.get("outgoing_traffic") or 0)
        try:
            day_bytes_snapshot = int(await self.client.get_outbound_today_bytes(server_id, settings.timezone))
        except Exception:
            day_bytes_snapshot = 0
        try:
            daily_points_snapshot = await self.client.get_outbound_daily(server_id, days=30)
        except Exception:
            daily_points_snapshot = []
        try:
            daily_points_snapshot = await self.client.get_outbound_daily(server_id, days=30)
        except Exception:
            daily_points_snapshot = []

        kept = {"ipv4": None, "ipv6": None}
        deleted_primary_ips = {"ipv4": None, "ipv6": None}
        warnings = []

        try:
            # 需要保留IP时，先禁止级联删除，避免删机把IP一起删掉
            if keep_ipv4 and ipv4_id:
                await self.client.update_primary_ip_auto_delete(int(ipv4_id), False)
            if keep_ipv6 and ipv6_id:
                await self.client.update_primary_ip_auto_delete(int(ipv6_id), False)

            if (keep_ipv4 or keep_ipv6) and keep_mode == "safe":
                # safe模式：先关机，再解绑，规避422
                pof = await self.client.server_action(server_id, "poweroff")
                pof_id = ((pof or {}).get("action") or {}).get("id")
                if pof_id:
                    await _wait_action_success(int(pof_id), f"poweroff server#{server_id}")

                if keep_ipv4 and ipv4_id:
                    r4 = await self.client.unassign_primary_ip(int(ipv4_id))
                    a4 = ((r4 or {}).get("action") or {}).get("id")
                    if a4:
                        await _wait_action_success(int(a4), f"unassign ipv4#{ipv4_id}")
                    kept["ipv4"] = int(ipv4_id)
                if keep_ipv6 and ipv6_id:
                    r6 = await self.client.unassign_primary_ip(int(ipv6_id))
                    a6 = ((r6 or {}).get("action") or {}).get("id")
                    if a6:
                        await _wait_action_success(int(a6), f"unassign ipv6#{ipv6_id}")
                    kept["ipv6"] = int(ipv6_id)
            else:
                if keep_ipv4 and ipv4_id:
                    kept["ipv4"] = int(ipv4_id)
                if keep_ipv6 and ipv6_id:
                    kept["ipv6"] = int(ipv6_id)

            await self.client.delete_server(server_id)

            # 未勾选保留IP时删除Primary IP（删除失败不阻断“服务器删除成功”）
            async def _delete_primary_with_retry(pid: int, label: str):
                last = None
                for i in range(4):
                    try:
                        await self.client.delete_primary_ip(int(pid))
                        return True
                    except httpx.HTTPStatusError as e:
                        last = e
                        code = e.response.status_code if e.response is not None else None
                        # 404: 已删除；412/422: 资源状态切换中，稍后重试
                        if code == 404:
                            return True
                        if code in (412, 422):
                            await asyncio.sleep(2 + i * 2)
                            continue
                        raise
                if last is not None:
                    try:
                        body = last.response.text if last.response is not None else str(last)
                    except Exception:
                        body = str(last)
                    warnings.append(f"{label} delete failed: {body[:200]}")
                return False

            if (not keep_ipv4) and ipv4_id:
                ok4 = await _delete_primary_with_retry(int(ipv4_id), f"ipv4#{ipv4_id}")
                if ok4:
                    deleted_primary_ips["ipv4"] = int(ipv4_id)
            if (not keep_ipv6) and ipv6_id:
                ok6 = await _delete_primary_with_retry(int(ipv6_id), f"ipv6#{ipv6_id}")
                if ok6:
                    deleted_primary_ips["ipv6"] = int(ipv6_id)

            # 删除成功后，把该机器当月/当日流量沉淀到历史累计，避免重建/删除后清零统计
            self._add_rollover(
                month_bytes=month_bytes_snapshot,
                day_bytes=day_bytes_snapshot,
                note=f"delete server#{server_id}",
            )
            self._merge_rollover_daily_history(daily_points_snapshot, exclude_today=True)
        except Exception as e:
            detail = str(e)
            if isinstance(e, httpx.HTTPStatusError) and e.response is not None:
                try:
                    detail = f"HTTP {e.response.status_code}: {e.response.text}"
                except Exception:
                    detail = str(e)
            return {"ok": False, "error": detail}

        msg = (
            f"🗑️ 服务器已删除\nID: {server_id}\n名称: {srv.get('name','-')}\n"
            f"快照: {'已创建' if create_snapshot else '未创建'}{f' ({snapshot_name})' if snapshot_name else ''}\n"
            f"模式: {keep_mode}\n"
            f"保留IPv4: {'是' if bool(kept['ipv4']) else '否'}\n"
            f"保留IPv6: {'是' if bool(kept['ipv6']) else '否'}\n"
            f"已删除Primary IPv4: {'是' if bool(deleted_primary_ips['ipv4']) else '否'}\n"
            f"已删除Primary IPv6: {'是' if bool(deleted_primary_ips['ipv6']) else '否'}"
        )
        if warnings:
            msg += "\n⚠️ 警告: " + " | ".join(warnings)
        await self.tg.send(msg)
        return {
            "ok": True,
            "deleted_server_id": server_id,
            "snapshot_created": bool(create_snapshot),
            "snapshot_name": snapshot_name,
            "keep_mode": keep_mode,
            "kept_primary_ip_ids": kept,
            "deleted_primary_ip_ids": deleted_primary_ips,
            "warnings": warnings,
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

    def _migrate_policy_and_qb(self, old_server_id: int, new_server_id: int):
        # Carry over auto rebuild policy + qB node config when server ID changes after rebuild
        migrated_policy = False
        qb_node = None

        try:
            policies = self.auto_policy.all() or {}
            old_key = str(old_server_id)
            if old_key in policies:
                self.auto_policy.set(new_server_id, policies.get(old_key) or {})
                self.auto_policy.delete(old_server_id)
                migrated_policy = True
        except Exception:
            pass

        try:
            nodes = self.qb_store.get_all() or {}
            old_key = str(old_server_id)
            if old_key in nodes:
                qb_node = nodes.get(old_key) or {}
                self.qb_store.set(new_server_id, qb_node)
                self.qb_store.delete(old_server_id)
        except Exception:
            pass

        return {"migrated_policy": migrated_policy, "qb_node": qb_node}

   async def _post_rebuild_qb_check(self, old_server_id: int, new_server_id: int, node: dict | None):
    if not node:
        return

    url = (node.get("url") or "").strip()
    username = node.get("username", "")
    password = node.get("password", "")

    if not (url and username and password):
        await self.tg.send(
            f"⚠️ 重建后 qB 配置不完整，无法自动重连\n"
            f"旧ID: {old_server_id}\n"
            f"新ID: {new_server_id}"
        )
        return

    # 先等新服务器进入 running
    running = False
    for _ in range(48):  # 最多等约 8 分钟
        try:
            srv = await self.client.get_server(new_server_id)
            if (srv or {}).get("status") == "running":
                running = True
                break
        except Exception:
            pass
        await asyncio.sleep(10)

    if not running:
        await self.tg.send(
            f"⚠️ 重建后 qB 自动重连未启动：新服务器长时间未到 running\n"
            f"旧ID: {old_server_id}\n"
            f"新ID: {new_server_id}"
        )
        return

    # running 后给 qB 一点启动时间
    await asyncio.sleep(25)

    attempt = 0
    last_error = ""

    while True:
        attempt += 1
        try:
            stats = await QBClient.fetch_stats(url, username, password)
            ok = bool((stats or {}).get("enabled")) and not (stats or {}).get("error")

            if ok:
                await self.tg.send(
                    f"✅ 重建后 qB 已自动重连成功\n"
                    f"旧ID: {old_server_id}\n"
                    f"新ID: {new_server_id}\n"
                    f"URL: {url}\n"
                    f"检查次数: {attempt}"
                )
                return

            last_error = str((stats or {}).get("error") or "qB not ready")

        except Exception as e:
            last_error = str(e)[:300]

        # 第一次失败提醒一次，之后每 12 次（约 1 小时）提醒一次，避免 TG 被刷屏
        if attempt == 1 or attempt % 12 == 0:
            await self.tg.send(
                f"⚠️ 重建后 qB 尚未连通，5 分钟后继续重试\n"
                f"旧ID: {old_server_id}\n"
                f"新ID: {new_server_id}\n"
                f"URL: {url}\n"
                f"第 {attempt} 次检查失败\n"
                f"错误: {last_error}"
            )

        await asyncio.sleep(300)  # 5 分钟

    async def rebuild_with_snapshot_manual(self, server_id: int, image_id):
        # Fast-first rebuild strategy:
        # 1) direct delete old server (expect Primary IP to become unassigned)
        # 2) create new server with same config + original Primary IP(s)
        # 3) fallback to safe flow (poweroff+unassign) only if needed
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
        month_bytes_snapshot = int(srv.get("outgoing_traffic") or 0)
        try:
            day_bytes_snapshot = int(await self.client.get_outbound_today_bytes(server_id, settings.timezone))
        except Exception:
            day_bytes_snapshot = 0

        async def _wait_action_success(action_id: int, title: str):
            for _ in range(90):
                act = await self.client.get_action(action_id)
                st = (act or {}).get("status")
                if st == "success":
                    return True
                if st == "error":
                    raise RuntimeError(f"{title} action failed: {act}")
                await asyncio.sleep(5)
            raise RuntimeError(f"{title} action timeout: {action_id}")

        async def _create_with_retry():
            created = None
            last_err = None
            for i in range(8):
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
                    if e.response is not None and e.response.status_code in (412, 422):
                        await asyncio.sleep(3 + i * 2)
                        continue
                    raise
            if created is None and last_err is not None:
                raise last_err
            return created

        try:
            # 防止删机时 Primary IP 被级联删除（Hetzner auto_delete=true 场景）
            if ipv4_id:
                await self.client.update_primary_ip_auto_delete(int(ipv4_id), False)
            if ipv6_id:
                await self.client.update_primary_ip_auto_delete(int(ipv6_id), False)

            # FAST path: direct delete first
            await self.client.delete_server(server_id)
            self._add_rollover(month_bytes_snapshot, day_bytes_snapshot, note=f"rebuild old server#{server_id}")
            self._merge_rollover_daily_history(daily_points_snapshot, exclude_today=True)
            created = await _create_with_retry()
            path = "fast"
        except Exception:
            # SAFE fallback (best-effort): if old server still exists, poweroff+unassign then create
            try:
                still = await self.client.get_server(server_id)
            except Exception:
                still = None
            if still:
                try:
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
                    self._add_rollover(month_bytes_snapshot, day_bytes_snapshot, note=f"rebuild old server#{server_id}")
                    self._merge_rollover_daily_history(daily_points_snapshot, exclude_today=True)
                except Exception:
                    pass
            try:
                created = await _create_with_retry()
                path = "safe-fallback"
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
        new_id = new_srv.get("id")

        carried_qb = None
        migrated_policy = False
        if new_id:
            mig = self._migrate_policy_and_qb(server_id, int(new_id))
            migrated_policy = bool((mig or {}).get("migrated_policy"))
            carried_qb = (mig or {}).get("qb_node")
            if carried_qb:
                asyncio.create_task(self._post_rebuild_qb_check(server_id, int(new_id), carried_qb))

        await self.tg.send(
            f"♻️ 重建已完成（重置流量）\n"
            f"旧服务器ID: {server_id}\n"
            f"新服务器ID: {new_srv.get('id')}\n"
            f"IPv4: {new_srv.get('public_net',{}).get('ipv4',{}).get('ip','-')}\n"
            f"镜像/快照: {image}\n"
            f"路径: {path}\n"
            f"策略迁移: {'已延用' if migrated_policy else '无/未延用'}\n"
            f"qB迁移: {'已延用' if carried_qb else '无/未延用'}\n"
            f"说明: 已删除旧机，并使用原Primary IP创建同配置新机"
        )

        return {
            "ok": True,
            "old_server_id": server_id,
            "new_server": new_srv,
            "image_id": image,
            "path": path,
            "migrated_auto_policy": migrated_policy,
            "migrated_qb": bool(carried_qb),
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
        month_bytes_snapshot = int(src.get("outgoing_traffic") or 0)
        try:
            day_bytes_snapshot = int(await self.client.get_outbound_today_bytes(server_id, settings.timezone))
        except Exception:
            day_bytes_snapshot = 0
        try:
            daily_points_snapshot = await self.client.get_outbound_daily(server_id, days=30)
        except Exception:
            daily_points_snapshot = []
        created = await self.client.create_server(
            name=src.get("name", f"server-{server_id}"),
            server_type=src.get("server_type", {}).get("name"),
            location=src.get("datacenter", {}).get("location", {}).get("name"),
            image=image,
        )
        await self.client.delete_server(server_id)
        self._add_rollover(month_bytes_snapshot, day_bytes_snapshot, note=f"full-rebuild old server#{server_id}")
        self._merge_rollover_daily_history(daily_points_snapshot, exclude_today=True)
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
