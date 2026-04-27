import json
import os
from typing import Any


DEFAULT_CACHE_PATH = os.environ.get("CATEGORY_CACHE_FILE", "category_cache.json")


def normalize_receiver(receiver: str) -> str:
    return " ".join((receiver or "").strip().lower().split())


def load_category_cache(cache_path: str = DEFAULT_CACHE_PATH) -> dict[str, str]:
    try:
        if not os.path.exists(cache_path):
            return {}
        with open(cache_path, "r", encoding="utf-8") as f:
            data: Any = json.load(f)
        if not isinstance(data, dict):
            return {}
        out: dict[str, str] = {}
        for k, v in data.items():
            if not isinstance(k, str) or not isinstance(v, str):
                continue
            key = normalize_receiver(k)
            val = v.strip()
            if key and val:
                out[key] = val
        return out
    except Exception:
        return {}


def save_category_cache(cache: dict[str, str], cache_path: str = DEFAULT_CACHE_PATH) -> None:
    normalized: dict[str, str] = {}
    for k, v in (cache or {}).items():
        key = normalize_receiver(k)
        val = (v or "").strip()
        if key and val:
            normalized[key] = val

    tmp_path = f"{cache_path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(dict(sorted(normalized.items())), f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, cache_path)

