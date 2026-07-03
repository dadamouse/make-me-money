"""待選擇狀態：候選清單等待使用者以數字回覆（in-memory，附 TTL）。"""
import time


class PendingChoices:
    def __init__(self, ttl_seconds: float = 300, clock=time.monotonic):
        self._ttl = ttl_seconds
        self._clock = clock
        self._items: dict[str, tuple[float, dict]] = {}

    def put(self, key: str, value: dict) -> None:
        self._items[key] = (self._clock(), value)

    def pop(self, key: str) -> dict | None:
        item = self._items.pop(key, None)
        if not item:
            return None
        created_at, value = item
        if self._clock() - created_at > self._ttl:
            return None
        return value
