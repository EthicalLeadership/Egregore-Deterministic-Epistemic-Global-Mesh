"""Tests for the PKCS#11 HSM signing backend.

Unit tests run against a fake ``pkcs11`` module (real Ed25519 math via
PyNaCl underneath; the "private key" never leaves the fake token).
SoftHSM integration tests auto-skip unless EGREGORE_TEST_SOFTHSM=1 and
softhsm2-util is available.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest
from nacl.signing import SigningKey

from egregore.infrastructure.key_management import KeyManagementError
from egregore.infrastructure.pkcs11_signing_backend import (
    HsmError,
    Pkcs11Config,
    Pkcs11KeyManager,
    Pkcs11SigningBackend,
    _normalize_ec_point,
    build_signing_backend_from_env,
)

# --------------------------------------------------------------------------
# Fake pkcs11 module
# --------------------------------------------------------------------------


class _FakeAttribute:
    CLASS = "class"
    LABEL = "label"
    EC_POINT = "ec_point"
    KEY_TYPE = "key_type"


class _FakeObjectClass:
    PUBLIC_KEY = "public"
    PRIVATE_KEY = "private"


class _FakeKeyType:
    EC_EDWARDS = "ec_edwards"


class _FakeMechanism:
    EDDSA = "eddsa"


class _FakeConstants:
    Attribute = _FakeAttribute
    ObjectClass = _FakeObjectClass
    KeyType = _FakeKeyType


class _FakeKeyObject:
    def __init__(self, label: str, object_class: str, signing_key: SigningKey | None):
        self.label = label
        self.object_class = object_class
        self._signing_key = signing_key

    def sign(self, data: bytes, mechanism: str | None = None) -> bytes:
        assert mechanism == _FakeMechanism.EDDSA
        assert self._signing_key is not None
        return self._signing_key.sign(data).signature

    def __getitem__(self, attr: str):
        if attr == _FakeAttribute.EC_POINT:
            assert self._signing_key is not None
            return bytes(self._signing_key.verify_key.encode())
        if attr == _FakeAttribute.LABEL:
            return self.label
        raise KeyError(attr)


class _FakeSession:
    def __init__(self, token: "_FakeToken"):
        self._token = token

    def __enter__(self) -> "_FakeSession":
        return self

    def __exit__(self, *exc) -> None:
        return None

    def get_objects(self, filters: dict):
        klass = filters.get(_FakeAttribute.CLASS)
        label = filters.get(_FakeAttribute.LABEL)
        result = []
        for key in self._token.keys:
            if klass is not None and key.object_class != klass:
                continue
            if label is not None and key.label != label:
                continue
            result.append(key)
        return iter(result)

    def generate_keypair(self, key_type, label, mechanism=None):
        assert key_type == _FakeKeyType.EC_EDWARDS
        sk = SigningKey.generate()
        self._token.keys.append(_FakeKeyObject(label, "private", sk))
        self._token.keys.append(_FakeKeyObject(label, "public", sk))
        return None, None


class _FakeToken:
    def __init__(self, pin: str = "1234", der_wrap_point: bool = False):
        self.pin = pin
        self.keys: list[_FakeKeyObject] = []
        self.der_wrap_point = der_wrap_point

    def add_key(self, label: str) -> SigningKey:
        sk = SigningKey.generate()
        pub = _FakeKeyObject(label, "public", sk)
        priv = _FakeKeyObject(label, "private", sk)
        if self.der_wrap_point:
            original_getitem = pub.__class__.__getitem__

            def wrapped(self_key, attr, _orig=original_getitem):
                value = _orig(self_key, attr)
                if attr == _FakeAttribute.EC_POINT:
                    return b"\x04\x20" + value
                return value

            pub.__getitem__ = wrapped  # type: ignore[method-assign]
        self.keys.extend([pub, priv])
        return sk

    def open(self, user_pin=None, rw=False):
        if user_pin is not None and user_pin != self.pin:
            raise RuntimeError("CKR_PIN_INCORRECT")
        return _FakeSession(self)


class _FakeLib:
    def __init__(self, token: _FakeToken):
        self._token = token

    def get_token(self, slot_id=None, token_label=None):
        if slot_id is not None and slot_id != 0:
            raise RuntimeError("CKR_SLOT_ID_INVALID")
        return self._token

    def get_slots(self, token_present=False):
        return [self]


@pytest.fixture()
def fake_pkcs11(monkeypatch):
    """Install a fake pkcs11 module and return (module, token)."""
    token = _FakeToken()
    lib = _FakeLib(token)
    module = types.ModuleType("pkcs11")
    module.lib = lambda path: lib
    module.constants = _FakeConstants
    module.Mechanism = _FakeMechanism
    module.KeyType = _FakeKeyType
    constants_mod = types.ModuleType("pkcs11.constants")
    for name in ("Attribute", "ObjectClass", "KeyType"):
        setattr(constants_mod, name, getattr(_FakeConstants, name))
    monkeypatch.setitem(sys.modules, "pkcs11", module)
    monkeypatch.setitem(sys.modules, "pkcs11.constants", constants_mod)
    return module, token


def _config(label: str = "egregore-zarc") -> Pkcs11Config:
    return Pkcs11Config(
        module_path="/fake/libsofthsm2.so", key_label=label, slot_id=0, pin="1234"
    )


# --------------------------------------------------------------------------
# Unit tests
# --------------------------------------------------------------------------


class TestEcPointNormalization:
    def test_raw_point_passthrough(self):
        raw = b"\x11" * 32
        assert _normalize_ec_point(raw) == raw

    def test_der_wrapped_point_unwrapped(self):
        raw = b"\x22" * 32
        assert _normalize_ec_point(b"\x04\x20" + raw) == raw

    def test_garbage_rejected(self):
        with pytest.raises(HsmError):
            _normalize_ec_point(b"\x07" * 10)


class TestPkcs11SigningBackend:
    def test_sign_verify_roundtrip(self, fake_pkcs11):
        _, token = fake_pkcs11
        sk = token.add_key("egregore-zarc")
        backend = Pkcs11SigningBackend(_config())

        assert backend.fingerprint() == sk.verify_key.encode().hex()
        signature = backend.sign("payload-hash")
        assert backend.verify("payload-hash", signature, backend.fingerprint())

    def test_verify_rejects_wrong_fingerprint(self, fake_pkcs11):
        _, token = fake_pkcs11
        token.add_key("egregore-zarc")
        backend = Pkcs11SigningBackend(_config())
        signature = backend.sign("payload-hash")
        other = SigningKey.generate()
        assert not backend.verify(
            "payload-hash", signature, other.verify_key.encode().hex()
        )

    def test_missing_key_fails_at_boot(self, fake_pkcs11):
        with pytest.raises(HsmError, match="Key not found"):
            Pkcs11SigningBackend(_config(label="absent"))

    def test_wrong_pin_fails(self, fake_pkcs11):
        _, token = fake_pkcs11
        token.add_key("egregore-zarc")
        config = Pkcs11Config(
            module_path="/fake/lib.so", key_label="egregore-zarc", slot_id=0, pin="0000"
        )
        with pytest.raises(HsmError, match="login failed"):
            Pkcs11SigningBackend(config)

    def test_bad_module_path_fails_closed(self, fake_pkcs11):
        module, token = fake_pkcs11
        token.add_key("egregore-zarc")

        def bad_loader(path):
            raise RuntimeError("cannot open shared object")

        with pytest.raises(HsmError, match="Cannot load PKCS#11 module"):
            Pkcs11SigningBackend(_config(), lib_loader=bad_loader)


class TestPkcs11KeyManager:
    def test_get_key_refuses_export(self, fake_pkcs11):
        _, token = fake_pkcs11
        token.add_key("egregore-zarc")
        manager = Pkcs11KeyManager(_config())
        with pytest.raises(KeyManagementError, match="not exportable"):
            manager.get_key("egregore-zarc")

    def test_get_public_key(self, fake_pkcs11):
        _, token = fake_pkcs11
        sk = token.add_key("egregore-zarc")
        manager = Pkcs11KeyManager(_config())
        assert manager.get_public_key("egregore-zarc") == bytes(
            sk.verify_key.encode()
        )

    def test_generate_and_list(self, fake_pkcs11):
        manager = Pkcs11KeyManager(_config())
        new_label = manager.generate_key("Ed25519")
        assert new_label in manager.list_key_ids()

    def test_unsupported_algorithm_rejected(self, fake_pkcs11):
        manager = Pkcs11KeyManager(_config())
        with pytest.raises(KeyManagementError, match="Ed25519 only"):
            manager.generate_key("AES-256-GCM")

    def test_health_check(self, fake_pkcs11):
        _, token = fake_pkcs11
        token.add_key("egregore-zarc")
        manager = Pkcs11KeyManager(_config())
        health = manager.health_check()
        assert health["status"] == "HEALTHY"
        assert health["backend"] == "pkcs11"


class TestBuildFromEnv:
    def test_local_backend_default(self, monkeypatch):
        monkeypatch.setenv("EGREGORE_SIGNING_KEY_HEX", SigningKey.generate().encode().hex())
        monkeypatch.delenv("EGREGORE_SIGNING_BACKEND", raising=False)
        from egregore.infrastructure.ed25519_signing_backend import (
            Ed25519SigningBackend,
        )

        backend = build_signing_backend_from_env()
        assert isinstance(backend, Ed25519SigningBackend)

    def test_no_key_returns_none(self, monkeypatch):
        monkeypatch.delenv("EGREGORE_SIGNING_BACKEND", raising=False)
        monkeypatch.delenv("EGREGORE_SIGNING_KEY_HEX", raising=False)
        assert build_signing_backend_from_env() is None

    def test_pkcs11_missing_config_fails_closed(self, monkeypatch):
        monkeypatch.setenv("EGREGORE_SIGNING_BACKEND", "pkcs11")
        monkeypatch.delenv("EGREGORE_PKCS11_MODULE", raising=False)
        monkeypatch.delenv("EGREGORE_PKCS11_KEY_LABEL", raising=False)
        with pytest.raises(HsmError, match="missing required env"):
            build_signing_backend_from_env()

    def test_pkcs11_backend_constructed(self, monkeypatch, fake_pkcs11):
        _, token = fake_pkcs11
        token.add_key("egregore-zarc")
        monkeypatch.setenv("EGREGORE_SIGNING_BACKEND", "pkcs11")
        monkeypatch.setenv("EGREGORE_PKCS11_MODULE", "/fake/libsofthsm2.so")
        monkeypatch.setenv("EGREGORE_PKCS11_KEY_LABEL", "egregore-zarc")
        monkeypatch.setenv("EGREGORE_PKCS11_SLOT", "0")
        monkeypatch.setenv("EGREGORE_PKCS11_PIN", "1234")
        backend = build_signing_backend_from_env()
        assert isinstance(backend, Pkcs11SigningBackend)
        assert backend.sign("x")

    def test_unknown_backend_fails_closed(self, monkeypatch):
        monkeypatch.setenv("EGREGORE_SIGNING_BACKEND", "definitely-not-a-backend")
        with pytest.raises(HsmError, match="Unknown EGREGORE_SIGNING_BACKEND"):
            build_signing_backend_from_env()


# --------------------------------------------------------------------------
# SoftHSM integration (auto-skip)
# --------------------------------------------------------------------------

SOFTHSM = shutil.which("softhsm2-util")


@pytest.mark.skipif(
    os.environ.get("EGREGORE_TEST_SOFTHSM") != "1" or SOFTHSM is None,
    reason="SoftHSM integration disabled (set EGREGORE_TEST_SOFTHSM=1, needs softhsm2-util)",
)
class TestSoftHsmIntegration:
    def test_softhsm_sign_verify(self, tmp_path: Path):
        pkcs11 = pytest.importorskip("pkcs11")
        softhsm_lib = None
        for candidate in (
            "/usr/lib/softhsm/libsofthsm2.so",
            "/usr/lib/x86_64-linux-gnu/softhsm/libsofthsm2.so",
            "/usr/local/lib/softhsm/libsofthsm2.so",
        ):
            if Path(candidate).exists():
                softhsm_lib = candidate
                break
        if softhsm_lib is None:
            pytest.skip("libsofthsm2.so not found")

        token_dir = tmp_path / "tokens"
        token_dir.mkdir()
        env = dict(os.environ, SOFTHSM2_CONF=str(tmp_path / "softhsm2.conf"))
        (tmp_path / "softhsm2.conf").write_text(
            f"directories.tokendir = {token_dir}\n"
            "objectstore.backend = file\n"
            "log.level = ERROR\n"
        )
        subprocess.run(
            [
                SOFTHSM,
                "--init-token",
                "--free",
                "--label",
                "egregore",
                "--so-pin",
                "0000",
                "--pin",
                "1234",
            ],
            env=env,
            check=True,
            capture_output=True,
        )

        lib = pkcs11.lib(softhsm_lib)
        token = lib.get_token(token_label="egregore")
        with token.open(user_pin="1234", rw=True) as session:
            try:
                session.generate_keypair(
                    pkcs11.KeyType.EC_EDWARDS,
                    label="egregore-zarc",
                    mechanism=pkcs11.Mechanism.EDDSA,
                )
            except Exception as exc:
                pytest.skip(f"token does not support Ed25519/EDDSA: {exc}")

        backend = Pkcs11SigningBackend(
            Pkcs11Config(
                module_path=softhsm_lib,
                key_label="egregore-zarc",
                token_label="egregore",
                pin="1234",
            )
        )
        signature = backend.sign("softhsm-payload")
        assert backend.verify("softhsm-payload", signature, backend.fingerprint())
