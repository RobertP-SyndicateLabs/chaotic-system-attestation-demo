# verifier.py
import time
import json
import secrets
import argparse
from typing import Optional, Tuple, Dict, Any

from common import (
    Challenge, PROTOCOL_VERSION,
    canonical_json, hmac_sha256_hex,
    load_json, save_json
)

ISSUED_DB = "issued_db.json"

def now_s() -> float:
    return time.time()

def load_db(path: str = ISSUED_DB) -> Dict[str, Any]:
    try:
        return load_json(path)
    except FileNotFoundError:
        return {"v": 1, "nonces": {}}

def save_db(db: Dict[str, Any], path: str = ISSUED_DB) -> None:
    save_json(path, db)

def make_challenge(steps: int = 5000, budget_ms: int = 35, ttl_ms: int = 750) -> Challenge:
    nonce = secrets.token_hex(16)
    seed = int.from_bytes(secrets.token_bytes(8), "big") / 2**64

    return Challenge(
        v=PROTOCOL_VERSION,
        nonce=nonce,
        seed=seed,
        steps=int(steps),
        budget_ms=int(budget_ms),
        ttl_ms=int(ttl_ms),
        issued_at=now_s(),
    )

def record_issue(ch: Challenge, db_path: str = ISSUED_DB) -> None:
    db = load_db(db_path)
    db["nonces"][ch.nonce] = {
        "issued_at": ch.issued_at,
        "ttl_ms": ch.ttl_ms,
        "used": False,
    }
    save_db(db, db_path)

def mark_used(nonce: str, db_path: str = ISSUED_DB) -> None:
    db = load_db(db_path)
    if nonce in db["nonces"]:
        db["nonces"][nonce]["used"] = True
        save_db(db, db_path)

def is_fresh_and_unused(ch: Challenge, db_path: str = ISSUED_DB) -> Tuple[bool, str]:
    db = load_db(db_path)
    entry = db["nonces"].get(ch.nonce)
    if not entry:
        return False, "Unknown nonce (not issued by this verifier instance)"

    if entry.get("used"):
        return False, "Replay detected (nonce already used)"

    issued_at = float(entry.get("issued_at", 0.0))
    ttl_ms = int(entry.get("ttl_ms", ch.ttl_ms))
    age_ms = (now_s() - issued_at) * 1000.0

    if age_ms < 0:
        return False, "Clock anomaly"
    if age_ms > ttl_ms:
        return False, f"Expired challenge (age_ms={age_ms:.1f} > ttl_ms={ttl_ms})"

    return True, "Fresh"

def load_profile(path: Optional[str]) -> Optional[Dict[str, Any]]:
    if not path:
        return None
    return load_json(path)

def within_envelope(val: float, mean: float, std: float, k: float, min_abs: Optional[float]=None, max_abs: Optional[float]=None) -> bool:
    lo = mean - k * std
    hi = mean + k * std
    if min_abs is not None:
        lo = max(lo, min_abs)
    if max_abs is not None:
        hi = min(hi, max_abs)
    return lo <= val <= hi

def verify_response(
    ch: Challenge,
    resp_obj: Dict[str, Any],
    key_hex: Optional[str] = None,
    profile: Optional[Dict[str, Any]] = None,
    enforce_replay: bool = True,
) -> Tuple[bool, str]:

    # 0) Freshness / replay gate
    if enforce_replay:
        ok, msg = is_fresh_and_unused(ch)
        if not ok:
            return False, msg

    r = resp_obj.get("response", {})
    if r.get("v") != PROTOCOL_VERSION:
        return False, "Protocol mismatch"

    if r.get("nonce") != ch.nonce:
        return False, "Nonce mismatch"

    # 1) Optional HMAC (demo-only)
    if key_hex:
        key = bytes.fromhex(key_hex)
        r_no = dict(r)
        claimed = r_no.pop("hmac", None)
        if not claimed:
            return False, "Missing HMAC"

        payload = canonical_json(r_no)
        expected = hmac_sha256_hex(key, payload)
        if expected != claimed:
            return False, "Bad HMAC"

    # 2) Timing sanity (broad, public-safe)
    t = float(r.get("t", 999.0))
    if not (0.0 < t < 2.0):
        return False, f"Timing out of bounds: {t}"

    # 3) Envelope checks (if profile provided)
    if profile:
        k = float(profile.get("k", 3.5))

        # timing envelope
        t_mean = float(profile["t"]["mean"])
        t_std  = float(profile["t"]["std"])
        if not within_envelope(t, t_mean, t_std, k, min_abs=0.0, max_abs=2.0):
            return False, f"Timing outside envelope: t={t} (mean={t_mean}, std={t_std}, k={k})"

        # jitter envelope
        j = float(r.get("j", 0.0))
        j_mean = float(profile["j"]["mean"])
        j_std  = float(profile["j"]["std"])
        if not within_envelope(j, j_mean, j_std, k):
            return False, f"Jitter outside envelope: j={j} (mean={j_mean}, std={j_std}, k={k})"

    # Success: mark used (nonce is single-use in this demo)
    if enforce_replay:
        mark_used(ch.nonce)

    return True, "OK"

def cmd_issue(args) -> int:
    ch = make_challenge(args.steps, args.budget_ms, args.ttl_ms)
    save_json(args.out, ch.__dict__)
    record_issue(ch)

    print(f"Issued challenge -> {args.out}")
    print(f"Nonce: {ch.nonce}")
    print(f"TTL:   {ch.ttl_ms} ms")
    return 0

def cmd_verify(args) -> int:
    ch_obj = load_json(args.challenge)
    ch = Challenge(
        v=ch_obj["v"],
        nonce=ch_obj["nonce"],
        seed=float(ch_obj["seed"]),
        steps=int(ch_obj["steps"]),
        budget_ms=int(ch_obj["budget_ms"]),
        ttl_ms=int(ch_obj.get("ttl_ms", 750)),
        issued_at=ch_obj.get("issued_at"),
    )

    resp = load_json(args.response)
    profile = load_profile(args.profile)

    ok, msg = verify_response(
        ch,
        resp,
        key_hex=args.key,
        profile=profile,
        enforce_replay=not args.no_replay,
    )
    print("VERIFY:", ok, msg)
    return 0 if ok else 2

def mean_std(xs):
    n = len(xs)
    if n == 0:
        return 0.0, 0.0
    m = sum(xs) / n
    if n == 1:
        return m, 0.0
    var = sum((x - m) ** 2 for x in xs) / (n - 1)
    return m, var ** 0.5

def cmd_calibrate(args) -> int:
    """
    Public-safe calibration:
    Run the prover locally N times (by importing prover.run_engine)
    and build timing/jitter envelopes for this device/environment.
    """
    import math
    from prover import run_engine  # local import (same folder)

    # Fixed seed for calibration stability; still fine for public-safe demo
    seed = args.seed
    ts = []
    js = []

    for _ in range(args.runs):
        x, y, z, jitter_acc, elapsed, iters = run_engine(seed, args.steps, args.budget_ms)
        ts.append(float(elapsed))
        js.append(float(jitter_acc))

    t_mean, t_std = mean_std(ts)
    j_mean, j_std = mean_std(js)

    profile = {
        "v": PROTOCOL_VERSION,
        "created_at": now_s(),
        "runs": args.runs,
        "k": args.k,
        "params": {
            "seed": seed,
            "steps": args.steps,
            "budget_ms": args.budget_ms,
        },
        "t": {"mean": t_mean, "std": t_std},
        "j": {"mean": j_mean, "std": j_std},
    }

    save_json(args.out, profile)
    print(f"Wrote profile -> {args.out}")
    print(f"t: mean={t_mean:.6f} std={t_std:.6f}")
    print(f"j: mean={j_mean:.2f} std={j_std:.2f}")
    print(f"k: {args.k} (envelope = mean ± k*std)")
    return 0

def main():
    ap = argparse.ArgumentParser(description="CSA-lite verifier (public-safe demo).")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_issue = sub.add_parser("issue", help="Issue a fresh challenge and record it (replay-protected).")
    p_issue.add_argument("--steps", type=int, default=5000)
    p_issue.add_argument("--budget-ms", type=int, default=35)
    p_issue.add_argument("--ttl-ms", type=int, default=750)
    p_issue.add_argument("--out", default="challenge.json")
    p_issue.set_defaults(fn=cmd_issue)

    p_verify = sub.add_parser("verify", help="Verify a response against an issued challenge.")
    p_verify.add_argument("--challenge", default="challenge.json")
    p_verify.add_argument("--response", default="response.json")
    p_verify.add_argument("--key", required=False, help="Optional shared key (hex) for HMAC binding (demo-only)")
    p_verify.add_argument("--profile", required=False, help="Optional profile.json for envelope verification")
    p_verify.add_argument("--no-replay", action="store_true", help="Disable replay protection (not recommended)")
    p_verify.set_defaults(fn=cmd_verify)

    p_cal = sub.add_parser("calibrate", help="Build a local timing/jitter envelope profile for this device.")
    p_cal.add_argument("--runs", type=int, default=64)
    p_cal.add_argument("--steps", type=int, default=5000)
    p_cal.add_argument("--budget-ms", type=int, default=35)
    p_cal.add_argument("--seed", type=float, default=0.123456)
    p_cal.add_argument("--k", type=float, default=3.5)
    p_cal.add_argument("--out", default="profile.json")
    p_cal.set_defaults(fn=cmd_calibrate)

    args = ap.parse_args()
    return args.fn(args)

if __name__ == "__main__":
    raise SystemExit(main())
