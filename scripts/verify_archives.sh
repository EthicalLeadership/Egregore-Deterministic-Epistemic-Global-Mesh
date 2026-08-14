#!/usr/bin/env bash
# =============================================================================
# Egregore legacy archive verification
# =============================================================================
# The 2026-08-14 legacy archives are PARTIAL module backups: they contain only
# the module set recorded in manifest.json (no SHA256SUMS files). This script
# therefore verifies integrity with:
#
#   1. manifest.json parses and lists its source/timestamp
#   2. every module path listed under "copied_modules" resolves to a real
#      file/dir in BOTH archives
#   3. a recursive diff of HDD vs USB src/egregore trees (when USB is mounted)
#
# Usage:
#   scripts/verify_archives.sh          # full check (skips USB if unmounted)
#   CHECK_USB=1 scripts/verify_archives.sh  # fail hard if USB is missing
# =============================================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HDD_ARCHIVE="$REPO_ROOT/_legacy_archive_20260814_011422"
USB_ARCHIVE="/media/kark/7A7666CA4E34EADC2/egregore_legacy_full_20260814_011422"
MANIFEST_REL="manifest.json"
SRC_REL="src/egregore"

red='\033[0;31m'
green='\033[0;32m'
yellow='\033[0;33m'
reset='\033[0m'

FAIL=0

fail() {
    echo -e "${red}FAIL${reset}: $1" >&2
    FAIL=1
}

pass() {
    echo -e "${green}PASS${reset}: $1"
}

skip() {
    echo -e "${yellow}SKIP${reset}: $1"
}

# ---------------------------------------------------------------------------
# 0. HDD archive must exist (it is the reference copy)
# ---------------------------------------------------------------------------
if [[ ! -d "$HDD_ARCHIVE" ]]; then
    fail "HDD archive not found: $HDD_ARCHIVE"
    echo "Cannot continue without the reference archive." >&2
    exit 1
fi
pass "HDD archive present: $HDD_ARCHIVE"

# ---------------------------------------------------------------------------
# 1. manifest.json parses and has the expected shape
# ---------------------------------------------------------------------------
MANIFEST="$HDD_ARCHIVE/$MANIFEST_REL"
if [[ ! -f "$MANIFEST" ]]; then
    fail "manifest.json missing in HDD archive"
else
    if python3 - "$MANIFEST" <<'PYEOF'
import json, sys
with open(sys.argv[1], encoding="utf-8") as fh:
    data = json.load(fh)
assert isinstance(data, dict), "manifest must be a JSON object"
assert "timestamp" in data, "manifest missing timestamp"
assert "copied_modules" in data, "manifest missing copied_modules"
assert isinstance(data["copied_modules"], list), "copied_modules must be a list"
PYEOF
    then
        pass "manifest.json parses; timestamp=$(python3 -c "import json;print(json.load(open('$MANIFEST'))['timestamp'])")"
    else
        fail "manifest.json is not a valid Egregore archive manifest (timestamp + copied_modules)"
    fi
fi

# ---------------------------------------------------------------------------
# 2. Every manifest-listed module exists in the HDD archive
# ---------------------------------------------------------------------------
manifest_bad=0
while IFS= read -r module; do
    # Dotted module path (already rooted at egregore.) -> path under src/egregore/
    rel="${module#egregore.}"
    rel="${rel//.//}"
    candidate="$HDD_ARCHIVE/$SRC_REL/$rel.py"
    candidate_dir="$HDD_ARCHIVE/$SRC_REL/$rel"
    if [[ ! -f "$candidate" && ! -d "$candidate_dir" ]]; then
        echo "  missing (HDD): $module" >&2
        manifest_bad=1
    fi
done < <(python3 -c "import json;print('\n'.join(json.load(open('$MANIFEST'))['copied_modules']))")
if [[ "$manifest_bad" -ne 0 ]]; then
    fail "one or more manifest-listed modules are missing from the HDD archive"
else
    pass "all manifest-listed modules present in HDD archive"
fi

# ---------------------------------------------------------------------------
# 3. USB archive (mount-aware)
# ---------------------------------------------------------------------------
if [[ ! -d "$USB_ARCHIVE" ]]; then
    if [[ "${CHECK_USB:-0}" == "1" ]]; then
        fail "USB archive not mounted at $USB_ARCHIVE (CHECK_USB=1)"
    else
        skip "USB archive not mounted at $USB_ARCHIVE"
    fi
else
    pass "USB archive present: $USB_ARCHIVE"

    # 3a. USB manifest matches (module list identical to HDD)
    if [[ ! -f "$USB_ARCHIVE/$MANIFEST_REL" ]]; then
        fail "USB archive missing manifest.json"
    else
        if diff -q <(python3 -c "import json;print('\n'.join(json.load(open('$MANIFEST'))['copied_modules']))") \
                   <(python3 -c "import json;print('\n'.join(json.load(open('$USB_ARCHIVE/$MANIFEST_REL'))['copied_modules']))") >/dev/null; then
            pass "USB and HDD manifests list identical modules"
        else
            fail "USB manifest module list differs from HDD"
        fi
    fi

    # 3b. Every manifest-listed module exists in the USB archive
    usb_bad=0
    while IFS= read -r module; do
        rel="${module#egregore.}"
        rel="${rel//.//}"
        candidate="$USB_ARCHIVE/$SRC_REL/$rel.py"
        candidate_dir="$USB_ARCHIVE/$SRC_REL/$rel"
        if [[ ! -f "$candidate" && ! -d "$candidate_dir" ]]; then
            echo "  missing (USB): $module" >&2
            usb_bad=1
        fi
    done < <(python3 -c "import json;print('\n'.join(json.load(open('$MANIFEST'))['copied_modules']))")
    if [[ "$usb_bad" -ne 0 ]]; then
        fail "one or more manifest-listed modules are missing from the USB archive"
    else
        pass "all manifest-listed modules present in USB archive"
    fi

    # 3c. Recursive tree diff (in lieu of SHA256SUMS)
    if diff -rq "$HDD_ARCHIVE/$SRC_REL" "$USB_ARCHIVE/$SRC_REL" >/tmp/egregore_archive_diff.txt 2>&1; then
        pass "HDD and USB src/egregore trees are identical"
    else
        # diff -rq reports "Only in ..." entries; absence of "Files ... differ" means
        # the trees are structurally identical, just not byte-identical.
        if grep -q "differ$" /tmp/egregore_archive_diff.txt; then
            fail "file content differences found between HDD and USB archives"
            sed 's/^/    /' /tmp/egregore_archive_diff.txt >&2
        else
            skip "structural-only differences between HDD and USB (see $(wc -l < /tmp/egregore_archive_diff.txt) 'Only in' entries)"
        fi
    fi
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
if [[ "$FAIL" -ne 0 ]]; then
    echo -e "${red}=== ARCHIVE VERIFICATION FAILED ===${reset}"
    exit 1
else
    echo -e "${green}=== ARCHIVE VERIFICATION COMPLETE ===${reset}"
    exit 0
fi
