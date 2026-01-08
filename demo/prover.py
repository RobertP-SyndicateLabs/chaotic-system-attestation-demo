# prover.py
import time
import math
import json
import argparse
from typing import Dict, Any

from common import (
    Challenge, PROTOCOL_VERSION,
    sha256_hex, hmac_sha256_hex, canonical_json, load_json
)

def lorenz_step(x, y, z, dt, sigma=10.0, rho=28.0, beta=8.0/3.0):
    dx = sigma * (y - x)
    dy = x * (rho - z) - y
    dz = x * y - beta * z
    return (x + dx*dt, y + dy*dt, z + dz*dt)

def run_engine(seed: float, steps: int, budget_ms: int):
    x = math.sin(seed)
    y = math.cos(seed)
    z = seed % 1.0

    t0 = time.perf_counter()
    deadline = t0 + (budget_ms / 1000.0)

    jitter_acc = 0.0
    iters = 0

    # Bounded execution loop
    for _ in range(steps):
        t = time.perf_counter()
        # “Public-safe” coupling: use tiny timing-dependent dt
        dt = (t % 0.01) + 1e-6
        jitter_acc += (dt * 1e6)
        x, y, z = lorenz_step(x, y, z, dt)
        iters += 1

        if time.perf_counter() > deadline:
            break

    elapsed = time.perf_counter() - t0
    return x, y, z, jitter_acc, elapsed, iters

def main():
    ap = argparse.ArgumentParser(description="CSA-lite prover (public-safe demo).")
    ap.add_argument("--challenge", required=True, help="Path to challenge.json")
    ap.add_argument("--key", required=False, help="Optional shared key (hex) for HMAC binding (demo-only)")
    args = ap.parse_args()

    ch_obj: Dict[str, Any] = load_json(args.challenge)

    # Accept extra fields safely
    ch = Challenge(
        v=ch_obj["v"],
        nonce=ch_obj["nonce"],
        seed=float(ch_obj["seed"]),
        steps=int(ch_obj["steps"]),
        budget_ms=int(ch_obj["budget_ms"]),
        ttl_ms=int(ch_obj.get("ttl_ms", 750)),
        issued_at=ch_obj.get("issued_at"),
    )

    if ch.v != PROTOCOL_VERSION:
        raise SystemExit(f"Protocol mismatch: got {ch.v}, expected {PROTOCOL_VERSION}")

    x, y, z, jitter_acc, elapsed, iters = run_engine(ch.seed, ch.steps, ch.budget_ms)

    # Public-safe response vector (compact + interpretable)
    response_vec = {
        "v": PROTOCOL_VERSION,
        "nonce": ch.nonce,
        "iters": int(iters),
        "x": round(x, 6),
        "y": round(y, 6),
        "z": round(z, 6),
        "j": round(jitter_acc, 2),
        "t": round(elapsed, 6),
    }

    # Hash / HMAC are computed over the vector WITHOUT the 'hmac' field
    payload = canonical_json(response_vec)

    if args.key:
        key = bytes.fromhex(args.key)
        response_vec["hmac"] = hmac_sha256_hex(key, payload)

    # Final digest over the full response vector
    final_payload = canonical_json(response_vec)
    out = {
        "response": response_vec,
        "digest": sha256_hex(final_payload),
    }

    print(json.dumps(out, indent=2))

if __name__ == "__main__":
    main()
