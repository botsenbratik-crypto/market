"""Simple in-memory TTL cache."""

from __future__ import annotations

import time
from typing import Generic, TypeVar

T = TypeVar("T")


class TtlCache(Generic[T]):
    def __init__(self, ttl_sec: float) -> None:
        self._ttl = ttl_sec
        self._data: dict[str, tuple[float, T]] = {}

    def get(self, key: str) -> T | None:
        row = self._data.get(key)
        if not row:
            return None
        ts, value = row
        if time.monotonic() - ts > self._ttl:
            return None
        return value

    def peek(self, key: str) -> T | None:
        row = self._data.get(key)
        return None if not row else row[1]

    def set(self, key: str, value: T) -> None:
        self._data[key] = (time.monotonic(), value)
