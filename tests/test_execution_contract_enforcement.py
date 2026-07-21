from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

_SRC_ROOT = Path(__file__).resolve().parents[1] / "src"


def _iter_python_files(root: Path) -> Iterable[Path]:
    for p in root.rglob("*.py"):
        # Skip typical noise directories if present
        parts = set(p.parts)
        if ".venv" in parts or "__pycache__" in parts or ".pytest_cache" in parts:
            continue
        yield p


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _count_calls(text: str, *, attr: str) -> int:
    """
    Counts explicit call sites of the form: <something>.<attr>(...
    Example: ".commit_generate_t2("
    """
    pattern = re.compile(rf"\.\s*{re.escape(attr)}\s*\(")
    return len(pattern.findall(text))


def _count_constructor_calls(text: str, cls_name: str) -> int:
    """
    Counts explicit constructor calls of the form: <cls_name>(...
    Example: "AuditEvent(" or "OutboxEntry("
    """
    pattern = re.compile(rf"\b{re.escape(cls_name)}\s*\(")
    return len(pattern.findall(text))


def test_no_alternate_t2_commit_path_rule() -> None:
    """
    Invariant: only the deterministic core executor should invoke T2 commit on the tx port.
    We allow:
    - method definitions: `def commit_generate_t2(...):`
    - the protocol declaration
    - the call site inside `CorePlaneGenerateDossierExecutor`
    """
    call_sites: list[Path] = []

    for p in _iter_python_files(_SRC_ROOT):
        txt = _read_text(p)
        # Avoid false positives on method definition lines by matching `.commit_generate_t2(`
        if _count_calls(txt, attr="commit_generate_t2") > 0:
            call_sites.append(p)

    # Expected: only semantics_executor.py and the ANCHORUM forensic batch runner call
    # commit on the injected tx port. ANCHORUM commits a forensic case snapshot through
    # the same journal interface but does not create an alternate dossier-execution route.
    expected = {"semantics_executor.py", "batch_runner.py"}
    assert {
        p.name for p in call_sites
    } == expected, f"Unexpected commit call sites: {[str(p) for p in call_sites]}"


def test_single_source_of_truth_artifacts_rule() -> None:
    """
    Invariant: orchestration/adapters must not directly construct AuditEvent/OutboxEntry.
    They must come only from derive_generate_artifacts() in derivations.py.
    """
    bad_audit_event_files: list[Path] = []
    bad_outbox_entry_files: list[Path] = []

    for p in _iter_python_files(_SRC_ROOT):
        txt = _read_text(p)

        audit_ctors = _count_constructor_calls(txt, cls_name="AuditEvent")
        outbox_ctors = _count_constructor_calls(txt, cls_name="OutboxEntry")

        if audit_ctors > 0 and p.name != "derivations.py":
            bad_audit_event_files.append(p)
        if outbox_ctors > 0 and p.name != "derivations.py":
            bad_outbox_entry_files.append(p)

    assert (
        not bad_audit_event_files
    ), f"AuditEvent constructor calls outside derivations.py: {[str(p) for p in bad_audit_event_files]}"
    assert (
        not bad_outbox_entry_files
    ), f"OutboxEntry constructor calls outside derivations.py: {[str(p) for p in bad_outbox_entry_files]}"


def test_derive_generate_artifacts_invocation_rule() -> None:
    """
    Invariant: derive_generate_artifacts() should be called only by the deterministic core executor.
    """
    call_files: list[Path] = []

    for p in _iter_python_files(_SRC_ROOT):
        txt = _read_text(p)
        if p.name == "derivations.py":
            # Ignore function definition; we only care about call sites.
            continue
        if re.search(r"\bderive_generate_artifacts\s*\(", txt):
            call_files.append(p)

    # Expected: semantics_executor.py is the only call site.
    assert [p.name for p in call_files] == [
        "semantics_executor.py"
    ], f"Unexpected derive_generate_artifacts call sites: {[str(p) for p in call_files]}"


def test_replay_layer_has_no_t2_commit_rule() -> None:
    """
    Invariant: replay interpreter is pure (no durable commit invocation paths).
    """
    # Directly scan replay interpreter file.
    replay_path = (
        _SRC_ROOT / "egregore" / "application" / "semantics_replay_interpreter.py"
    )
    assert replay_path.exists(), f"Missing replay interpreter: {replay_path}"

    txt = _read_text(replay_path)
    assert (
        _count_calls(txt, attr="commit_generate_t2") == 0
    ), "Replay interpreter must not call commit_generate_t2"
