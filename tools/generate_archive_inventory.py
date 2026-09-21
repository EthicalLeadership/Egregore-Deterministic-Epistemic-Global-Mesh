#!/usr/bin/env python3
"""
generate_archive_inventory.py

Generate the list of files/directories to archive for the legacy-crown purge.
Keeps only the canonical living tree:
  - src/egregore/
  - tests/
  - archive/ (merge artifacts)
  - pyproject.toml
  - pytest.ini
  - .gitignore
  - README.md
  - .agents/ (project skill)
  - reorganize_egregore.py and archive_legacy_crown.sh (tools for this run)
  - .venv/ (needed to run tests; not archived but kept)

Everything else is dead and goes to the HDD vault.
"""

from pathlib import Path
import subprocess
import sys

ROOT = Path.cwd()

KEEP = {
    "src/egregore",
    "tests",
    "archive",
    "pyproject.toml",
    "pytest.ini",
    ".gitignore",
    "README.md",
    "reorganize_egregore.py",
    "archive_legacy_crown.sh",
    "generate_archive_inventory.py",
    ".agents",
    ".venv",
}


def is_inside(path: Path, candidates: set[str]) -> bool:
    s = str(path)
    for c in candidates:
        if s == c or s.startswith(c + "/"):
            return True
    return False


def is_parent(path: Path, candidates: set[str]) -> bool:
    s = str(path)
    for c in candidates:
        if c.startswith(s + "/"):
            return True
    return False


def unquote(path: str) -> str:
    """Git status/ls-files may quote paths with special characters."""
    if path.startswith('"') and path.endswith('"'):
        path = path[1:-1]
        # Git escapes quotes as \\\" in quoted output.
        path = path.replace('\\"', '"')
    return path


def main() -> int:
    tracked = {unquote(p) for p in subprocess.run(["git", "ls-files"], capture_output=True, text=True).stdout.splitlines()}
    untracked = {
        unquote(p)
        for p in subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            capture_output=True,
            text=True,
        ).stdout.splitlines()
    }
    # Also include ignored-but-present files (the debris that previous purges left behind).
    ignored = {
        unquote(p)
        for p in subprocess.run(
            ["git", "ls-files", "--others", "--ignored", "--exclude-standard"],
            capture_output=True,
            text=True,
        ).stdout.splitlines()
    }

    all_entries: set[str] = set()
    for t in tracked | untracked | ignored:
        all_entries.add(t)
        parts = Path(t).parts
        for i in range(1, len(parts)):
            all_entries.add(str(Path(*parts[:i])))

    dead_dirs: list[str] = []
    dead_files: list[str] = []

    for e in sorted(all_entries):
        p = ROOT / e
        if not p.exists() and not p.is_symlink():
            continue
        if "__pycache__" in e.split("/"):
            continue
        if is_inside(Path(e), KEEP):
            continue
        if is_parent(Path(e), KEEP):
            continue
        # Skip symlinks pointing outside the repo (e.g. external USB drive).
        if p.is_symlink():
            try:
                target = p.resolve()
                if not str(target).startswith(str(ROOT.resolve())):
                    continue
            except Exception:
                pass
        if p.is_dir() and not p.is_symlink():
            dead_dirs.append(e)
        else:
            dead_files.append(e)

    # Write inventory files next to the script so bash can source them.
    with open("archive_dead_dirs.txt", "w") as f:
        for d in dead_dirs:
            f.write(d + "\n")
    with open("archive_dead_files.txt", "w") as f:
        for f_ in dead_files:
            f.write(f_ + "\n")

    print(f"Generated archive inventory: {len(dead_dirs)} dirs, {len(dead_files)} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
