#!/usr/bin/env bash
set -u
DIR=/media/kark/EGREGORE/egregore/tools
mkdir -p "$DIR"
BK="/tmp/sixth-backup-$(date +%Y%m%dT%H%M%S)"
mkdir -p "$BK"
for f in sixth_walker.py sixth_render.py sixth_audit.py; do
    [ -f "$DIR/$f" ] && cp "$DIR/$f" "$BK/$f" && echo "backed up $f"
done
echo "backup: $BK"

cat > "$DIR/sixth_walker.py" << 'WALKER_EOF'
#!/usr/bin/env python3
# Read-only inventory scanner for roots A-G. Writes /tmp/sixth_data.json
import os, re, sys, json, fnmatch, hashlib, datetime

HEADER_LINES = 40
HASH_BYTES = 65536
HEADER_ONLY_BYTES = 2 * 1024 * 1024
BIG_FILE_BYTES = 100 * 1024 * 1024
MAX_IMPORTS = 400
MAX_DEFS = 1500
MAX_PURPOSE = 400
MAX_SCAN_LINES = 200000
SCAN_HEADER_LINES = 5000
SKIP_MARK = "[skipped: secret-bearing]"
BIG_MARK = "[not counted: file over 100MB]"
OUTPUT_DIR_NAME = "sixth_output"

ROOTS = [
    ("A", os.environ.get("SIXTH_ROOT_A", "/media/kark/EGREGORE/egregore")),
    ("B", os.environ.get("SIXTH_ROOT_B", "/home/kark/egregore-core-agent")),
    ("C", os.environ.get("SIXTH_ROOT_C", "/opt/blackstar_control")),
    ("D", os.environ.get("SIXTH_ROOT_D", "/opt/blackstar")),
    ("E", os.environ.get("SIXTH_ROOT_E", "/opt/egregore")),
    ("F", os.environ.get("SIXTH_ROOT_F", "/srv/egregore")),
    ("G", os.environ.get("SIXTH_ROOT_G", "/etc/egregore")),
]

PRUNE_DIRS = {
    "node_modules", ".git", "__pycache__", ".venv", "venv", ".mypy_cache",
    ".pytest_cache", "dist", "build", "site-packages", ".next", ".cache",
    ".ruff_cache", "target", ".tox", ".eggs", OUTPUT_DIR_NAME,
}

SECRET_GLOBS = [
    ".env", ".env.*", "*.env", ".netrc", ".htpasswd", ".pgpass", ".my.cnf",
    ".npmrc", ".pypirc", "credentials.*", "secrets.*", "*.key", "*.pem",
    "*.p12", "*.pfx", "*.jks", "*.keystore", "*.token", "*.secret",
    "id_rsa*", "id_ed25519*", "id_ecdsa*", "id_dsa*",
]
SECRET_FRAGMENTS = ["/.ssh/", "/.aws/credentials", "/.kube/config", "/.docker/config.json"]

BINARY_EXT = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff", ".webp", ".ico",
    ".mp3", ".mp4", ".mov", ".mkv", ".webm", ".wav", ".ogg", ".flac", ".avi",
    ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar", ".iso",
    ".pdf", ".docx", ".xlsx", ".pptx", ".odt", ".ods", ".odp",
    ".so", ".dll", ".dylib", ".a", ".o", ".pyc", ".class", ".jar",
    ".woff", ".woff2", ".ttf", ".otf", ".eot", ".wasm",
    ".gguf", ".safetensors", ".onnx", ".pt", ".pth", ".h5", ".pb",
}

LANG_BY_EXT = {
    ".py": "python", ".pyi": "python", ".pyw": "python",
    ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript", ".jsx": "javascript",
    ".ts": "typescript", ".tsx": "typescript", ".mts": "typescript", ".cts": "typescript",
    ".json": "json", ".json5": "json", ".jsonl": "json",
    ".yaml": "yaml", ".yml": "yaml", ".toml": "toml",
    ".ini": "ini", ".cfg": "ini", ".conf": "ini", ".properties": "ini",
    ".sh": "shell", ".bash": "shell", ".zsh": "shell", ".ksh": "shell", ".fish": "shell",
    ".md": "markdown", ".markdown": "markdown", ".rst": "rst",
    ".html": "html", ".htm": "html", ".vue": "vue", ".svelte": "svelte",
    ".css": "css", ".scss": "scss", ".sass": "scss", ".less": "less",
    ".sql": "sql", ".go": "go", ".rs": "rust", ".java": "java",
    ".c": "c", ".h": "c", ".cc": "cpp", ".cpp": "cpp", ".cxx": "cpp", ".hpp": "cpp", ".hh": "cpp",
    ".rb": "ruby", ".php": "php", ".pl": "perl", ".pm": "perl", ".lua": "lua",
    ".swift": "swift", ".kt": "kotlin", ".kts": "kotlin", ".cs": "csharp",
    ".txt": "text", ".log": "text", ".csv": "csv", ".tsv": "tsv",
    ".xml": "xml", ".svg": "svg", ".graphql": "graphql", ".proto": "protobuf",
    ".service": "systemd", ".timer": "systemd", ".target": "systemd", ".socket": "systemd",
    ".env": "env", ".lock": "lock", ".map": "json", ".ipynb": "notebook",
    ".r": "r", ".jl": "julia", ".dart": "dart", ".ex": "elixir", ".exs": "elixir",
    ".tf": "terraform", ".hcl": "hcl",
    ".bat": "batch", ".ps1": "powershell", ".vim": "vim", ".el": "lisp",
    ".patch": "diff", ".diff": "diff",
}

SOURCE_LANGS = {
    "python", "javascript", "typescript", "shell", "go", "rust", "java", "c", "cpp",
    "ruby", "php", "perl", "lua", "swift", "kotlin", "csharp", "sql", "vue", "svelte",
    "html", "css", "scss", "less", "r", "julia", "dart", "elixir", "terraform", "hcl",
    "powershell", "batch", "graphql", "protobuf", "systemd",
}
CONFIG_LANGS = {"json", "yaml", "toml", "ini", "env"}

def language_of(name):
    lower = name.lower()
    if lower in ("dockerfile", "containerfile"): return "dockerfile"
    if lower in ("makefile", "gnumakefile"): return "makefile"
    if lower.endswith(".d.ts"): return "typescript"
    return LANG_BY_EXT.get(os.path.splitext(lower)[1], "unknown")

def is_secret(rel_path, name):
    for pattern in SECRET_GLOBS:
        if fnmatch.fnmatchcase(name, pattern) or fnmatch.fnmatchcase(name.lower(), pattern.lower()):
            return True
    posix = "/" + rel_path.replace(os.sep, "/")
    for fragment in SECRET_FRAGMENTS:
        if fragment in posix:
            return True
    return False

def iso(ts):
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).astimezone().isoformat()

ENCODING_RE = re.compile(r"coding[:=]")

LANG_COMMENT_MARKERS = {
    "python": ("#",), "javascript": ("//", "/*"), "typescript": ("//", "/*"),
    "shell": ("#",), "ruby": ("#",), "perl": ("#",), "php": ("#", "//", "/*"),
    "lua": ("--",), "sql": ("--",), "yaml": ("#",), "toml": ("#",),
    "ini": ("#", ";"), "env": ("#",), "go": ("//", "/*"), "rust": ("//", "/*"),
    "java": ("//", "/*"), "kotlin": ("//", "/*"), "swift": ("//", "/*"),
    "c": ("//", "/*"), "cpp": ("//", "/*"), "csharp": ("//", "/*"),
    "css": ("/*",), "scss": ("//", "/*"), "less": ("//", "/*"),
    "html": ("<!--",), "xml": ("<!--",), "svg": ("<!--",),
    "markdown": ("#", "<!--"), "rst": (), "text": ("#", "//"),
    "csv": (), "json": (), "notebook": (), "diff": (), "lock": (),
    "terraform": ("#", "//"), "hcl": ("#", "//"), "powershell": ("#",),
    "batch": ("::",), "graphql": ("#",), "protobuf": ("//",),
    "dart": ("//", "/*"), "elixir": ("#",), "r": ("#",), "julia": ("#",),
    "vim": ('"',), "lisp": (";",), "systemd": ("#", ";"),
    "makefile": ("#",), "dockerfile": ("#",), "unknown": ("#", "//"),
}
DEFAULT_MARKERS = ("#", "//")

def extract_purpose(header_lines, language="unknown"):
    lines = header_lines
    allowed = LANG_COMMENT_MARKERS.get(language, DEFAULT_MARKERS)
    if not allowed: return None
    index = 0
    if lines and lines[0].startswith("#!"): index = 1
    while index < len(lines) and not lines[index].strip(): index += 1
    if index >= len(lines): return None
    first = lines[index].lstrip()
    triple = re.match(r'^(?:[rRbBuUfF]{0,2})("""|\'\'\')', first) if language == "python" else None
    if triple:
        quote = triple.group(1)
        body = []
        rest = first[len(triple.group(0)):]
        for chunk in [rest] + lines[index + 1:]:
            if quote in chunk:
                body.append(chunk.split(quote)[0]); break
            body.append(chunk)
        text = " ".join(p.strip() for p in body if p.strip())
        return text[:MAX_PURPOSE] if text else None
    stripped_first = first.strip()
    marker = next((c for c in allowed if stripped_first.startswith(c)), None)
    if marker is None: return None
    collected = []
    for line in lines[index:]:
        stripped = line.strip()
        if marker == "<!--":
            if not (stripped.startswith("<!--") or stripped.endswith("-->") or collected): break
            content = stripped
            if content.startswith("<!--"): content = content[4:]
            if content.endswith("-->"): content = content[:-3]
            content = content.strip()
            if not content: break
            collected.append(content); continue
        if marker == "/*":
            if not (stripped.startswith("/*") or stripped.startswith("*") or stripped.endswith("*/")):
                if collected: break
                continue
            stripped = stripped.lstrip("/").lstrip("*").lstrip("/")
            if stripped.endswith("*/"): stripped = stripped[:-2]
            stripped = stripped.strip()
            if not stripped:
                if collected: break
                continue
            collected.append(stripped); continue
        if marker == "*":
            if not stripped.startswith("*"):
                if collected: break
                continue
            stripped = stripped.lstrip("*").strip()
            if not stripped:
                if collected: break
                continue
            collected.append(stripped); continue
        if not stripped.startswith(marker):
            if collected: break
            continue
        content = stripped[len(marker):].strip()
        if not content:
            if collected: break
            continue
        if content.startswith("-*-") or ENCODING_RE.search(content.split(" ")[0] if content.split(" ") else ""):
            continue
        if content.startswith("!"): continue
        collected.append(content)
    if not collected: return None
    text = re.sub(r"\s+", " ", " ".join(collected)).strip()
    return text[:MAX_PURPOSE] or None

IMPORT_MATCHERS = {
    "python": re.compile(r"^\s*(?:import|from)\s+\S"),
    "javascript": re.compile(r"^\s*import\s|^\s*export\s+.*\bfrom\s|require\s*\(|^\s*import\s*\("),
    "typescript": re.compile(r"^\s*import\s|^\s*export\s+.*\bfrom\s|require\s*\(|^\s*import\s*\(|^\s*import\s+type\s"),
    "c": re.compile(r"^\s*#\s*include\b"),
    "cpp": re.compile(r"^\s*#\s*include\b"),
    "shell": re.compile(r"^\s*\.\s+\S|^\s*source\s+\S"),
    "ruby": re.compile(r"^\s*(?:require|require_relative|load)\b"),
    "php": re.compile(r"^\s*(?:require|include)(?:_once)?\b"),
    "perl": re.compile(r"^\s*(?:use|require)\s+\S"),
    "lua": re.compile(r"require\s*[\(\"']"),
    "go": re.compile(r"^\s*import\b|^\s*[\w.]+\s+\"|^\s*\""),
    "rust": re.compile(r"^\s*(?:use|extern\s+crate)\s+\S"),
    "java": re.compile(r"^\s*import\s+\S"),
    "kotlin": re.compile(r"^\s*import\s+\S"),
    "swift": re.compile(r"^\s*import\s+\S"),
    "csharp": re.compile(r"^\s*using\s+\S"),
    "dart": re.compile(r"^\s*(?:import|export|part)\s+[\"']"),
    "elixir": re.compile(r"^\s*(?:alias|import|require|use)\s+\S"),
    "terraform": re.compile(r"^\s*source\s*="),
    "hcl": re.compile(r"^\s*source\s*="),
    "html": re.compile(r"<script[^>]+src=|<link[^>]+href="),
    "vue": re.compile(r"^\s*import\s|require\s*\(|from\s+[\"']"),
    "svelte": re.compile(r"^\s*import\s|require\s*\(|from\s+[\"']"),
    "css": re.compile(r"^\s*@import\b"),
    "scss": re.compile(r"^\s*@(?:import|use|forward)\b"),
    "less": re.compile(r"^\s*@import\b"),
    "graphql": re.compile(r"^\s*#import\b"),
    "protobuf": re.compile(r"^\s*import\s+\""),
}

DEF_PATTERNS = {
    "python": [re.compile(r"^(?:async\s+)?def\s+([A-Za-z_]\w*)"), re.compile(r"^class\s+([A-Za-z_]\w*)")],
    "javascript": [
        re.compile(r"^(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)"),
        re.compile(r"^(?:export\s+)?(?:default\s+)?class\s+([A-Za-z_$][\w$]*)"),
        re.compile(r"^(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*="),
    ],
    "typescript": [
        re.compile(r"^(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)"),
        re.compile(r"^(?:export\s+)?(?:abstract\s+)?class\s+([A-Za-z_$][\w$]*)"),
        re.compile(r"^(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*="),
        re.compile(r"^(?:export\s+)?interface\s+([A-Za-z_$][\w$]*)"),
        re.compile(r"^(?:export\s+)?type\s+([A-Za-z_$][\w$]*)\s*="),
        re.compile(r"^(?:export\s+)?enum\s+([A-Za-z_$][\w$]*)"),
    ],
    "shell": [re.compile(r"^([A-Za-z_][\w]*)\s*\(\)\s*\{"), re.compile(r"^function\s+([A-Za-z_][\w]*)")],
    "ruby": [re.compile(r"^\s*def\s+([\w?!=\[\]]+)"), re.compile(r"^\s*class\s+([\w:]+)"), re.compile(r"^\s*module\s+([\w:]+)")],
    "php": [re.compile(r"^\s*function\s+([\w]+)"), re.compile(r"^\s*class\s+([\w]+)")],
    "perl": [re.compile(r"^\s*sub\s+([\w]+)")],
    "lua": [re.compile(r"^\s*local\s+function\s+([\w.:]+)"), re.compile(r"^\s*function\s+([\w.:]+)")],
    "go": [re.compile(r"^func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)"), re.compile(r"^type\s+([A-Za-z_]\w*)")],
    "rust": [re.compile(r"^\s*(?:pub\s+)?(?:async\s+)?fn\s+([A-Za-z_]\w*)"), re.compile(r"^\s*(?:pub\s+)?(?:struct|enum|trait|union)\s+([A-Za-z_]\w*)"), re.compile(r"^\s*mod\s+([A-Za-z_]\w*)")],
    "dart": [re.compile(r"^\s*(?:class|mixin|enum)\s+([A-Za-z_]\w*)")],
    "elixir": [re.compile(r"^\s*defmodule\s+([\w.]+)"), re.compile(r"^\s*def\s+([\w?!]+)")],
    "powershell": [re.compile(r"^\s*function\s+([\w-]+)")],
    "systemd": [re.compile(r"^\[(\w+)\]")],
}

YAML_KEY = re.compile(r"^([A-Za-z0-9_][A-Za-z0-9_.\-]*)\s*:(?:\s|$)")
TOML_KEY = re.compile(r"^([A-Za-z0-9_][A-Za-z0-9_.\-]*)\s*=")
INI_SECTION = re.compile(r"^\[([^\]]+)\]")
INI_KEY = re.compile(r"^([A-Za-z0-9_][A-Za-z0-9_.\-]*)\s*=")

def extract_keys(lang, text):
    keys = []
    if lang == "json":
        try: value = json.loads(text)
        except Exception: value = None
        if isinstance(value, dict): return list(value.keys())[:300]
        depth = 0
        for line in text.splitlines():
            stripped = line.strip()
            if depth == 1:
                m = re.match(r'^"([^"]+)"\s*:', stripped)
                if m: keys.append(m.group(1))
            depth += stripped.count("{") + stripped.count("[") - stripped.count("}") - stripped.count("]")
        return keys[:300]
    if lang == "toml":
        for line in text.splitlines():
            if not line or line[0] in " \t" or line.startswith("["): continue
            m = TOML_KEY.match(line)
            if m: keys.append(m.group(1))
        return keys[:300]
    if lang == "yaml":
        for line in text.splitlines():
            if not line or line[0] in " \t-#" or line.startswith("---") or line.startswith("..."): continue
            m = YAML_KEY.match(line)
            if m: keys.append(m.group(1))
        return keys[:300]
    if lang == "ini":
        sections, pairs = [], []
        for line in text.splitlines():
            s = line.strip()
            if not s or s.startswith("#") or s.startswith(";"): continue
            m = INI_SECTION.match(s)
            if m: sections.append(m.group(1)); continue
            m = INI_KEY.match(s)
            if m: pairs.append(m.group(1))
        return (sections if sections else pairs)[:300]
    if lang == "env":
        for line in text.splitlines():
            s = line.strip()
            if not s or s.startswith("#") or "=" not in s: continue
            keys.append(s.split("=", 1)[0].strip())
        return keys[:300]
    return keys

def collect_lines(path, size):
    """Return (collected_lines, total_lines, first64bytes, header_only, big)."""
    if size > BIG_FILE_BYTES:
        with open(path, "rb") as h:
            first64 = h.read(HASH_BYTES)
        return [], -1, first64, True, True
    header_only = size > HEADER_ONLY_BYTES
    line_cap = SCAN_HEADER_LINES if header_only else MAX_SCAN_LINES
    collected = []
    total_lines = 0
    first64 = bytearray()
    buffered = b""
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(1 << 20)
            if not chunk: break
            if len(first64) < HASH_BYTES:
                first64.extend(chunk[:HASH_BYTES - len(first64)])
            parts = (buffered + chunk).split(b"\n")
            buffered = parts.pop()
            total_lines += len(parts)
            for part in parts:
                if len(collected) >= line_cap: continue
                collected.append(part.decode("utf-8", "replace"))
    if buffered:
        total_lines += 1
        if len(collected) < line_cap:
            collected.append(buffered.decode("utf-8", "replace"))
    return collected, total_lines, bytes(first64), header_only, False

def scan_file(root, rel, full, st):
    name = os.path.basename(full)
    language = language_of(name)
    entry = {
        "path": rel, "name": name, "size": st.st_size,
        "mtime": iso(st.st_mtime), "language": language,
    }
    if os.path.islink(full):
        entry["is_symlink"] = True
        try:
            target = os.path.realpath(full)
            root_real = os.path.realpath(root)
            if not (target == root_real or target.startswith(root_real + os.sep)):
                entry["external_symlink"] = True
        except OSError:
            pass
    if is_secret(rel, name):
        entry.update({
            "secret": True, "lines": SKIP_MARK, "sha256_16": SKIP_MARK,
            "purpose": SKIP_MARK, "imports": [], "defs": [], "keys": [],
            "first_line": SKIP_MARK, "header_only": False, "is_entry": False,
            "in_bin_or_scripts": False, "is_main_file": False,
            "has_shebang": False, "has_main_guard": False, "has_process_argv": False,
        })
        return entry
    ext = os.path.splitext(name.lower())[1]
    if ext in BINARY_EXT:
        entry.update({
            "lines": -1, "sha256_16": "", "purpose": None, "imports": [],
            "defs": [], "keys": [], "first_line": "", "header_only": False,
            "is_entry": False, "in_bin_or_scripts": False, "is_main_file": False,
            "has_shebang": False, "has_main_guard": False, "has_process_argv": False,
            "binary": True,
        })
        try:
            with open(full, "rb") as h:
                first64 = h.read(HASH_BYTES)
            entry["sha256_16"] = hashlib.sha256(first64).hexdigest()[:16] if first64 else ""
        except OSError:
            pass
        return entry
    try:
        collected, total_lines, first64, header_only, big = collect_lines(full, st.st_size)
    except OSError as exc:
        entry["error"] = "ERROR: " + str(exc)
        return entry
    entry["lines"] = BIG_MARK if big else total_lines
    entry["sha256_16"] = hashlib.sha256(first64).hexdigest()[:16] if first64 else ""
    entry["header_only"] = header_only
    if big:
        entry.update({
            "purpose": None, "imports": [], "defs": [], "keys": [],
            "first_line": "", "is_entry": False, "in_bin_or_scripts": False,
            "is_main_file": False, "has_shebang": False,
            "has_main_guard": False, "has_process_argv": False,
        })
        parts = rel.split(os.sep)
        entry["in_bin_or_scripts"] = any(p in ("bin", "scripts") for p in parts[:-1])
        return entry
    header = collected[:HEADER_LINES]
    entry["purpose"] = extract_purpose(header, language)
    entry["first_line"] = next((l for l in header if l.strip()), "")
    entry["has_shebang"] = bool(header) and header[0].startswith("#!")
    body = "\n".join(collected)
    entry["has_main_guard"] = bool(
        re.search(r"^\s*if\s+__name__\s*==\s*['\"]__main__['\"]", body, re.MULTILINE)
    )
    entry["has_process_argv"] = "process.argv" in body
    imports = []
    matcher = IMPORT_MATCHERS.get(language)
    if matcher:
        for line in collected:
            if matcher.search(line):
                imports.append(re.sub(r"\s+$", "", line))
                if len(imports) >= MAX_IMPORTS: break
    entry["imports"] = imports
    defs = []
    patterns = DEF_PATTERNS.get(language)
    if patterns:
        for line in collected:
            for pat in patterns:
                m = pat.match(line)
                if m and m.group(1):
                    defs.append(m.group(1)); break
            if len(defs) >= MAX_DEFS: break
    entry["defs"] = defs
    entry["keys"] = extract_keys(language, body) if language in CONFIG_LANGS else []
    parts = rel.split(os.sep)
    entry["in_bin_or_scripts"] = any(p in ("bin", "scripts") for p in parts[:-1])
    entry["is_main_file"] = name in ("main.py", "__main__.py")
    entry["is_entry"] = bool(
        entry["has_shebang"] or entry["has_main_guard"] or entry["has_process_argv"]
    )
    return entry

def walk_root(label, root):
    import stat as statmod
    info = {"label": label, "root": root, "files": [], "errors": [],
            "dirs": 0, "empty_dirs": [], "skipped_mounts": [],
            "exists": os.path.isdir(root)}
    if not info["exists"]:
        info["error"] = "ERROR: root does not exist"
        return info
    def onerror(err):
        info["errors"].append({"path": getattr(err, "filename", ""), "error": "ERROR: " + str(err)})
    for dirpath, dirnames, filenames in os.walk(root, topdown=True, onerror=onerror, followlinks=False):
        rel_dir = os.path.relpath(dirpath, root)
        if rel_dir != "." and os.path.ismount(dirpath):
            info["skipped_mounts"].append(rel_dir); dirnames[:] = []; continue
        info["dirs"] += 1
        kept = [d for d in sorted(dirnames) if d not in PRUNE_DIRS]
        dirnames[:] = kept
        visible = sorted(filenames)
        if not kept and not visible:
            info["empty_dirs"].append(rel_dir)
        for name in visible:
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root)
            try: st = os.lstat(full)
            except OSError as exc:
                info["errors"].append({"path": rel, "error": "ERROR: " + str(exc)}); continue
            if statmod.S_ISDIR(st.st_mode): continue
            if not (statmod.S_ISREG(st.st_mode) or statmod.S_ISLNK(st.st_mode)):
                info["errors"].append({"path": rel, "error": "ERROR: not a regular file"}); continue
            try:
                info["files"].append(scan_file(root, rel, full, st))
            except OSError as exc:
                info["errors"].append({"path": rel, "error": "ERROR: " + str(exc)})
            except Exception as exc:
                info["errors"].append({"path": rel, "error": "ERROR: " + type(exc).__name__ + ": " + str(exc)})
    return info

def main():
    started = datetime.datetime.now().astimezone().isoformat()
    out = {"generated": started, "roots": {}, "constants": {
        "header_lines": HEADER_LINES, "hash_bytes": HASH_BYTES,
        "header_only_bytes": HEADER_ONLY_BYTES, "big_file_bytes": BIG_FILE_BYTES,
        "max_imports": MAX_IMPORTS, "max_defs": MAX_DEFS, "max_scan_lines": MAX_SCAN_LINES,
        "prune_dirs": sorted(PRUNE_DIRS),
    }}
    for label, root in ROOTS:
        sys.stderr.write("walking %s %s\n" % (label, root)); sys.stderr.flush()
        info = walk_root(label, root)
        out["roots"][label] = info
        sys.stderr.write("  %s: %d files, %d errors\n" % (label, len(info["files"]), len(info["errors"])))
        sys.stderr.flush()
        with open("/tmp/sixth_data_partial.json", "w") as h: json.dump(out, h)
    with open("/tmp/sixth_data.json", "w") as h: json.dump(out, h)
    sys.stderr.write("wrote /tmp/sixth_data.json\n"); sys.stderr.flush()

if __name__ == "__main__":
    main()
WALKER_EOF

cat > "$DIR/sixth_render.py" << 'RENDER_EOF'
#!/usr/bin/env python3
# Builds sixth_output artifacts from /tmp/sixth_data.json (read-only over the roots).
import os, re, sys, json

DATA_PATH = os.environ.get("SIXTH_DATA", "/tmp/sixth_data.json")
OUT_DIR = os.environ.get("SIXTH_OUT", "/media/kark/EGREGORE/egregore/tools/sixth_output")
SPEC_DIR = os.path.join(OUT_DIR, "spec")

SOURCE_LANGS = {
    "python", "javascript", "typescript", "shell", "go", "rust", "java", "c", "cpp",
    "ruby", "php", "perl", "lua", "swift", "kotlin", "csharp", "sql", "vue", "svelte",
    "html", "css", "scss", "less", "r", "julia", "dart", "elixir", "terraform", "hcl",
    "powershell", "batch", "graphql", "protobuf", "systemd",
}
PACKAGE_FILES = {"__init__.py", "index.js", "index.ts", "index.tsx", "index.mjs",
                 "index.cjs", "mod.rs", "index.jsx", "main.go"}
DOTTED_LANGS = {"python", "java", "kotlin", "perl", "csharp", "swift", "elixir", "go", "rust"}
QUOTED_RE = re.compile(r"""['"]([^'"]+)['"]""")

def extract_specifiers(lang, line):
    text = line.strip()
    if lang == "python":
        m = re.match(r"^from\s+([\.\w]+)\s+import\s+(.+)$", text)
        if m:
            module = m.group(1)
            if module.strip(".") == "":
                names = []
                for part in m.group(2).split(","):
                    name = part.strip().split(" as ")[0].strip().strip("()").split(".")[0]
                    if name: names.append(name)
                return names
            return [module]
        m = re.match(r"^import\s+(.+)$", text)
        if m:
            names = []
            for part in m.group(1).split(","):
                name = part.strip().split(" as ")[0].strip()
                if name: names.append(name)
            return names
        return []
    if lang in ("javascript", "typescript", "vue", "svelte", "dart"):
        for pattern in (r"""from\s*['"]([^'"]+)['"]""",
                        r"""import\s*\(\s*['"]([^'"]+)['"]""",
                        r"""(?:require|import)\s*\(\s*['"]([^'"]+)['"]""",
                        r"""^\s*import\s+['"]([^'"]+)['"]""",
                        r"""^\s*(?:import|export|part)\s+['"]([^'"]+)['"]"""):
            m = re.search(pattern, text)
            if m: return [m.group(1)]
        return []
    if lang in ("c", "cpp"):
        m = re.search(r"""^\s*#\s*include\s*[<"]([^>"]+)[>"]""", text)
        return [m.group(1)] if m else []
    if lang == "shell":
        m = re.match(r"^\s*(?:source|\.)\s+(\S+)", text)
        return [m.group(1)] if m else []
    if lang in ("ruby", "php", "lua", "protobuf", "graphql"):
        m = QUOTED_RE.search(text)
        if m: return [m.group(1)]
        m = re.search(r"^\s*require\s+([\w\.\-]+)", text)
        return [m.group(1)] if m else []
    if lang == "perl":
        m = re.search(r"^\s*(?:use|require)\s+([\w:]+)", text)
        return [m.group(1)] if m else []
    if lang == "go":
        m = QUOTED_RE.search(text)
        return [m.group(1)] if m else []
    if lang == "rust":
        m = re.search(r"^\s*use\s+([\w:]+)", text)
        if m: return [m.group(1)]
        m = re.search(r"^\s*(?:pub\s+)?mod\s+(\w+)", text)
        return [m.group(1)] if m else []
    if lang in ("java", "kotlin", "swift", "csharp"):
        m = re.search(r"^\s*(?:import|using)\s+(?:static\s+)?([\w\.\*]+)", text)
        return [m.group(1)] if m else []
    if lang == "elixir":
        m = re.search(r"""^\s*(?:alias|import|require|use)\s+([\w\.]+)""", text)
        return [m.group(1)] if m else []
    if lang in ("html", "vue", "svelte"):
        m = re.search(r"""(?:src|href)\s*=\s*['"]([^'"]+)['"]""", text)
        return [m.group(1)] if m else []
    if lang in ("css", "scss", "less"):
        m = re.search(r"""@(?:import|use|forward)\s+(?:url\()?\s*['"]([^'"]+)['"]""", text)
        return [m.group(1)] if m else []
    if lang in ("terraform", "hcl"):
        m = re.search(r"""^\s*source\s*=\s*['"]([^'"]+)['"]""", text)
        return [m.group(1)] if m else []
    if lang == "powershell":
        m = re.search(r"""^\s*\.\s+['"]?([\w\\/\.\-:]+)""", text)
        return [m.group(1)] if m else []
    return []

def last_component(spec, lang):
    cleaned = spec.strip().strip("'\"")
    cleaned = cleaned.split("?")[0].split("#")[0]
    if cleaned.startswith("file://"): cleaned = cleaned[7:]
    if cleaned.startswith("node:") or cleaned.startswith("http://") or cleaned.startswith("https://"):
        return re.split(r"[/:]", cleaned)[-1]
    if "/" in cleaned or "\\" in cleaned:
        cleaned = re.split(r"[\\/]", cleaned)[-1]
        stem = os.path.splitext(cleaned)[0]
        return stem if stem else cleaned
    if "::" in cleaned: cleaned = cleaned.split("::")[-1]
    if lang in DOTTED_LANGS and "." in cleaned:
        cleaned = cleaned.split(".")[-1]
    stem = os.path.splitext(cleaned)[0]
    return stem if stem else cleaned

def build_index(files):
    by_name, by_stem, package_index = {}, {}, {}
    for item in files:
        rel, name = item["path"], item["name"]
        by_name.setdefault(name, []).append(rel)
        stem = os.path.splitext(name)[0]
        by_stem.setdefault(stem, []).append(rel)
        if name in PACKAGE_FILES:
            parent = os.path.basename(os.path.dirname(rel))
            if parent: package_index.setdefault(parent, []).append(rel)
    return by_name, by_stem, package_index

def choose_candidate(candidates, spec):
    ordered = sorted(candidates, key=lambda p: (len(p.split(os.sep)), p))
    implied = spec.replace("\\", "/").strip("'\"")
    if "/" in implied:
        prefix = implied.rsplit("/", 1)[0]
        scored = []
        for c in ordered:
            directory = os.path.dirname(c).replace(os.sep, "/")
            if directory and (directory.endswith(prefix) or prefix.endswith(directory)):
                scored.append((len(prefix), c))
        if scored:
            scored.sort(key=lambda p: (-p[0], p[1]))
            return scored[0][1]
    return ordered[0]

def resolve_imports(label, files):
    by_name, by_stem, package_index = build_index(files)
    present = {item["path"] for item in files}
    edges, unresolved = [], []
    for item in files:
        importer = item["path"]
        for raw in item.get("imports", []):
            for spec in extract_specifiers(item["language"], raw):
                component = last_component(spec, item["language"])
                if not component: continue
                candidates = by_name.get(component) or by_stem.get(component) or package_index.get(component)
                if not candidates:
                    unresolved.append({"name": component, "raw": raw,
                                       "importer": importer, "specifier": spec}); continue
                target = choose_candidate(candidates, spec)
                if target not in present:
                    unresolved.append({"name": component, "raw": raw,
                                       "importer": importer, "specifier": spec}); continue
                edges.append({"from": importer, "to": target, "import": raw,
                              "specifier": spec, "component": component,
                              "ambiguous": len(candidates) > 1})
    return edges, unresolved

def clean_field(value):
    if value is None: return "none"
    return str(value).replace("\t", " ").replace("\n", " ").replace("\r", " ")

def write_tsv(label, files, errors):
    rows = []
    for item in files:
        rows.append([clean_field(item["path"]), clean_field(item["size"]),
                     clean_field(item["mtime"]), clean_field(item["lines"]),
                     clean_field(item["language"]), clean_field(item["sha256_16"]),
                     "true" if item.get("is_entry") else "false",
                     clean_field(item.get("purpose") or "none")])
    for err in errors:
        m = clean_field(err.get("error", "ERROR: unknown"))
        rows.append([clean_field(err.get("path", "")), m, m, m, m, m, "false", m])
    rows.sort(key=lambda row: row[0])
    with open(os.path.join(OUT_DIR, "%s.files.tsv" % label), "w") as h:
        for row in rows: h.write("\t".join(row) + "\n")
    return len(rows)

def spec_filename(rel, used):
    base = rel.replace(os.sep, "__").replace("/", "__") or "__root__"
    candidate = base + ".md"
    if candidate not in used:
        used.add(candidate); return candidate
    counter = 2
    while True:
        alt = "%s-%d.md" % (base, counter)
        if alt not in used:
            used.add(alt); return alt
        counter += 1

def write_specs(label, files):
    directory = os.path.join(SPEC_DIR, label)
    os.makedirs(directory, exist_ok=True)
    used = set(); written = 0
    for item in files:
        name = spec_filename(item["path"], used)
        lines = ["# %s" % item["path"], "",
                 "- size: %s bytes" % item["size"],
                 "- mtime: %s" % item["mtime"],
                 "- lines: %s" % clean_field(item["lines"]),
                 "- sha256[:16]: %s" % clean_field(item["sha256_16"]),
                 "- language: %s" % item["language"],
                 "- is_entry: %s" % ("true" if item.get("is_entry") else "false"),
                 "- purpose: %s" % clean_field(item.get("purpose") or "none"),
                 "", "## first line", "", item.get("first_line") or "none",
                 "", "## imports", ""]
        imports = item.get("imports", [])
        lines += (["- none"] if not imports else ["- `%s`" % e.replace("`", "'") for e in imports])
        lines += ["", "## defs", ""]
        defs = item.get("defs", [])
        lines += (["- none"] if not defs else ["- `%s`" % e.replace("`", "'") for e in defs])
        keys = item.get("keys", [])
        if keys:
            lines += ["", "## keys", ""]
            lines += ["- `%s`" % str(e).replace("`", "'") for e in keys]
        lines += [""]
        with open(os.path.join(directory, name), "w") as h: h.write("\n".join(lines))
        written += 1
    return written

def group_unresolved(unresolved):
    grouped = {}
    for entry in unresolved:
        grouped.setdefault(entry["name"], []).append(entry)
    return grouped

def summary_for_root(label, info, edges, unresolved, secrets):
    files = info["files"]
    inbound = {}
    for edge in edges:
        inbound.setdefault(edge["to"], set()).add(edge["from"])
    for item in files:
        if item.get("in_bin_or_scripts") and not inbound.get(item["path"]):
            item["is_entry"] = True
    source_files = [i for i in files if i["language"] in SOURCE_LANGS]
    entries = [i["path"] for i in files if i.get("is_entry")]
    hubs = sorted([(p, len(s)) for p, s in inbound.items() if len(s) >= 3],
                  key=lambda x: (-x[1], x[0]))
    undocumented = sorted(i["path"] for i in source_files if not i.get("purpose"))
    orphans = sorted(i["path"] for i in source_files
                     if not inbound.get(i["path"]) and not i.get("is_entry")
                     and not i.get("in_bin_or_scripts") and not i.get("is_main_file"))
    ext_counts = {}
    for item in files:
        ext = os.path.splitext(item["name"])[1].lower() or "(none)"
        rec = ext_counts.setdefault(ext, [0, 0])
        rec[0] += 1
        if isinstance(item["size"], int): rec[1] += item["size"]
    header_only = sorted(i["path"] for i in files if i.get("header_only"))
    external_symlinks = sorted(i["path"] for i in files if i.get("external_symlink"))
    grouped = group_unresolved(unresolved)
    out = ["## Root %s" % label, "",
           "- root: `%s`" % info["root"],
           "- exists: %s" % ("true" if info.get("exists") else "false"),
           "- total files: %d" % len(files),
           "- total source files: %d" % len(source_files),
           "- directories walked: %d" % info.get("dirs", 0),
           "- imports resolved: %d" % len(edges),
           "- imports unresolved: %d" % len(unresolved),
           "- secret-bearing files recorded (not opened): %d" % len(secrets), ""]
    if info.get("error"):
        out += ["ERROR: %s" % info["error"], ""]
    out += ["### counts by extension", "", "| extension | files | bytes |", "| --- | --- | --- |"]
    for ext in sorted(ext_counts):
        rec = ext_counts[ext]
        out.append("| %s | %d | %d |" % (ext, rec[0], rec[1]))
    out += ["", "### entry points (%d)" % len(entries), ""]
    out += (["- none"] if not entries else ["- `%s`" % p for p in entries])
    out += ["", "### hub files, imported by 3+ files (%d)" % len(hubs), ""]
    out += (["- none"] if not hubs else ["- `%s` — imported by %d files" % (p, n) for p, n in hubs])
    out += ["", "### source files with no header (%d)" % len(undocumented), ""]
    out += (["- none"] if not undocumented else ["- `%s`" % p for p in undocumented])
    out += ["", "### orphan source files (%d)" % len(orphans), ""]
    out += (["- none"] if not orphans else ["- `%s`" % p for p in orphans])
    out += ["", "### unresolved imports, grouped by name (%d distinct)" % len(grouped), ""]
    if not grouped:
        out.append("- none")
    else:
        for name in sorted(grouped):
            items = grouped[name]
            out.append("- `%s` — %d reference(s)" % (name, len(items)))
            for entry in items[:200]:
                out.append("  - `%s` in `%s`" % (entry["raw"].replace("`", "'"), entry["importer"]))
            if len(items) > 200:
                out.append("  - %d more reference(s)" % (len(items) - 200))
    out += ["", "### scanned header only (%d)" % len(header_only), ""]
    out += (["- none"] if not header_only else ["- `%s`" % p for p in header_only])
    out += ["", "### secret-bearing paths, not opened (%d)" % len(secrets), ""]
    out += (["- none"] if not secrets else ["- `%s`" % p for p in secrets])
    out += ["", "### external symlinks, target outside root (%d)" % len(external_symlinks), ""]
    out += (["- none"] if not external_symlinks else ["- `%s`" % p for p in external_symlinks])
    empty_dirs = info.get("empty_dirs", [])
    out += ["", "### empty directories (%d)" % len(empty_dirs), ""]
    out += (["- none"] if not empty_dirs else ["- `%s`" % p for p in empty_dirs])
    skipped_mounts = info.get("skipped_mounts", [])
    out += ["", "### skipped mount points (%d)" % len(skipped_mounts), ""]
    out += (["- none"] if not skipped_mounts else ["- `%s`" % p for p in skipped_mounts])
    errors = info.get("errors", [])
    out += ["", "### errors (%d)" % len(errors), ""]
    out += (["- none"] if not errors else ["- `%s` — %s" % (e.get("path", ""), e.get("error", "")) for e in errors])
    out += [""]
    return "\n".join(out)

UNIT_FILTER = ("egregore", "anchorum", "blackstar", "socia")
UNIT_SUFFIXES = (".service", ".timer", ".target")
PATH_KEYS = ("ExecStart", "ExecStartPre", "ExecStartPost", "ExecReload", "ExecStop",
             "ExecStopPost", "WorkingDirectory", "EnvironmentFile", "ReadWritePaths",
             "ReadOnlyPaths", "InaccessiblePaths", "RootDirectory", "PIDFile",
             "RuntimeDirectory", "StateDirectory", "LogsDirectory", "CacheDirectory",
             "ConfigurationDirectory", "StandardOutput", "StandardError")

def parse_unit(path):
    parsed = {"directives": {}, "sections": [], "error": None}
    try:
        with open(path, "r", errors="replace") as h: raw = h.read()
    except OSError as exc:
        parsed["error"] = "ERROR: " + str(exc); return parsed
    section = ""; pending = ""
    for line in raw.splitlines():
        if pending:
            line = pending + line; pending = ""
        if line.endswith("\\"):
            pending = line[:-1]; continue
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith(";"): continue
        if stripped.startswith("["):
            section = stripped.strip("[]")
            if section not in parsed["sections"]: parsed["sections"].append(section)
            continue
        if "=" not in stripped: continue
        k, v = stripped.split("=", 1)
        parsed["directives"].setdefault(k.strip(), []).append(v.strip())
    return parsed

def first_directive(d, key):
    v = d.get(key)
    return v[0] if v else None

def environment_names(d):
    names = []
    for value in d.get("Environment", []):
        for token in value.split():
            token = token.strip().strip('"\'')
            if not token: continue
            names.append(token.split("=", 1)[0] if "=" in token else token)
    return names

def referenced_paths(d):
    paths = []
    for key in PATH_KEYS:
        for value in d.get(key, []):
            for token in value.split():
                token = token.strip().strip('"\'')
                if "://" in token: continue
                raw = token.lstrip("-=")
                candidates = []
                if raw.startswith("/"): candidates.append(raw)
                for piece in raw.split(":"):
                    if piece.startswith("/"): candidates.append(piece)
                for c in candidates:
                    c = c.rstrip(";,)\"'")
                    if not c or c == "/": continue
                    if c not in paths: paths.append(c)
    return paths

def write_systemd_units():
    base = "/etc/systemd/system"
    out = ["# systemd units", "", "- search base: `%s`" % base, ""]
    found = []
    if not os.path.isdir(base):
        out.append("ERROR: %s does not exist" % base)
    else:
        for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
            for name in filenames:
                if not name.endswith(UNIT_SUFFIXES): continue
                lower = name.lower()
                if any(t in lower for t in UNIT_FILTER):
                    found.append(os.path.join(dirpath, name))
    found.sort()
    out.append("- units found: %d" % len(found))
    out.append("")
    for path in found:
        parsed = parse_unit(path)
        d = parsed["directives"]
        out.append("## %s" % os.path.basename(path))
        out.append("")
        out.append("- path: %s" % path)
        out.append("- symlink: %s" % ("true" if os.path.islink(path) else "false"))
        if os.path.islink(path): out.append("- symlink target: %s" % os.readlink(path))
        out.append("- exists: %s" % ("true" if os.path.exists(path) else "false"))
        if parsed["error"]:
            out += ["- ERROR: %s" % parsed["error"], ""]; continue
        out.append("- sections: %s" % (", ".join(parsed["sections"]) or "none"))
        out.append("- Description: %s" % (first_directive(d, "Description") or "absent"))
        out.append("- WorkingDirectory: %s" % (first_directive(d, "WorkingDirectory") or "absent"))
        out.append("- ExecStart: %s" % (first_directive(d, "ExecStart") or "absent"))
        out.append("- User: %s" % (first_directive(d, "User") or "absent"))
        names = environment_names(d)
        out.append("- Environment: %s" % (", ".join(names) or "absent"))
        out.append("")
        existing, missing = [], []
        for p in referenced_paths(d):
            (existing if os.path.exists(p) else missing).append(p)
        out.append("Referenced paths that exist: %s" % (", ".join(existing) or "none"))
        out.append("")
        out.append("Referenced paths that do not exist: %s" % (", ".join(missing) or "none"))
        out.append("")
    with open(os.path.join(OUT_DIR, "systemd-units.md"), "w") as h:
        h.write("\n".join(out))

def main():
    with open(DATA_PATH) as h: data = json.load(h)
    os.makedirs(OUT_DIR, exist_ok=True)
    os.makedirs(SPEC_DIR, exist_ok=True)
    graph = {"generated": data.get("generated"), "roots": {}}
    summary_sections = []
    header = ["# SUMMARY", "",
              "- generated: %s" % data.get("generated"),
              "- roots: A, B, C, D, E, F, G",
              "- import resolution: within a root, by last path component only",
              "- artifacts: SUMMARY.md, graph.json, <ROOT>.files.tsv, spec/<ROOT>/<file>.md, systemd-units.md",
              ""]
    for label in ["A", "B", "C", "D", "E", "F", "G"]:
        info = data["roots"].get(label)
        if info is None:
            summary_sections.append("## Root %s\n\nERROR: root was not walked\n" % label); continue
        info["constants"] = data.get("constants", {})
        files = info["files"]
        edges, unresolved = resolve_imports(label, files)
        inbound = {}
        for edge in edges:
            inbound.setdefault(edge["to"], set()).add(edge["from"])
        for item in files:
            if item.get("in_bin_or_scripts") and not inbound.get(item["path"]):
                item["is_entry"] = True
        graph["roots"][label] = {"root": info["root"],
                                 "exists": info.get("exists", False),
                                 "error": info.get("error"),
                                 "edge_count": len(edges),
                                 "unresolved_count": len(unresolved),
                                 "edges": edges, "unresolved": unresolved}
        secrets = sorted(i["path"] for i in files if i.get("secret"))
        section = summary_for_root(label, info, edges, unresolved, secrets)
        summary_sections.append(section)
        with open(os.path.join(OUT_DIR, "SUMMARY-%s.md" % label), "w") as h: h.write(section)
        write_tsv(label, files, info.get("errors", []))
        write_specs(label, files)
        sys.stderr.write("built %s: %d files, %d edges, %d unresolved\n" %
                         (label, len(files), len(edges), len(unresolved))); sys.stderr.flush()
    with open(os.path.join(OUT_DIR, "graph.json"), "w") as h:
        json.dump(graph, h, indent=1, sort_keys=False)
    with open(os.path.join(OUT_DIR, "SUMMARY.md"), "w") as h:
        h.write("\n".join(header) + "\n" + "\n".join(summary_sections))
    write_systemd_units()
    sys.stderr.write("artifacts written to %s\n" % OUT_DIR); sys.stderr.flush()

if __name__ == "__main__":
    main()
RENDER_EOF

cat > "$DIR/sixth_audit.py" << 'AUDIT_EOF'
#!/usr/bin/env python3
# Verifies that renderer output is consistent with walker JSON.
import json, os, sys

OUT_DIR = os.environ.get("SIXTH_OUT", "/media/kark/EGREGORE/egregore/tools/sixth_output")
DATA_PATH = os.environ.get("SIXTH_DATA", "/tmp/sixth_data.json")
SPEC_DIR = os.path.join(OUT_DIR, "spec")
REPORT = os.environ.get("SIXTH_AUDIT", "/tmp/sixth_audit.txt")

try:
    with open(DATA_PATH) as h: data = json.load(h)
except Exception as exc:
    print("ERROR: cannot read %s: %s" % (DATA_PATH, exc)); sys.exit(2)

if "roots" not in data or not isinstance(data["roots"], dict):
    print("ERROR: %s does not have a roots dict" % DATA_PATH); sys.exit(2)

report = []

def spec_filename(rel, used):
    base = rel.replace(os.sep, "__").replace("/", "__") or "__root__"
    candidate = base + ".md"
    if candidate not in used:
        used.add(candidate); return candidate
    counter = 2
    while True:
        alt = "%s-%d.md" % (base, counter)
        if alt not in used:
            used.add(alt); return alt
        counter += 1

for label in ["A", "B", "C", "D", "E", "F", "G"]:
    info = data["roots"].get(label)
    if info is None:
        report.append("%s: root not in JSON" % label); continue
    if "files" not in info:
        report.append("%s: files key missing" % label); continue
    files = info["files"]
    directory = os.path.join(SPEC_DIR, label)
    entries = os.listdir(directory) if os.path.isdir(directory) else []
    entry_set = set(entries)
    used = set(); missing = []
    for item in files:
        name = spec_filename(item["path"], used)
        if name not in entry_set:
            missing.append((item["path"], name))
    tsv_path = os.path.join(OUT_DIR, "%s.files.tsv" % label)
    tsv_rows = 0
    if os.path.exists(tsv_path):
        with open(tsv_path, "rb") as h: tsv_rows = h.read().count(b"\n")
    report.append("%s files=%d specs_on_disk=%d tsv_rows=%d errors=%d missing=%d" %
                  (label, len(files), len(entries), tsv_rows, len(info.get("errors", [])), len(missing)))
    for path, name in missing[:5]:
        report.append("   MISSING spec %s -> %s (len name=%d)" % (path, name, len(name)))
    longest = 0; longest_path = ""
    for item in files:
        candidate = item["path"].replace(os.sep, "__").replace("/", "__") + ".md"
        if len(candidate.encode("utf-8", "replace")) > longest:
            longest = len(candidate.encode("utf-8", "replace")); longest_path = item["path"]
    if longest > 255:
        report.append("   !! longest spec name exceeds 255 bytes: %d for %s" % (longest, longest_path))
    else:
        report.append("   longest spec name: %d bytes for %s" % (longest, longest_path))

graph_path = os.path.join(OUT_DIR, "graph.json")
report.append("")
report.append("graph.json exists=%s size=%d" % (os.path.exists(graph_path),
              os.path.getsize(graph_path) if os.path.exists(graph_path) else 0))
if os.path.exists(graph_path):
    try:
        with open(graph_path) as h: graph = json.load(h)
        for label in ["A", "B", "C", "D", "E", "F", "G"]:
            node = graph.get("roots", {}).get(label)
            if node:
                report.append("graph %s edges=%d unresolved=%d" % (
                    label, node.get("edge_count", 0), node.get("unresolved_count", 0)))
    except Exception as exc:
        report.append("graph.json unreadable: %s" % exc)

for name in ("SUMMARY.md", "systemd-units.md"):
    path = os.path.join(OUT_DIR, name)
    report.append("%s exists=%s size=%d" % (name, os.path.exists(path),
                  os.path.getsize(path) if os.path.exists(path) else 0))

with open(REPORT, "w") as h: h.write("\n".join(report) + "\n")
print("audit done -> %s" % REPORT)
AUDIT_EOF

chmod +x "$DIR"/sixth_*.py
echo
echo "── compile check ──"
python3 -m py_compile "$DIR/sixth_walker.py" "$DIR/sixth_render.py" "$DIR/sixth_audit.py" && echo "all three compile"
echo
echo "── sizes ──"
wc -l "$DIR"/sixth_*.py