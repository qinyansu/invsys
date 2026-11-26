"""按客户端 IP 的滑动窗口限流。"""

import threading
import time


class RateLimiter:
    def __init__(self, limit=240, window=60):
        self.limit = limit
        self.window = window
        self._hits = {}
        self._lock = threading.Lock()

    def allow(self, key: str):
        now = time.time()
        with self._lock:
            stamps = [t for t in self._hits.get(key, []) if now - t < self.window]
            if len(stamps) >= self.limit:
                retry = max(1, int(self.window - (now - stamps[0])))
                self._hits[key] = stamps
                return False, retry
            stamps.append(now)
            self._hits[key] = stamps
            return True, 0
