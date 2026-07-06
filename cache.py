import hashlib
import time

from config import RESP_CACHE_TTL, RESP_CACHE_MAX, SEARCH_CACHE_TTL, is_cacheable

class _TTLCache:
    def __init__(self, ttl, max_size=None):
        self._store: dict[str, tuple[str, float]] = {}
        self.ttl = ttl
        self.max_size = max_size

    def _key(self, text: str) -> str:
        return hashlib.md5(text.strip().lower().encode()).hexdigest()

    def get(self, text: str) -> str | None:
        k = self._key(text)
        if k in self._store:
            val, ts = self._store[k]
            if time.time() - ts < self.ttl:
                return val
            del self._store[k]
        return None

    def set(self, text: str, value: str):
        if self.max_size and len(self._store) >= self.max_size:
            oldest = min(self._store, key=lambda k: self._store[k][1])
            del self._store[oldest]
        self._store[self._key(text)] = (value, time.time())

    def clear(self):
        self._store.clear()

    def __len__(self):
        return len(self._store)


_resp_cache = _TTLCache(ttl=RESP_CACHE_TTL, max_size=RESP_CACHE_MAX)
_search_cache = _TTLCache(ttl=SEARCH_CACHE_TTL, max_size=None)


def resp_cache_get(msg: str) -> str | None:
    return _resp_cache.get(msg)


def resp_cache_set(msg: str, response: str):
    _resp_cache.set(msg, response)


def cache_get(query: str) -> str | None:
    return _search_cache.get(query)


def cache_set(query: str, result: str):
    _search_cache.set(query, result)
