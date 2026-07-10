"""Tiny disk cache for PubChem responses.

PubChem's chemical/safety data is static enough for hackathon-week purposes —
it won't meaningfully change between building and recording the demo — so an
indefinite on-disk cache does two things: speeds up repeated dev runs, and
means demo recording on Day 4 isn't hostage to live network conditions. Only
successful, well-formed responses are cached; 404s and PUG-View "Fault"
bodies are re-checked live each time (cheap, and the honest-omission logic
should always reflect current reality for those).
"""

import hashlib
import json
from pathlib import Path

CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache" / "pubchem"


def _key(url: str, params: dict | None) -> str:
    raw = url + "?" + json.dumps(params or {}, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def get(url: str, params: dict | None = None) -> dict | None:
    path = CACHE_DIR / f"{_key(url, params)}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def put(url: str, params: dict | None, value: dict) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"{_key(url, params)}.json"
    path.write_text(json.dumps(value), encoding="utf-8")
