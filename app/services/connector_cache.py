# connector_cache is a no-op shim now.
# Persistent DuckDB files make an in-process cache unnecessary.

class _NoOp:
    def get(self, key):
        return None
    def set(self, key, value):
        pass
    def invalidate(self, key):
        pass
    def size(self):
        return 0
    def clear(self):
        pass

connector_cache = _NoOp()