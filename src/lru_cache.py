# lru_cache.py
"""
厨师本地知识库使用的LRU缓存。
"""

from collections import OrderedDict
from typing import Optional

class LRUCache:
    """线程不安全的LRU缓存，需在调用方自行加锁（若需要）。"""
    def __init__(self, capacity: int):
        self.cache = OrderedDict()
        self.capacity = capacity

    def get(self, key: str) -> Optional[str]:
        if key not in self.cache:
            return None
        self.cache.move_to_end(key)
        return self.cache[key]

    def put(self, key: str, value: str) -> None:
        if key in self.cache:
            self.cache.move_to_end(key)
        self.cache[key] = value
        if len(self.cache) > self.capacity:
            self.cache.popitem(last=False)

    def __len__(self) -> int:
        return len(self.cache)

    def items(self):
        return self.cache.items()