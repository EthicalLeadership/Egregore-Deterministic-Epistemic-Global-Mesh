"""
Cluster Key Encryption Key (KEK) manager.
Generates, persists, and rotates the node-cluster master KEK.
"""

import hashlib
import os
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

KEYSTORE_DIR = Path(os.environ.get("EGREGORE_KEK_DIR", "/opt/egregore/keystore"))
KEK_FILE = KEYSTORE_DIR / "cluster_kek.bin"
KEK_BACKUP = KEYSTORE_DIR / "cluster_kek.bin.bak"


class KEKError(Exception):
    pass


class KEKNotFoundError(KEKError):
    pass


class KEKCorruptError(KEKError):
    pass


def _ensure_keystore():
    KEYSTORE_DIR.mkdir(parents=True, mode=0o700, exist_ok=True)
    st = os.stat(KEYSTORE_DIR)
    if oct(st.st_mode)[-3:] != "700":
        os.chmod(KEYSTORE_DIR, 0o700)


def generate_kek() -> bytes:
    return AESGCM.generate_key(bit_length=256)


def kek_exists() -> bool:
    return KEK_FILE.exists() and KEK_FILE.stat().st_size == 32


def load_kek() -> bytes:
    _ensure_keystore()
    if not KEK_FILE.exists():
        raise KEKNotFoundError(f"KEK not found at {KEK_FILE}")
    raw = KEK_FILE.read_bytes()
    if len(raw) != 32:
        raise KEKCorruptError(f"KEK file size invalid: {len(raw)} bytes (expected 32)")
    st = os.stat(KEK_FILE)
    if oct(st.st_mode)[-3:] not in ("600", "400"):
        raise KEKCorruptError(f"KEK file permissions too open: {oct(st.st_mode)}")
    return raw


def persist_kek(key_bytes: bytes) -> None:
    _ensure_keystore()
    if len(key_bytes) != 32:
        raise KEKCorruptError(f"Invalid KEK length: {len(key_bytes)}")
    if KEK_FILE.exists():
        KEK_FILE.replace(KEK_BACKUP)
    KEK_FILE.write_bytes(key_bytes)
    os.chmod(KEK_FILE, 0o600)


def initialize_kek(force_rotate: bool = False) -> str:
    _ensure_keystore()
    if kek_exists() and not force_rotate:
        kek = load_kek()
        kek_id = hashlib.sha256(kek).hexdigest()[:16]
        return kek_id
    kek = generate_kek()
    persist_kek(kek)
    kek_id = hashlib.sha256(kek).hexdigest()[:16]
    return kek_id


def wrap_dek(dek_bytes: bytes, kek_bytes: bytes | None = None) -> dict:
    kek = kek_bytes or load_kek()
    aesgcm = AESGCM(kek)
    nonce = os.urandom(12)
    ct = aesgcm.encrypt(nonce, dek_bytes, None)
    kek_id = hashlib.sha256(kek).hexdigest()[:16]
    return {
        "kek_id": kek_id,
        "nonce": nonce.hex(),
        "ciphertext": ct.hex(),
        "algorithm": "AES-256-GCM",
    }


def unwrap_dek(wrapped: dict, kek_bytes: bytes | None = None) -> bytes:
    kek = kek_bytes or load_kek()
    aesgcm = AESGCM(kek)
    nonce = bytes.fromhex(wrapped["nonce"])
    ct = bytes.fromhex(wrapped["ciphertext"])
    return aesgcm.decrypt(nonce, ct, None)


def kek_health_check() -> dict:
    try:
        kek = load_kek()
        kek_id = hashlib.sha256(kek).hexdigest()[:16]
        return {
            "status": "HEALTHY",
            "kek_id": kek_id,
            "path": str(KEK_FILE),
            "permissions_ok": True,
        }
    except KEKNotFoundError:
        return {
            "status": "MISSING",
            "kek_id": None,
            "path": str(KEK_FILE),
            "permissions_ok": False,
        }
    except KEKCorruptError as e:
        return {
            "status": "CORRUPT",
            "kek_id": None,
            "path": str(KEK_FILE),
            "permissions_ok": False,
            "error": str(e),
        }
