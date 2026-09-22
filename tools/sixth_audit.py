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
