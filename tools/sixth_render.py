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
        rows.append([
            clean_field(item.get("path", "")),
            clean_field(item.get("size", 0)),
            clean_field(item.get("mtime", "")),
            clean_field(item.get("lines", -1)),
            clean_field(item.get("language", "unknown")),
            clean_field(item.get("sha256_16", "")),
            "true" if item.get("is_entry") else "false",
            clean_field(item.get("purpose") or "none"),
        ])
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
        rel = item.get("path", "")
        name = spec_filename(rel, used)
        lines = ["# %s" % rel, "",
                 "- size: %s bytes" % item.get("size", 0),
                 "- mtime: %s" % item.get("mtime", ""),
                 "- lines: %s" % clean_field(item.get("lines", -1)),
                 "- sha256[:16]: %s" % clean_field(item.get("sha256_16", "")),
                 "- language: %s" % item.get("language", "unknown"),
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
