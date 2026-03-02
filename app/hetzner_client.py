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
        params = {"type": "snapshot", "bound_to": "null"}
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get(f"{BASE}/images", headers=self.headers, params=params)
            r.raise_for_status()
            return r.json().get("images", [])

    async def get_outbound_bytes_month(self, server_id: int) -> int:
        now = dt.datetime.utcnow()
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
        end = now.replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
        params = {
            "type": "network",
            "start": start,
            "end": end,
            "step": "3600",
        }
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get(f"{BASE}/servers/{server_id}/metrics", headers=self.headers, params=params)
            r.raise_for_status()
            data = r.json()

        series = data.get("metrics", {}).get("time_series", {}).get("network.0.tx", [])
        total = 0
        for point in series:
            if len(point) > 1 and point[1] is not None:
                total += int(float(point[1]))
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
