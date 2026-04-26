from __future__ import annotations

import base64
import json
import math
import secrets
from pathlib import Path


KEY_DIR = Path("data/keys")
PUBLIC_EXPONENT = 65537
SYSTEM_KEY_ID = "buiam-auth-system"


def private_key_path(agent_id: str) -> Path:
    return KEY_DIR / f"{agent_id}_private.pem"


def public_key_path(agent_id: str) -> Path:
    return KEY_DIR / f"{agent_id}_public.pem"


def ensure_system_keypair() -> None:
    ensure_agent_keypair(SYSTEM_KEY_ID)


def load_system_private_key() -> dict:
    ensure_system_keypair()
    return load_private_key(SYSTEM_KEY_ID)


def load_system_public_key() -> dict:
    ensure_system_keypair()
    return load_public_key(SYSTEM_KEY_ID)


def _is_probable_prime(candidate: int, rounds: int = 12) -> bool:
    if candidate < 2:
        return False
    small_primes = [2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37]
    if candidate in small_primes:
        return True
    if any(candidate % prime == 0 for prime in small_primes):
        return False

    exponent = candidate - 1
    factor = 0
    while exponent % 2 == 0:
        factor += 1
        exponent //= 2

    for _ in range(rounds):
        base = secrets.randbelow(candidate - 3) + 2
        value = pow(base, exponent, candidate)
        if value in (1, candidate - 1):
            continue
        for _ in range(factor - 1):
            value = pow(value, 2, candidate)
            if value == candidate - 1:
                break
        else:
            return False
    return True


def _generate_prime(bits: int) -> int:
    while True:
        candidate = secrets.randbits(bits) | (1 << (bits - 1)) | 1
        if _is_probable_prime(candidate):
            return candidate


def _generate_rsa_keypair(bits: int = 1024) -> tuple[dict, dict]:
    while True:
        p = _generate_prime(bits // 2)
        q = _generate_prime(bits // 2)
        if p == q:
            continue
        phi = (p - 1) * (q - 1)
        if math.gcd(PUBLIC_EXPONENT, phi) == 1:
            break
    n = p * q
    d = pow(PUBLIC_EXPONENT, -1, phi)
    private = {"kty": "BUIAM-RSA", "n": str(n), "e": str(PUBLIC_EXPONENT), "d": str(d)}
    public = {"kty": "BUIAM-RSA", "n": str(n), "e": str(PUBLIC_EXPONENT)}
    return private, public


def _write_pem(path: Path, label: str, payload: dict) -> None:
    encoded = base64.b64encode(json.dumps(payload).encode()).decode()
    lines = [encoded[index : index + 64] for index in range(0, len(encoded), 64)]
    path.write_text(
        f"-----BEGIN {label}-----\n" + "\n".join(lines) + f"\n-----END {label}-----\n",
        encoding="utf-8",
    )


def _read_pem(path: Path) -> dict:
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if not line.startswith("---")]
    return json.loads(base64.b64decode("".join(lines)).decode())


def ensure_agent_keypair(agent_id: str) -> None:
    KEY_DIR.mkdir(parents=True, exist_ok=True)
    private_path = private_key_path(agent_id)
    public_path = public_key_path(agent_id)
    if private_path.exists() and public_path.exists():
        return
    private, public = _generate_rsa_keypair()
    _write_pem(private_path, "BUIAM RSA PRIVATE KEY", private)
    _write_pem(public_path, "BUIAM RSA PUBLIC KEY", public)


def load_private_key(agent_id: str) -> dict:
    ensure_agent_keypair(agent_id)
    return _read_pem(private_key_path(agent_id))


def load_public_key(agent_id: str) -> dict:
    ensure_agent_keypair(agent_id)
    return _read_pem(public_key_path(agent_id))
