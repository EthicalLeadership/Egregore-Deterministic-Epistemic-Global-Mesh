#!/usr/bin/env python3
"""Patch bootstrap.py to register ToolCallMiddleware safely."""

import re
import shutil
import sys
from pathlib import Path
from datetime import datetime

BOOTSTRAP = Path.cwd() / "src/egregore/interface/bootstrap.py"
MIDDLEWARE_IMPORT = "from egregore.http_api.http.middleware.tool_call_middleware import ToolCallMiddleware"
REGISTER_LINE = "app.add_middleware(ToolCallMiddleware)"

def main():
    if not BOOTSTRAP.exists():
        print(f"ERROR: {BOOTSTRAP} not found")
        sys.exit(1)

    text = BOOTSTRAP.read_text()

    # Idempotency
    if REGISTER_LINE in text:
        print("Middleware already registered. Nothing to do.")
        return

    # Backup
    backup_dir = Path.cwd() / "backups" / "bootstrap_patch"
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = backup_dir / f"bootstrap.py.{timestamp}.bak"
    shutil.copy2(BOOTSTRAP, backup_path)
    print(f"Backup created: {backup_path}")

    lines = text.splitlines()

    # ---------- 1. Insert import (after __future__ imports and docstring) ----------
    import_inserted = False
    if "tool_call_middleware" not in text:
        insert_idx = 0
        # Skip shebang
        if lines and lines[0].startswith("#!"):
            insert_idx = 1

        # Skip module docstring (triple-quoted string)
        i = insert_idx
        while i < len(lines):
            stripped = lines[i].lstrip()
            if stripped.startswith(('"""', "'''")):
                quote = stripped[:3]
                if quote in stripped[3:]:
                    insert_idx = i + 1
                    break
                else:
                    for j in range(i+1, len(lines)):
                        if quote in lines[j]:
                            insert_idx = j + 1
                            break
                    break
            else:
                break
            i += 1

        # Skip __future__ imports
        for j in range(insert_idx, len(lines)):
            if lines[j].startswith("from __future__ import"):
                insert_idx = j + 1
            elif lines[j].strip() == "":
                continue
            else:
                break

        lines.insert(insert_idx, MIDDLEWARE_IMPORT)
        import_inserted = True
        print("Import inserted.")

    # ---------- 2. Insert middleware registration ----------
    app_line_idx = None
    for idx, line in enumerate(lines):
        if re.match(r'^\s*app\s*=\s*FastAPI\s*\(', line):
            app_line_idx = idx
            break

    if app_line_idx is None:
        print("ERROR: could not find 'app = FastAPI(' line.")
        sys.exit(1)

    indent = len(lines[app_line_idx]) - len(lines[app_line_idx].lstrip())
    indent_str = ' ' * indent

    # Find closing parenthesis using bracket counting
    open_parens = 0
    close_idx = None
    for j in range(app_line_idx, len(lines)):
        for ch in lines[j]:
            if ch == '(':
                open_parens += 1
            elif ch == ')':
                open_parens -= 1
                if open_parens == 0:
                    close_idx = j
                    break
        if close_idx is not None:
            break

    if close_idx is None:
        print("ERROR: could not find closing parenthesis of FastAPI call.")
        sys.exit(1)

    insert_pos = close_idx + 1
    lines.insert(insert_pos, f"{indent_str}{REGISTER_LINE}")
    print(f"Middleware registration inserted after line {insert_pos+1}.")

    # Write back
    BOOTSTRAP.write_text("\n".join(lines) + "\n")
    print(f"Patched {BOOTSTRAP} successfully.")

if __name__ == "__main__":
    main()
