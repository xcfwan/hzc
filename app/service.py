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
            "snapshots": [{"id": i.get("id"), "name": i.get("description") or i.get("name")} for i in snapshots],
        }

    async def collect(self):
        servers = await self.client.list_servers()
        rows = []
        for s in servers:
            outbound = await self.client.get_outbound_bytes_month(s["id"])
            used_tb = outbound / BYTES_IN_TB
            pct = used_tb / settings.traffic_limit_tb
            row = {
                "id": s["id"],
                "name": s["name"],
                "status": s["status"],
                "ip": s.get("public_net", {}).get("ipv4", {}).get("ip", ""),
                "server_type": s.get("server_type", {}).get("name", ""),
                "disk_gb": s.get("server_type", {}).get("disk", 0),
                "used_tb": round(used_tb, 3),
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
        est_monthly = round(disk_gb * settings.snapshot_price_per_gb, 4)
        return {
            "ok": True,
            "server_id": server_id,
            "server_name": src.get("name"),
            "disk_gb": disk_gb,
            "snapshot_price_per_gb": settings.snapshot_price_per_gb,
            "estimated_monthly_eur": est_monthly,
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


monitor = MonitorService()
