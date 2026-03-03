import asyncio
import datetime as dt
from app.config import settings
from app.hetzner_client import HetznerClient
from app.telegram_bot import Tg

BYTES_IN_TB = 1024**4


class MonitorService:
    def __init__(self):
        self.client = HetznerClient(settings.hetzner_token)
        self.tg = Tg(settings.telegram_bot_token, settings.telegram_chat_id)
        self.last_snapshot = []

    async def meta(self):
        types = await self.client.list_server_types()
        locations = await self.client.list_locations()
        try:
            snapshots = await self.client.list_snapshots()
        except Exception:
            snapshots = []
        return {
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
            daily = await self.client.get_outbound_daily(s["id"], days=days)
            result.append({"id": s["id"], "name": s["name"], "daily": daily})
        return result

    async def collect(self):
        servers = await self.client.list_servers()
        rows = []
        for s in servers:
            outbound = await self.client.get_outbound_bytes_month(s["id"])
            used_tb = outbound / BYTES_IN_TB
            used_gb = outbound / (1024**3)
            pct = used_tb / settings.traffic_limit_tb
            daily = await self.client.get_outbound_daily(s["id"], days=2)
            today_gb = 0.0
            if daily:
                today_gb = (daily[-1].get("bytes", 0) / (1024**3))
            row = {
                "id": s["id"],
                "name": s["name"],
                "status": s["status"],
                "ip": s.get("public_net", {}).get("ipv4", {}).get("ip", ""),
                "server_type": s.get("server_type", {}).get("name", ""),
                "disk_gb": s.get("server_type", {}).get("disk", 0),
                "used_tb": round(used_tb, 3),
                "used_gb": round(used_gb, 2),
                "today_gb": round(today_gb, 2),
                "limit_tb": settings.traffic_limit_tb,
                "ratio": round(pct, 4),
                "over_threshold": pct >= settings.rotate_threshold,
            }
            rows.append(row)
        self.last_snapshot = rows
        return rows

    async def rotate_if_needed(self):
        rows = await self.collect()
        for row in rows:
            if row["over_threshold"]:
                if settings.safe_mode:
                    await self.tg.send(f"⚠️ SAFE_MODE 告警: {row['name']} 流量占比 {round(row['ratio']*100,2)}%，仅通知不执行删除/重建")
                    continue
                await self.rotate_server(row["id"])

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
        return {"ok": True, "message": "snapshot job started", "action": res.get("action", {})}

    async def create_server_manual(self, name: str, server_type: str, location: str, image):
        created = await self.client.create_server(name=name, server_type=server_type, location=location, image=image)
        await self.tg.send(f"🆕 New server created: {created.get('server', {}).get('name', name)}")
        return created

    async def delete_snapshot_manual(self, image_id: int):
        await self.client.delete_snapshot(image_id)
        await self.tg.send(f"🗑️ Snapshot deleted: {image_id}")
        return {"ok": True, "deleted": image_id}


    async def server_list_text(self):
        rows = await self.collect()
        if not rows:
            return "暂无服务器"
        return "\n".join([f"{r['id']} | {r['name']} | {r['status']} | {r['ip']} | {round(r['ratio']*100,2)}%" for r in rows])

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


monitor = MonitorService()
