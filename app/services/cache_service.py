"""
Simple in-memory TTL cache for query results.

Keyed by: (prompt, connection_id, dialect)
TTL: configurable (default 30 minutes)

When the same user asks the same question on the same connection, we
return the cached result instantly instead of calling Gemini again.
"""

import hashlib
import json
import time
from threading import Lock
from typing import Any, Optional


class TTLCache:
    def __init__(self, ttl_seconds: int = 1800, max_size: int = 500):
        self.ttl = ttl_seconds
        self.max_size = max_size
        self._store: dict = {}
        self._lock = Lock()

    def _key(self, *args) -> str:
        s = json.dumps(args, sort_keys=True, default=str)
        return hashlib.sha256(s.encode()).hexdigest()

    def get(self, *args) -> Optional[Any]:
        k = self._key(*args)
        with self._lock:
            entry = self._store.get(k)
            if not entry:
                return None
            value, expires = entry
            if time.time() > expires:
                del self._store[k]
                return None
            return value

    def set(self, value: Any, *args):
        k = self._key(*args)
        with self._lock:
            if len(self._store) >= self.max_size:
                # Evict oldest 10%
                items = sorted(self._store.items(), key=lambda x: x[1][1])
                for old_key, _ in items[: self.max_size // 10]:
                    self._store.pop(old_key, None)
            self._store[k] = (value, time.time() + self.ttl)

    def invalidate_connection(self, connection_id: int):
        """Remove all cached entries for a connection (call after reindex)."""
        with self._lock:
            # We can't peek inside the hash, so just clear everything
            # — acceptable tradeoff for a personal project
            self._store.clear()

    def size(self) -> int:
        with self._lock:
            return len(self._store)

    def clear(self):
        with self._lock:
            self._store.clear()


query_cache = TTLCache(ttl_seconds=1800)  # 30 minutes