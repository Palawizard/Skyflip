from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CacheEntry:
    payload: Any
    created_at: float
    source: str


class FileCache:
    def __init__(self, cache_dir: Path | str = ".cache/skyflip", ttl_seconds: int = 300) -> None:
        self.cache_dir = Path(cache_dir)
        self.ttl_seconds = ttl_seconds
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def get(self, key: str) -> CacheEntry | None:
        path = self._path_for(key)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        created_at = float(data.get("created_at", 0))
        if self.ttl_seconds > 0 and time.time() - created_at > self.ttl_seconds:
            return None
        return CacheEntry(payload=data.get("payload"), created_at=created_at, source="cache")

    def set(self, key: str, payload: Any) -> None:
        path = self._path_for(key)
        tmp = path.with_suffix(".tmp")
        data = {"created_at": time.time(), "payload": payload}
        tmp.write_text(json.dumps(data, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)

    def _path_for(self, key: str) -> Path:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return self.cache_dir / f"{digest}.json"
