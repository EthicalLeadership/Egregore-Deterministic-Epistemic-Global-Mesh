#!/usr/bin/env python3
"""Egregore QAV Audit — HONEST VERSION
Fails on MISSING. No mercy.
"""
import sys, os, subprocess, json
from pathlib import Path

REPO = Path.home() / "egregore"
SRC = REPO / "src" / "egregore"
TESTS = REPO / "tests"

def exists_any(patterns, root=SRC):
    for p in patterns:
        if list(root.rglob(p)):
            return True
    return False

def count_py(root):
    return len(list(root.rglob("*.py")))

def run(cmd):
    try:
        return subprocess.run(cmd, shell=True, capture_output=True, text=True, cwd=REPO).stdout
    except Exception:
        return ""

print("=== BLACKSTAR QAV AUDIT (HONEST) ===")
print(f"Session: 2026-06-16")
print(f"Target: {SRC}")
print(f"Exists: {SRC.exists()}")
print()

score = 0
max_score = 0

# 1. STRUCTURAL INVENTORY
print("--- 1. STRUCTURAL INVENTORY ---")
planes = {
    "application": SRC / "application",
    "domain": SRC / "domain",
    "governance": SRC / "governance",
    "interface": SRC / "interface",
    "kernel": SRC / "kernel",
    "powertrain": SRC / "powertrain",
    "bus": SRC / "bus",
    "infrastructure": SRC / "infrastructure",
    "shared": SRC / "shared",
}
struct_ok = True
for name, path in planes.items():
    if path.exists():
        n = count_py(path)
        print(f"  OK {name:20s} ({n} .py files)")
    else:
        print(f"  MISSING {name:20s}")
        struct_ok = False

# 1b. TWO-PLANE ENFORCEMENT
print("\n--- 1b. TWO-PLANE ENFORCEMENT ---")
domain_imports = run("grep -rn 'import infrastructure\\|import application\\|from infrastructure\\|from application' src/egregore/domain/ --include='*.py'")
if domain_imports.strip():
    print(f"  FAIL: Domain imports outer layers:\n{domain_imports[:500]}")
    struct_ok = False
else:
    print("  PASS: Domain is pure")

max_score += 1
if struct_ok:
    score += 1
    print("  STRUCTURAL: PASS")
else:
    print("  STRUCTURAL: FAIL")

# 2. TEST HEALTH
print("\n--- 2. TEST HEALTH ---")
test_files = list(TESTS.rglob("test_*.py")) if TESTS.exists() else []
print(f"  Test files: {len(test_files)}")
required_tests = [
    "test_arch_enforcement.py",
    "test_imap_connector.py",
    "test_sqlite_dossier_adapter.py",
    "test_tamper.py",
    "test_engine.py",
]
test_ok = True
for t in required_tests:
    found = any(f.name == t for f in test_files)
    status = "OK" if found else "MISSING"
    print(f"  {status} {t}")
    if not found:
        test_ok = False

max_score += 1
if test_ok:
    score += 1
    print("  TEST HEALTH: PASS")
else:
    print("  TEST HEALTH: FAIL")

# 3. CBI-0 GOVERNANCE
print("\n--- 3. CBI-0 GOVERNANCE ---")
cbi_markers = {
    "M1": "M1",
    "M2": "M2",
    "M3": "M3",
    "M4": "M4",
}
cbi_ok = True
for marker, label in cbi_markers.items():
    found = bool(run(f"grep -rn '{marker}' src/egregore/governance/ --include='*.py'").strip())
    status = "OK" if found else "MISSING"
    print(f"  {status} {label}")
    if not found:
        cbi_ok = False

max_score += 1
if cbi_ok:
    score += 1
    print("  CBI-0: PASS")
else:
    print("  CBI-0: FAIL")

# 4. RESILIENCE PATTERNS
print("\n--- 4. RESILIENCE PATTERNS ---")
patterns = {
    "circuit_breaker": ["circuit", "breaker"],
    "retry": ["retry", "backoff"],
    "timeout": ["timeout", "deadline"],
    "bulkhead": ["bulkhead", "semaphore"],
    "fallback": ["fallback", "degrade"],
    "health_check": ["health", "ready", "probe"],
}
res_ok = True
for name, keywords in patterns.items():
    found = False
    for kw in keywords:
        if run(f"grep -rn '{kw}' src/egregore/ --include='*.py' | head -1").strip():
            found = True
            break
    status = "OK" if found else "MISSING"
    print(f"  {status} {name:20s}")
    if not found:
        res_ok = False

max_score += 1
if res_ok:
    score += 1
    print("  RESILIENCE: PASS")
else:
    print("  RESILIENCE: FAIL")

# 5. PERFORMANCE & SCALE
print("\n--- 5. PERFORMANCE & SCALE ---")
async_count = len(run("grep -rn 'async def' src/egregore/ --include='*.py'").strip().splitlines())
sync_count = len(run("grep -rn '^def ' src/egregore/ --include='*.py'").strip().splitlines())
print(f"  Async: {async_count}, Sync: {sync_count}")
perf_ok = async_count >= 10  # At least 10 async functions
max_score += 1
if perf_ok:
    score += 1
    print("  PERFORMANCE: PASS")
else:
    print("  PERFORMANCE: FAIL")

# 6. SECURITY MODEL
print("\n--- 6. SECURITY MODEL ---")
sec_checks = {
    "ed25519": exists_any(["*ed25519*", "*crypto*", "*sign*"], SRC / "kernel"),
    "provenance": exists_any(["*provenance*", "*zarc*"]),
    "auth": exists_any(["*auth*", "*jwt*", "*token*"]),
    "encrypt": exists_any(["*encrypt*", "*cipher*", "*vault*"]),
}
sec_ok = True
for name, found in sec_checks.items():
    status = "OK" if found else "MISSING"
    print(f"  {status} {name}")
    if not found:
        sec_ok = False

max_score += 1
if sec_ok:
    score += 1
    print("  SECURITY: PASS")
else:
    print("  SECURITY: FAIL")

# 7. OPERATIONAL
print("\n--- 7. OPERATIONAL ---")
ops_checks = {
    "docker": exists_any(["Dockerfile*", "docker-compose*"], REPO),
    "systemd": exists_any(["*.service", "*.timer", "*.socket"], REPO),
    "reqs": exists_any(["requirements*.txt", "requirements*.in"], REPO),
    "pyproject": exists_any(["pyproject.toml", "setup.py", "setup.cfg"], REPO),
}
ops_ok = True
for name, found in ops_checks.items():
    status = "OK" if found else "MISSING"
    print(f"  {status} {name:20s}")
    if not found:
        ops_ok = False

max_score += 1
if ops_ok:
    score += 1
    print("  OPERATIONAL: PASS")
else:
    print("  OPERATIONAL: FAIL")

# FINAL
print()
print("=" * 50)
print(f"SCORE: {score}/{max_score}")
if score == max_score:
    print("STATUS: ALL CLEAR")
    sys.exit(0)
else:
    print("STATUS: GAPS DETECTED")
    sys.exit(1)
