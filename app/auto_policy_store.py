import json
import os
from threading import Lock


class AutoPolicyStore:
    def __init__(self, path: str):
        self.path = path
        self.lock = Lock()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if not os.path.exists(path):
            with open(path, "w", encoding="utf-8") as f:
                json.dump({}, f)

    def _read(self):
        with open(self.path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _write(self, data):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def all(self):
        with self.lock:
            return self._read()

    def set(self, server_id: int, policy: dict):
        with self.lock:
            data = self._read()
            data[str(server_id)] = policy
            self._write(data)
            return data[str(server_id)]

    def delete(self, server_id: int):
        with self.lock:
            data = self._read()
            data.pop(str(server_id), None)
            self._write(data)
