# common.py
import hashlib
import hmac
import json
from dataclasses import dataclass, asdict
from typing import Optional, Any, Dict

PROTOCOL_VERSION = "csa-lite-0.2"

@dataclass
class Challenge:
    v: str
    nonce: str
    seed: float
    steps: int
    budget_ms: int
    ttl_ms: int = 750  # freshness window for demo
    issued_at: Optional[float] = None  # unix seconds

def sha256_hex(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()

def hmac_sha256_hex(key: bytes, msg: bytes) -> str:
    return hmac.new(key, msg, hashlib.sha256).hexdigest()

def canonical_json(obj: Any) -> bytes:
    # Stable serialization for hashes / HMAC
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()

def challenge_bytes(ch: Challenge) -> bytes:
    return canonical_json(asdict(ch))

def load_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def save_json(path: str, obj: Any) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2)
