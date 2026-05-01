from __future__ import annotations

import base64
import json
import math
import os
import secrets
from pathlib import Path

KEY_DIR = Path(os.getenv("BUIAM_KEY_DIR", "data/keys"))
PUBLIC_EXPONENT = 65537
SYSTEM_KEY_ID = "buiam-auth-system"
MLDSA_ALG = os.getenv("BUIAM_MLDSA_ALG", "ML-DSA-65")


def ensure_oqs_runtime_env() -> None:
    if os.getenv("OQS_INSTALL_PATH"):
        return
    project_root = Path(__file__).resolve().parents[2]
    bundled_install = project_root / "third_party" / "liboqs" / "install"
    if bundled_install.exists():
        os.environ["OQS_INSTALL_PATH"] = str(bundled_install)


def private_key_path(agent_id: str) -> Path:
    return KEY_DIR / f"{agent_id}_private.pem"


def public_key_path(agent_id: str) -> Path:
    return KEY_DIR / f"{agent_id}_public.pem"


def mldsa_private_key_path(agent_id: str) -> Path:
    return KEY_DIR / f"{agent_id}_mldsa_private.pem"


def mldsa_public_key_path(agent_id: str) -> Path:
    return KEY_DIR / f"{agent_id}_mldsa_public.pem"


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
    path.write_text(f"-----BEGIN {label}-----\n" + "\n".join(lines) + f"\n-----END {label}-----\n", encoding="utf-8")


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


def _is_mldsa_keypair_usable(priv_path: Path, pub_path: Path) -> bool:
    try:
        private = _read_pem(priv_path)
        public = _read_pem(pub_path)
        if str(private.get("kty", "")) != "ML-DSA" or str(public.get("kty", "")) != "ML-DSA":
            return False
        if str(private.get("alg", "")) != MLDSA_ALG or str(public.get("alg", "")) != MLDSA_ALG:
            return False
        sk = base64.b64decode(str(private.get("sk", "")))
        pk = base64.b64decode(str(public.get("pk", "")))
        ensure_oqs_runtime_env()
        from oqs import oqs as oqs_mod  # type: ignore

        probe = b"buiam-mldsa-key-health-check"
        with oqs_mod.Signature(MLDSA_ALG, secret_key=sk) as signer:
            sig = signer.sign(probe)
        with oqs_mod.Signature(MLDSA_ALG) as verifier:
            return bool(verifier.verify(probe, sig, pk))
    except Exception:
        return False


def _generate_and_store_mldsa_keypair(priv_path: Path, pub_path: Path) -> None:
    ensure_oqs_runtime_env()
    try:
        from oqs import oqs as oqs_mod  # type: ignore
    except Exception as exc:
        raise RuntimeError("ML-DSA requires liboqs-python package and a loadable liboqs runtime") from exc

    with oqs_mod.Signature(MLDSA_ALG) as signer:
        public_key = signer.generate_keypair()
        secret_key = signer.export_secret_key()
    _write_pem(priv_path, "BUIAM MLDSA PRIVATE KEY", {"kty": "ML-DSA", "alg": MLDSA_ALG, "sk": base64.b64encode(secret_key).decode()})
    _write_pem(pub_path, "BUIAM MLDSA PUBLIC KEY", {"kty": "ML-DSA", "alg": MLDSA_ALG, "pk": base64.b64encode(public_key).decode()})


def ensure_agent_mldsa_keypair(agent_id: str) -> None:
    KEY_DIR.mkdir(parents=True, exist_ok=True)
    priv_path = mldsa_private_key_path(agent_id)
    pub_path = mldsa_public_key_path(agent_id)
    if priv_path.exists() and pub_path.exists() and _is_mldsa_keypair_usable(priv_path, pub_path):
        return
    _generate_and_store_mldsa_keypair(priv_path, pub_path)


def load_mldsa_private_key(agent_id: str) -> dict:
    ensure_agent_mldsa_keypair(agent_id)
    return _read_pem(mldsa_private_key_path(agent_id))


def load_mldsa_public_key(agent_id: str) -> dict:
    ensure_agent_mldsa_keypair(agent_id)
    return _read_pem(mldsa_public_key_path(agent_id))
