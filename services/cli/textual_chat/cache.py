from __future__ import annotations

import json
import os
import time
from pathlib import Path
from threading import RLock
from typing import Any


def _default_cache_root() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "LIARA" / "textual_chat"
    return Path.home() / ".liara" / "textual_chat"


def _deep_copy_json(value: Any) -> Any:
    return json.loads(json.dumps(value))


class ClientCache:
    def __init__(self, cache_root: str | Path | None = None, transcript_limit: int = 200):
        self.cache_root = Path(cache_root) if cache_root else _default_cache_root()
        self.cache_root.mkdir(parents=True, exist_ok=True)
        self.state_path = self.cache_root / "state.json"
        self.transcript_limit = max(1, transcript_limit)
        self._lock = RLock()
        self._data = self._load()

    def _empty_state(self) -> dict[str, Any]:
        return {
            "api": {},
            "transcripts": {},
            "settings": {},
            "stats": {"hits": 0, "misses": 0, "writes": 0},
        }

    def _load(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return self._empty_state()

        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return self._empty_state()

        data = self._empty_state()
        for key in data:
            if isinstance(raw.get(key), dict):
                data[key] = raw[key]
        return data

    def _save(self) -> None:
        tmp_path = self.state_path.with_suffix(".tmp")
        payload = json.dumps(self._data, ensure_ascii=True, indent=2, sort_keys=True)
        tmp_path.write_text(payload, encoding="utf-8")
        tmp_path.replace(self.state_path)

    def _bump_write(self) -> None:
        stats = self._data.setdefault("stats", {})
        stats["writes"] = int(stats.get("writes", 0)) + 1

    def get_cached(self, namespace: str, key: str) -> Any | None:
        with self._lock:
            bucket = self._data.setdefault("api", {}).setdefault(namespace, {})
            item = bucket.get(key)
            if not item:
                self._data["stats"]["misses"] = int(self._data["stats"].get("misses", 0)) + 1
                self._save()
                return None

            expires_at = float(item.get("expires_at", 0.0))
            if expires_at <= time.time():
                bucket.pop(key, None)
                self._data["stats"]["misses"] = int(self._data["stats"].get("misses", 0)) + 1
                self._bump_write()
                self._save()
                return None

            self._data["stats"]["hits"] = int(self._data["stats"].get("hits", 0)) + 1
            self._save()
            return _deep_copy_json(item.get("value"))

    def set_cached(self, namespace: str, key: str, value: Any, ttl_seconds: float) -> None:
        with self._lock:
            bucket = self._data.setdefault("api", {}).setdefault(namespace, {})
            bucket[key] = {
                "expires_at": time.time() + max(1.0, float(ttl_seconds)),
                "value": _deep_copy_json(value),
            }
            self._bump_write()
            self._save()

    def invalidate_prefix(self, namespace: str, prefix: str) -> None:
        with self._lock:
            bucket = self._data.setdefault("api", {}).setdefault(namespace, {})
            keys = [key for key in bucket if key.startswith(prefix)]
            if not keys:
                return
            for key in keys:
                bucket.pop(key, None)
            self._bump_write()
            self._save()

    def clear_api(self) -> None:
        with self._lock:
            self._data["api"] = {}
            self._bump_write()
            self._save()

    def append_transcript(self, session_id: str, role: str, text: str, kind: str = "chat") -> None:
        if not session_id or not text:
            return
        with self._lock:
            entries = self._data.setdefault("transcripts", {}).setdefault(session_id, [])
            entries.append(
                {
                    "role": role,
                    "text": text,
                    "kind": kind,
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                }
            )
            if len(entries) > self.transcript_limit:
                del entries[:-self.transcript_limit]
            self._bump_write()
            self._save()

    def get_transcript(self, session_id: str) -> list[dict[str, str]]:
        with self._lock:
            entries = self._data.setdefault("transcripts", {}).get(session_id, [])
            return _deep_copy_json(entries)

    def clear_transcript(self, session_id: str) -> None:
        with self._lock:
            if self._data.setdefault("transcripts", {}).pop(session_id, None) is None:
                return
            self._bump_write()
            self._save()

    def save_settings(self, settings: dict[str, Any]) -> None:
        with self._lock:
            self._data["settings"] = _deep_copy_json(settings)
            self._bump_write()
            self._save()

    def get_settings(self) -> dict[str, Any]:
        with self._lock:
            return _deep_copy_json(self._data.setdefault("settings", {}))

    def summary(self) -> dict[str, Any]:
        with self._lock:
            api = self._data.setdefault("api", {})
            active_entries = 0
            now = time.time()
            for bucket in api.values():
                if not isinstance(bucket, dict):
                    continue
                for item in bucket.values():
                    try:
                        if float(item.get("expires_at", 0.0)) > now:
                            active_entries += 1
                    except (TypeError, ValueError, AttributeError):
                        continue

            stats = self._data.setdefault("stats", {})
            return {
                "cache_root": str(self.cache_root),
                "state_path": str(self.state_path),
                "api_entries": active_entries,
                "transcript_sessions": len(self._data.setdefault("transcripts", {})),
                "hits": int(stats.get("hits", 0)),
                "misses": int(stats.get("misses", 0)),
                "writes": int(stats.get("writes", 0)),
            }