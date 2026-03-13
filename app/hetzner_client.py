import datetime as dt
from zoneinfo import ZoneInfo
import httpx

BASE = "https://api.hetzner.cloud/v1"


class HetznerClient:
    def __init__(self, token: str):
        self.headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    async def list_servers(self):
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get(f"{BASE}/servers", headers=self.headers)
            r.raise_for_status()
            return r.json().get("servers", [])

    async def list_server_types(self):
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get(f"{BASE}/server_types", headers=self.headers)
            r.raise_for_status()
            return r.json().get("server_types", [])

    async def list_locations(self):
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get(f"{BASE}/locations", headers=self.headers)
            r.raise_for_status()
            return r.json().get("locations", [])

    async def list_snapshots(self):
        params = {"type": "snapshot"}
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get(f"{BASE}/images", headers=self.headers, params=params)
            r.raise_for_status()
            return r.json().get("images", [])

    async def list_primary_ips(self):
        out = []
        page = 1
        while True:
            async with httpx.AsyncClient(timeout=30) as c:
                r = await c.get(f"{BASE}/primary_ips", headers=self.headers, params={"per_page": 50, "page": page})
                r.raise_for_status()
                data = r.json()
                items = data.get("primary_ips", [])
                out.extend(items)
                if not items or len(items) < 50:
                    break
                page += 1
        return out

    @staticmethod
    def _normalize_series(raw):
        # New format: {"values": [[ts,val], ...]}
        if isinstance(raw, dict):
            vals = raw.get("values", [])
            return vals if isinstance(vals, list) else []
        # Old format: [[ts,val], ...]
        if isinstance(raw, list):
            return raw
        return []

    @staticmethod
    def _point_date(ts):
        # ts may be unix epoch seconds (int/str) or iso string
        if isinstance(ts, (int, float)):
            return dt.datetime.utcfromtimestamp(int(ts)).strftime("%Y-%m-%d")
        s = str(ts)
        if s.isdigit():
            return dt.datetime.utcfromtimestamp(int(s)).strftime("%Y-%m-%d")
        return s[:10]

    def _pick_outbound_series(self, data: dict):
        ts = data.get("metrics", {}).get("time_series", {})
        # New Hetzner metric names (rate): bandwidth.out (bytes/s)
        if "network.0.bandwidth.out" in ts:
            return self._normalize_series(ts.get("network.0.bandwidth.out", [])), "bandwidth"
        # Backward compatibility
        if "network.0.tx" in ts:
            return self._normalize_series(ts.get("network.0.tx", [])), "tx"
        return [], "unknown"

    async def get_outbound_bytes_month(self, server_id: int) -> int:
        now = dt.datetime.utcnow()
        step = 3600
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
        end = now.replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
        params = {"type": "network", "start": start, "end": end, "step": str(step)}
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get(f"{BASE}/servers/{server_id}/metrics", headers=self.headers, params=params)
            r.raise_for_status()
            data = r.json()

        series, mode = self._pick_outbound_series(data)
        total = 0
        for point in series:
            if len(point) > 1 and point[1] is not None:
                try:
                    v = float(point[1])
                except (TypeError, ValueError):
                    continue
                total += int(v * step) if mode == "bandwidth" else int(v)
        return total

    async def get_outbound_daily(self, server_id: int, days: int = 7):
        # Use hourly step then aggregate by UTC date for trend chart
        now = dt.datetime.utcnow().replace(microsecond=0)
        step = 3600
        start = (now - dt.timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        end = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        params = {"type": "network", "start": start, "end": end, "step": str(step)}
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get(f"{BASE}/servers/{server_id}/metrics", headers=self.headers, params=params)
            r.raise_for_status()
            data = r.json()
        series, mode = self._pick_outbound_series(data)
        agg = {}
        for p in series:
            if len(p) > 1 and p[1] is not None:
                try:
                    v = float(p[1])
                except (TypeError, ValueError):
                    continue
                b = int(v * step) if mode == "bandwidth" else int(v)
                d = self._point_date(p[0])
                agg[d] = agg.get(d, 0) + b
        return [{"date": d, "bytes": agg[d]} for d in sorted(agg.keys())]

    async def get_outbound_today_bytes(self, server_id: int, timezone: str = "UTC") -> int:
        # Today 00:00 -> now (in configured timezone)
        tz = ZoneInfo(timezone or "UTC")
        now_local = dt.datetime.now(tz)
        start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        start_utc = start_local.astimezone(dt.timezone.utc)
        end_utc = now_local.astimezone(dt.timezone.utc)

        step = 300  # 5 min for better precision
        params = {
            "type": "network",
            "start": start_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end": end_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "step": str(step),
        }
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get(f"{BASE}/servers/{server_id}/metrics", headers=self.headers, params=params)
            r.raise_for_status()
            data = r.json()

        series, mode = self._pick_outbound_series(data)
        total = 0
        for p in series:
            if len(p) > 1 and p[1] is not None:
                try:
                    v = float(p[1])
                except (TypeError, ValueError):
                    continue
                total += int(v * step) if mode == "bandwidth" else int(v)
        return total

    async def create_snapshot(self, server_id: int, description: str):
        payload = {"type": "snapshot", "description": description}
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(f"{BASE}/servers/{server_id}/actions/create_image", headers=self.headers, json=payload)
            r.raise_for_status()
            return r.json()

    async def get_action(self, action_id: int):
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get(f"{BASE}/actions/{action_id}", headers=self.headers)
            r.raise_for_status()
            return r.json().get("action", {})

    async def create_server_from_image(self, src_server: dict, image_id: int):
        payload = {
            "name": f"{src_server['name']}-rotated",
            "server_type": src_server["server_type"]["name"],
            "image": image_id,
            "location": src_server.get("datacenter", {}).get("location", {}).get("name"),
            "labels": {**(src_server.get("labels") or {}), "rotated_from": str(src_server["id"])},
        }
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(f"{BASE}/servers", headers=self.headers, json=payload)
            r.raise_for_status()
            return r.json()

    async def create_server(self, *, name: str, server_type: str, location: str | None, image, primary_ip_id: int | None = None, primary_ipv6_id: int | None = None):
        payload = {
            "name": name,
            "server_type": server_type,
            "image": image,
        }
        if location:
            payload["location"] = location
        if primary_ip_id is not None or primary_ipv6_id is not None:
            public_net = {"enable_ipv4": True, "enable_ipv6": True}
            if primary_ip_id is not None:
                public_net["ipv4"] = int(primary_ip_id)
            if primary_ipv6_id is not None:
                public_net["ipv6"] = int(primary_ipv6_id)
            payload["public_net"] = public_net
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(f"{BASE}/servers", headers=self.headers, json=payload)
            r.raise_for_status()
            return r.json()

    async def delete_server(self, server_id: int):
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.delete(f"{BASE}/servers/{server_id}", headers=self.headers)
            r.raise_for_status()
            return True

    async def delete_snapshot(self, image_id: int):
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.delete(f"{BASE}/images/{image_id}", headers=self.headers)
            r.raise_for_status()
            return True

    async def delete_primary_ip(self, primary_ip_id: int):
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.delete(f"{BASE}/primary_ips/{primary_ip_id}", headers=self.headers)
            r.raise_for_status()
            return True

    async def update_snapshot_description(self, image_id: int, description: str):
        payload = {"description": description}
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.put(f"{BASE}/images/{image_id}", headers=self.headers, json=payload)
            r.raise_for_status()
            return r.json()

    async def server_action(self, server_id: int, action: str, payload: dict | None = None):
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(f"{BASE}/servers/{server_id}/actions/{action}", headers=self.headers, json=payload or {})
            r.raise_for_status()
            return r.json()

    async def get_server(self, server_id: int):
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get(f"{BASE}/servers/{server_id}", headers=self.headers)
            r.raise_for_status()
            return r.json().get("server", {})

    async def unassign_primary_ip(self, primary_ip_id: int):
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(f"{BASE}/primary_ips/{primary_ip_id}/actions/unassign", headers=self.headers, json={})
            r.raise_for_status()
            return r.json()

    async def update_primary_ip_auto_delete(self, primary_ip_id: int, auto_delete: bool):
        payload = {"auto_delete": bool(auto_delete)}
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.put(f"{BASE}/primary_ips/{primary_ip_id}", headers=self.headers, json=payload)
            r.raise_for_status()
            return r.json()

    async def rename_server(self, server_id: int, name: str):
        payload = {"name": name}
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.put(f"{BASE}/servers/{server_id}", headers=self.headers, json=payload)
            r.raise_for_status()
            return r.json()
