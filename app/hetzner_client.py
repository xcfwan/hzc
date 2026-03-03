import datetime as dt
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

    def _pick_outbound_series(self, data: dict):
        ts = data.get("metrics", {}).get("time_series", {})
        # New Hetzner metric names (rate): bandwidth.out (bytes/s)
        if "network.0.bandwidth.out" in ts:
            return ts.get("network.0.bandwidth.out", []), "bandwidth"
        # Backward compatibility
        if "network.0.tx" in ts:
            return ts.get("network.0.tx", []), "tx"
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
                v = float(point[1])
                total += int(v * step) if mode == "bandwidth" else int(v)
        return total

    async def get_outbound_daily(self, server_id: int, days: int = 7):
        now = dt.datetime.utcnow().replace(microsecond=0)
        step = 86400
        start = (now - dt.timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        end = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        params = {"type": "network", "start": start, "end": end, "step": str(step)}
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get(f"{BASE}/servers/{server_id}/metrics", headers=self.headers, params=params)
            r.raise_for_status()
            data = r.json()
        series, mode = self._pick_outbound_series(data)
        out = []
        for p in series:
            if len(p) > 1 and p[1] is not None:
                v = float(p[1])
                b = int(v * step) if mode == "bandwidth" else int(v)
                out.append({"date": str(p[0])[:10], "bytes": b})
        return out

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

    async def create_server(self, *, name: str, server_type: str, location: str, image):
        payload = {
            "name": name,
            "server_type": server_type,
            "location": location,
            "image": image,
        }
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

    async def rename_server(self, server_id: int, name: str):
        payload = {"name": name}
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.put(f"{BASE}/servers/{server_id}", headers=self.headers, json=payload)
            r.raise_for_status()
            return r.json()
