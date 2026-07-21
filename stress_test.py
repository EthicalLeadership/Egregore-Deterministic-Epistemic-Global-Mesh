#!/usr/bin/env python3
import os, sys, subprocess
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path("/mnt/egregore/vol-hdd-a/home_data_egregore/egregore")
SRC_ROOT = REPO_ROOT / "src"
SECRETS_DIR = REPO_ROOT / "secrets"
sys.path.insert(0, str(SRC_ROOT))

PASS = FAIL = 0

def test(name):
    def d(f):
        def w():
            global PASS, FAIL
            try:
                f()
                print(f"  PASS: {name}")
                PASS += 1
            except AssertionError as e:
                print(f"  FAIL: {name} — {e}")
                FAIL += 1
            except Exception as e:
                print(f"  ERROR: {name} — {type(e).__name__}: {e}")
                FAIL += 1
        return w
    return d

@test("Secrets purged from git")
def t1():
    r = subprocess.run(["git","log","--all","--full-history","--","secrets/"], cwd=REPO_ROOT, capture_output=True, text=True)
    assert r.stdout.strip() == ""

@test("Signing key: 64 hex, mode 600")
def t2():
    k = (SECRETS_DIR / "signing_key.pem").read_text().strip()
    assert len(k) == 64 and all(c in "0123456789abcdef" for c in k.lower())
    assert oct((SECRETS_DIR / "signing_key.pem").stat().st_mode)[-3:] == "600"

@test("KEK: 64 hex, mode 600")
def t3():
    k = (SECRETS_DIR / "cluster_kek.bin").read_text().strip()
    assert len(k) == 64
    assert oct((SECRETS_DIR / "cluster_kek.bin").stat().st_mode)[-3:] == "600"

@test("zarc_config: no hardcoded keys, fail-closed")
def t4():
    c = (REPO_ROOT / "src" / "egregore" / "http_api" / "http" / "v1" / "zarc_config.py").read_text()
    assert "a3b1c2d3" not in c and "01" * 32 not in c and "RuntimeError" in c

@test("zarc_config: fails closed at runtime")
def t5():
    e = os.environ.copy()
    e.pop("BLACKSTAR_ZARC_SIGNING_KEY_HEX", None)
    e.pop("BLACKSTAR_ZARC_SIGNING_KEY_HEX_FILE", None)
    r = subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, 'src'); import os; os.environ.pop('BLACKSTAR_ZARC_SIGNING_KEY_HEX', None); os.environ.pop('BLACKSTAR_ZARC_SIGNING_KEY_HEX_FILE', None); from egregore.http_api.http.v1.zarc_config import ZARC_SIGNING_KEY_HEX"], cwd=REPO_ROOT, capture_output=True, text=True, env=e)
    assert r.returncode != 0 and ("RuntimeError" in r.stderr or "mandatory" in r.stderr)

@test("SQLite: no predictable fallback")
def t6():
    c = (REPO_ROOT / "src" / "egregore" / "infrastructure" / "persistence" / "sqlite_dossier_adapter.py").read_text()
    assert '"01" * 32' not in c

@test(".gitignore blocks secrets")
def t7():
    c = (REPO_ROOT / ".gitignore").read_text()
    assert "secrets/*.pem" in c and "secrets/*.bin" in c

@test(".env.example: no defaults")
def t8():
    c = (REPO_ROOT / ".env.example").read_text()
    assert "00010203" not in c and "MANDATORY" in c.upper()

@test("Auth middleware: public API exists")
def t9():
    from egregore.http_api.http.middleware.api_key_middleware import APIKeyMiddleware, require_auth, require_role
    assert APIKeyMiddleware is not None and callable(require_auth) and callable(require_role)

@test("Auth middleware: fails closed with no keys")
def t10():
    import importlib
    with patch.dict(os.environ, {}, clear=True):
        importlib.reload(__import__("egregore.http_api.http.middleware.api_key_middleware", fromlist=["_API_KEYS"]))
        from egregore.http_api.http.middleware.api_key_middleware import _API_KEYS
        assert len(_API_KEYS) == 0

@test("Auth middleware: accepts 64-char key")
def t11():
    import importlib
    with patch.dict(os.environ, {"BLACKSTAR_API_KEYS": "a" * 64 + ":test:admin:admin"}):
        importlib.reload(__import__("egregore.http_api.http.middleware.api_key_middleware", fromlist=["_API_KEYS"]))
        from egregore.http_api.http.middleware.api_key_middleware import _API_KEYS
        assert "a" * 64 in _API_KEYS

@test("Auth middleware: rejects short key")
def t12():
    import importlib
    with patch.dict(os.environ, {"BLACKSTAR_API_KEYS": "a" * 32 + ":test:admin:admin"}):
        importlib.reload(__import__("egregore.http_api.http.middleware.api_key_middleware", fromlist=["_API_KEYS"]))
        from egregore.http_api.http.middleware.api_key_middleware import _API_KEYS
        assert "a" * 32 not in _API_KEYS

@test("FreezeController: NORMAL start")
def t13():
    from egregore.shared.freeze_state import FreezeController, FreezeState
    fc = FreezeController(tenant_id="test")
    assert fc.state == FreezeState.HEALTHY

@test("FreezeController: idempotent freeze")
def t14():
    from egregore.shared.freeze_state import FreezeController
    fc = FreezeController(tenant_id="test")
    fc.freeze(reason="test")
    fc.freeze(reason="test2")
    assert fc.is_frozen

@test("FreezeController: unfreeze + reset")
def t15():
    from egregore.shared.freeze_state import FreezeController, FreezeState
    fc = FreezeController(tenant_id="test")
    fc.freeze(reason="test")
    fc.unfreeze(reason="v", operator_id="a")
    fc.reset(reason="ok", operator_id="b")
    assert fc.state == FreezeState.HEALTHY

@test("FreezeController: forensic detail")
def t16():
    from egregore.shared.freeze_state import FreezeController
    fc = FreezeController(tenant_id="test")
    fc.freeze(reason="hash mismatch", block_hash_trigger="abc123", recomputed_hash="def456")
    assert fc.history[-1].block_hash_trigger == "abc123"

@test("FreezeController: 100 rapid cycles")
def t17():
    from egregore.shared.freeze_state import FreezeController, FreezeState
    fc = FreezeController(tenant_id="stress")
    for i in range(100):
        fc.freeze(reason=f"attack_{i}")
        assert fc.is_frozen
        fc.unfreeze(reason="verify", operator_id="sre")
        assert fc.state == FreezeState.RECONCILING
        fc.reset(reason="ok", operator_id="admin")
        assert fc.state == FreezeState.HEALTHY
    assert len(fc.history) == 300

@test("CompositionRoot: no forbidden globals")
def t18():
    import egregore.application.composition_root as cr
    assert not hasattr(cr, '_FACADE') or getattr(cr, '_FACADE', None) is None

@test("CompositionRoot: has dispose")
def t19():
    from egregore.application.composition_root import CompositionRoot
    assert callable(getattr(CompositionRoot, 'dispose'))

@test("CI: no continue-on-error")
def t20():
    c = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text()
    assert "continue-on-error: true" not in c

@test("CI: has lint + type + security")
def t21():
    c = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text()
    assert "  lint:" in c and "ruff check" in c
    assert "  type-check:" in c and "mypy" in c
    assert "  security-scan:" in c and "bandit" in c and "pip-audit" in c

@test("CI: bandit --severity-level high")
def t22():
    c = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text()
    assert "--severity-level high" in c

@test("CI: mypy --check-untyped-defs")
def t23():
    c = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text()
    assert "--check-untyped-defs" in c

@test("CI: if precedence fixed")
def t24():
    c = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text()
    bad = "always() && needs.deploy-pioneer1.result == 'success' || needs.deploy-red-dart.result == 'success'"
    assert bad not in c

print("=" * 60)
print("BLACKSTAR STRESS TEST")
print("=" * 60)
for t in [t1, t2, t3, t4, t5, t6, t7, t8, t9, t10, t11, t12, t13, t14, t15, t16, t17, t18, t19, t20, t21, t22, t23, t24]:
    t()
print()
print(f"RESULTS: {PASS} PASS, {FAIL} FAIL")
print("GREEN" if FAIL == 0 else "RED")

