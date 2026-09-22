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
        entry.update({
            "error": "ERROR: " + str(exc),
            "lines": -1, "sha256_16": "", "purpose": None, "imports": [],
            "defs": [], "keys": [], "first_line": "", "header_only": False,
            "is_entry": False, "in_bin_or_scripts": False, "is_main_file": False,
            "has_shebang": False, "has_main_guard": False, "has_process_argv": False,
        })
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
