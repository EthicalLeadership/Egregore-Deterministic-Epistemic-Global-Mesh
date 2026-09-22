#!/usr/bin/env node
// verify-ledger-exact.mjs
//
// Exact verifier for Egregore JSONL ledgers written by src/ledger.mjs.
//
// ASSUMED WRITER FORMULA (see src/ledger.mjs):
//
//     const payloadWithoutHash = JSON.stringify(record) + prev;
//     record.hash = sha256(payloadWithoutHash);
//
// Exit codes:
//   0  clean          internal consistency proven under the stated formula
//   1  integrity fail chain break, hash mismatch, seq gap, arm mismatch
//   2  usage error    bad arguments, missing file
//   3  unverifiable   not the ledger this verifier was built for
//
// Usage:
//   node verify-ledger-exact.mjs <ledger.jsonl>
//        [--compare <other.jsonl>]
//        [--arm <name>]
//        [--writer <path/to/src/ledger.mjs>]
//        [--anchor <anchor.json>]
//   node verify-ledger-exact.mjs --selftest

import fs from "node:fs";
import os from "node:os";
import crypto from "node:crypto";
import path from "node:path";

const GENESIS     = "0".repeat(64);
const HASH_RE     = /^[0-9a-f]{64}$/;
const ISO_8601_MS = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/;
const REQUIRED_FIELDS = ["hash", "prev_hash", "arm", "seq", "timestamp"];
const SHA256_EMPTY =
    "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855";

function writerHash(recordWithoutHash, prevHash) {
    const payload = JSON.stringify(recordWithoutHash) + prevHash;
    return crypto.createHash("sha256").update(payload, "utf8").digest("hex");
}

function sourceContainsExpectedShape(writerPath) {
    if (!writerPath) return { status: "unchecked" };
    if (!fs.existsSync(writerPath)) return { status: "missing", reason: "file not found" };
    let src;
    try { src = fs.readFileSync(writerPath, "utf8"); }
    catch (e) { return { status: "missing", reason: e.message }; }
    const patterns = [
        { name: "payload construction",
          re: /const\s+payloadWithoutHash\s*=\s*JSON\.stringify\(record\)\s*\+\s*prev\s*;/ },
        { name: "hash assignment",
          re: /record\.hash\s*=\s*sha256\(payloadWithoutHash\)\s*;/ },
    ];
    const matched = patterns.map(p => ({ name: p.name, found: p.re.test(src) }));
    const allFound = matched.every(m => m.found);
    return { status: allFound ? "shape-found" : "shape-missing", matched };
}

function readLedger(file) {
    const raw = fs.readFileSync(file, "utf8");
    const lines = raw.split(/\r\n|\n|\r/);
    while (lines.length && lines[lines.length - 1].trim() === "") lines.pop();
    return lines;
}

function validateShape(records) {
    for (let i = 0; i < records.length; i++) {
        const r = records[i];
        for (const f of REQUIRED_FIELDS) {
            if (!(f in r)) return { ok: false, at: i, field: f, reason: `required field '${f}' missing` };
        }
        if (typeof r.hash !== "string" || !HASH_RE.test(r.hash)) {
            return { ok: false, at: i, field: "hash", value: r.hash, reason: "hash is not 64 lowercase hex characters" };
        }
        const isGenesis = (i === 0 && r.prev_hash === GENESIS);
        if (!isGenesis && (typeof r.prev_hash !== "string" || !HASH_RE.test(r.prev_hash))) {
            return { ok: false, at: i, field: "prev_hash", value: r.prev_hash, reason: "prev_hash is not 64 lowercase hex characters" };
        }
        if (typeof r.timestamp !== "string" || !ISO_8601_MS.test(r.timestamp)) {
            return { ok: false, at: i, field: "timestamp", value: r.timestamp, reason: "timestamp is not ISO 8601 with milliseconds and Z" };
        }
        if (typeof r.seq !== "number" || !Number.isInteger(r.seq)) {
            return { ok: false, at: i, field: "seq", value: r.seq, reason: "seq is not an integer" };
        }
        if (typeof r.arm !== "string" || r.arm.length === 0) {
            return { ok: false, at: i, field: "arm", value: r.arm, reason: "arm is not a non-empty string" };
        }
    }
    return { ok: true };
}

function checkGenesis(records) {
    if (records[0].prev_hash !== GENESIS) {
        return { ok: false, reason: `first prev_hash is ${JSON.stringify(records[0].prev_hash)}; writer defines genesis as ${JSON.stringify(GENESIS)}` };
    }
    return { ok: true };
}

function checkChain(records) {
    for (let i = 1; i < records.length; i++) {
        if (records[i].prev_hash !== records[i - 1].hash) {
            return { ok: false, at: i, stored: records[i].prev_hash, expected: records[i - 1].hash };
        }
    }
    return { ok: true };
}

function checkSeq(records) {
    for (let i = 0; i < records.length; i++) {
        if (records[i].seq !== i + 1) return { ok: false, at: i, value: records[i].seq, expected: i + 1 };
    }
    return { ok: true };
}

function checkArm(records, expected) {
    for (let i = 0; i < records.length; i++) {
        if (records[i].arm !== expected) return { ok: false, at: i, value: records[i].arm, expected };
    }
    return { ok: true };
}

function checkTimestampMonotonic(records) {
    let last = -Infinity;
    for (let i = 0; i < records.length; i++) {
        const t = Date.parse(records[i].timestamp);
        if (!Number.isFinite(t)) return { ok: false, at: i, reason: "timestamp not parseable" };
        if (t < last) return { ok: false, at: i, reason: `timestamp goes backwards: ${records[i].timestamp} after ${records[i-1]?.timestamp}` };
        last = t;
    }
    return { ok: true };
}

function checkSelfHashes(records) {
    const failures = [];
    for (let i = 0; i < records.length; i++) {
        const r = records[i];
        const { hash, ...rest } = r;
        const computed = writerHash(rest, r.prev_hash);
        if (computed !== hash) failures.push({ at: i, stored: hash, computed });
    }
    return failures;
}

function checkAnchor(records, anchorPath) {
    if (!anchorPath) return { ok: true, present: false };
    if (!fs.existsSync(anchorPath)) return { ok: false, reason: `anchor file not found: ${anchorPath}` };
    let anchor;
    try { anchor = JSON.parse(fs.readFileSync(anchorPath, "utf8")); }
    catch (e) { return { ok: false, reason: `anchor not valid JSON: ${e.message}` }; }
    if (typeof anchor.seq !== "number" || typeof anchor.hash !== "string") {
        return { ok: false, reason: "anchor must have numeric 'seq' and string 'hash'" };
    }
    const rec = records.find(r => r.seq === anchor.seq);
    if (!rec) return { ok: false, reason: `no record with seq=${anchor.seq}` };
    if (rec.hash !== anchor.hash) {
        return { ok: false, reason: `record seq=${anchor.seq} hash is ${rec.hash}; anchor says ${anchor.hash}` };
    }
    return { ok: true, present: true, seq: anchor.seq };
}

function buildSeqIndex(records) {
    const m = new Map();
    for (const r of records) if (typeof r.seq === "number") m.set(r.seq, r);
    return m;
}

function fieldDiff(a, b) {
    const skip = new Set(["hash", "seq"]);
    const keys = new Set([...Object.keys(a), ...Object.keys(b)]);
    const out = [];
    for (const k of keys) {
        if (skip.has(k)) continue;
        const av = JSON.stringify(a[k]);
        const bv = JSON.stringify(b[k]);
        if (av !== bv) out.push({ field: k, before: bv, after: av });
    }
    return out;
}

function verifyFile(file, opts = {}) {
    const report = [];
    const log  = (s = "") => report.push(s);
    const elog = (s = "") => report.push(s);

    if (!fs.existsSync(file)) {
        elog(`not found: ${file}`);
        return { exit: 2, report };
    }

    log("══════════════════════════════════════════════════════════════");
    log(" VERIFIER ASSUMPTIONS");
    log("══════════════════════════════════════════════════════════════");
    log(" formula:  sha256(JSON.stringify(record_without_hash) + prev_hash)");

    if (opts.writerPath) {
        const shape = sourceContainsExpectedShape(opts.writerPath);
        log(` writer:   ${opts.writerPath}`);
        if (shape.status === "shape-found") {
            for (const m of shape.matched) log(`   ✓ ${m.name}`);
        } else if (shape.status === "shape-missing") {
            log(`   ✗ source shape not found`);
            for (const m of shape.matched) log(`     ${m.found ? "✓" : "✗"} ${m.name}`);
        } else {
            log(`   (${shape.reason})`);
        }
        if (shape.status === "shape-missing" || shape.status === "missing") {
            elog();
            elog("Cannot proceed: writer source does not contain the expected shape.");
            return { exit: 3, report };
        }
    } else {
        log(" writer:   (not checked — pass --writer <path> to run the shape tripwire)");
    }

    log();
    log(" PROVES:   internal consistency of this file under the stated formula");
    log(" DOES NOT PROVE:");
    log("   - authenticity (anyone with the writer can produce a valid file)");
    log("   - originality (a self-consistent alternate history is undetectable)");
    log("   - writer behavior (source-shape check is a tripwire, not a proof)");
    if (!opts.anchorPath) log("   - that the file on disk is the file that was written (no anchor provided)");
    log("══════════════════════════════════════════════════════════════");
    log();

    const lines = readLedger(file);
    log(`ledger:  ${path.resolve(file)}`);
    log(`records: ${lines.length}`);

    if (lines.length === 0) {
        log();
        log("empty ledger — nothing to verify.");
        log("VERDICT: PASS (empty)");
        return { exit: 0, report };
    }

    const records = [];
    for (let i = 0; i < lines.length; i++) {
        try { records.push(JSON.parse(lines[i])); }
        catch (e) { elog(`[PARSE] line ${i}: ${e.message}`); return { exit: 1, report }; }
    }

    const shape = validateShape(records);
    if (!shape.ok) {
        elog();
        elog(`[UNVERIFIABLE] record ${shape.at}: ${shape.reason}`);
        elog(`  field:  ${shape.field}`);
        if ("value" in shape) elog(`  value:  ${JSON.stringify(shape.value)}`);
        return { exit: 3, report };
    }

    const expectedArm = opts.armOverride ?? records[0].arm;
    log(`arm:     ${expectedArm}${opts.armOverride ? "  (override)" : "  (from record 0)"}`);
    log();

    let failed = false;

    const gen = checkGenesis(records);
    if (!gen.ok) { elog(`[GENESIS] ${gen.reason}`); failed = true; }
    else log(`[GENESIS] ok`);

    const chain = checkChain(records);
    if (!chain.ok) {
        elog(`[CHAIN] broken at record ${chain.at} (seq=${records[chain.at].seq})`);
        elog(`        stored prev_hash: ${chain.stored}`);
        elog(`        expected:         ${chain.expected}`);
        failed = true;
    } else log(`[CHAIN] ok`);

    const seq = checkSeq(records);
    if (!seq.ok) { elog(`[SEQ] record ${seq.at}: seq=${JSON.stringify(seq.value)}, expected ${seq.expected}`); failed = true; }
    else log(`[SEQ] ok (1..${records.length})`);

    const arm = checkArm(records, expectedArm);
    if (!arm.ok) { elog(`[ARM] record ${arm.at}: arm=${JSON.stringify(arm.value)}, expected ${JSON.stringify(arm.expected)}`); failed = true; }
    else log(`[ARM] ok (all records arm=${expectedArm})`);

    const ts = checkTimestampMonotonic(records);
    if (!ts.ok) { elog(`[TIMESTAMP] record ${ts.at}: ${ts.reason}`); failed = true; }
    else log(`[TIMESTAMP] ok (monotonic)`);

    const selfFailures = checkSelfHashes(records);
    if (selfFailures.length === 0) {
        log(`[SELF-HASH] ok (all ${records.length} records match writer formula)`);
    } else {
        failed = true;
        elog(`[SELF-HASH] ${selfFailures.length} record(s) do not match writer formula:`);
        let compareIndex = null;
        if (opts.compareFile && fs.existsSync(opts.compareFile)) {
            try {
                const otherRecords = readLedger(opts.compareFile).map(JSON.parse);
                compareIndex = buildSeqIndex(otherRecords);
            } catch (e) { elog(`  (could not read --compare file: ${e.message})`); }
        }
        elog();
        for (const f of selfFailures.slice(0, 5)) {
            const r = records[f.at];
            elog(`  record ${f.at}  (seq=${r.seq}, event=${r.event ?? "?"})`);
            elog(`    stored:   ${f.stored}`);
            elog(`    computed: ${f.computed}`);
            if (compareIndex) {
                const twin = compareIndex.get(r.seq);
                if (twin) {
                    const diffs = fieldDiff(r, twin);
                    elog(`    field diffs vs ${path.basename(opts.compareFile)}:`);
                    if (diffs.length === 0) elog(`      (no field diffs — mismatch is in the hash field itself)`);
                    else for (const d of diffs) {
                        elog(`      ${d.field}:`);
                        elog(`        before: ${d.before}`);
                        elog(`        after:  ${d.after}`);
                    }
                } else {
                    elog(`    (no record with seq=${r.seq} in compare file)`);
                }
            }
            elog();
        }
        if (selfFailures.length > 5) elog(`  ... and ${selfFailures.length - 5} more`);
    }

    const anchorResult = checkAnchor(records, opts.anchorPath);
    if (!anchorResult.ok) { elog(`[ANCHOR] ${anchorResult.reason}`); failed = true; }
    else if (anchorResult.present) log(`[ANCHOR] ok (seq=${anchorResult.seq} hash matches)`);
    else log(`[ANCHOR] not checked`);

    log();
    log(failed ? "VERDICT: FAIL" : "VERDICT: PASS (internal consistency)");
    if (!failed) {
        log();
        log("PASS means: every record's stored hash reproduces from its content");
        log("under the formula above, and the chain links, seq is dense from 1,");
        log("timestamps are non-decreasing, and every arm field matches.");
        log();
        log("PASS does NOT mean: the file was not rewritten with a self-consistent");
        log("alternative history. That property requires an external anchor — a signed");
        log("checkpoint published somewhere the writer cannot reach. Pass --anchor");
        log("with a {\"seq\": N, \"hash\": \"...\"} file to check one if you have it.");
    }

    return { exit: failed ? 1 : 0, report };
}

function selftest() {
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "vle-selftest-"));
    let allPassed = true;
    const report = (name, passed, detail) => {
        const mark = passed ? "PASS" : "FAIL";
        console.log(`  [${mark}] ${name}${detail ? "  — " + detail : ""}`);
        if (!passed) allPassed = false;
    };

    console.log("══════════════════════════════════════════════════════════════");
    console.log(" SELFTEST");
    console.log("══════════════════════════════════════════════════════════════");
    console.log();

    {
        const got = crypto.createHash("sha256").update("", "utf8").digest("hex");
        report("sha256 primitive", got === SHA256_EMPTY, got === SHA256_EMPTY ? "" : `got ${got}`);
    }

    const mkRec = (seq, prevHash, event) => {
        const without = {
            event, arm: "selftest", seq, prev_hash: prevHash,
            timestamp: new Date(Date.UTC(2026, 0, 1, 0, 0, seq)).toISOString(),
        };
        return { ...without, hash: writerHash(without, prevHash) };
    };

    const clean = [ mkRec(1, GENESIS, "genesis") ];
    clean.push(mkRec(2, clean[0].hash, "second"));
    clean.push(mkRec(3, clean[1].hash, "third"));

    const cleanPath = path.join(tmp, "clean.jsonl");
    fs.writeFileSync(cleanPath, clean.map(r => JSON.stringify(r)).join("\n") + "\n");
    {
        const r = verifyFile(cleanPath, { armOverride: "selftest" });
        report("clean ledger returns exit 0", r.exit === 0, `exit=${r.exit}`);
    }

    {
        const t = clean.map(r => ({ ...r }));
        t[1].event = "tampered";
        const p = path.join(tmp, "tampered.jsonl");
        fs.writeFileSync(p, t.map(r => JSON.stringify(r)).join("\n") + "\n");
        const r = verifyFile(p, { armOverride: "selftest" });
        report("tampered content returns exit 1", r.exit === 1, `exit=${r.exit}`);
    }

    {
        const b = clean.map(r => ({ ...r }));
        b[1].hash = "not-a-hash";
        const p = path.join(tmp, "broken-hash.jsonl");
        fs.writeFileSync(p, b.map(r => JSON.stringify(r)).join("\n") + "\n");
        const r = verifyFile(p, { armOverride: "selftest" });
        report("malformed hash returns exit 3", r.exit === 3, `exit=${r.exit}`);
    }

    {
        const m = clean.map(r => ({ ...r }));
        delete m[1].seq;
        const p = path.join(tmp, "missing-seq.jsonl");
        fs.writeFileSync(p, m.map(r => JSON.stringify(r)).join("\n") + "\n");
        const r = verifyFile(p, { armOverride: "selftest" });
        report("missing required field returns exit 3", r.exit === 3, `exit=${r.exit}`);
    }

    {
        const b = clean.map(r => ({ ...r }));
        b[2].prev_hash = "0".repeat(64);
        const p = path.join(tmp, "broken-chain.jsonl");
        fs.writeFileSync(p, b.map(r => JSON.stringify(r)).join("\n") + "\n");
        const r = verifyFile(p, { armOverride: "selftest" });
        report("chain break returns exit 1", r.exit === 1, `exit=${r.exit}`);
    }

    console.log();
    console.log(allPassed ? "SELFTEST: all checks passed" : "SELFTEST: some checks failed");
    console.log(`fixtures: ${tmp}`);
    console.log();
    console.log("NOTE: fixtures built with writerHash() — the same function the verifier");
    console.log("uses. This exercises the pipeline, not the formula. Compute one record's");
    console.log("hash independently (e.g. Python) to validate the formula itself.");
    return allPassed ? 0 : 1;
}

function main() {
    const argv = process.argv.slice(2);

    if (argv.includes("--selftest")) process.exit(selftest());

    if (argv.length === 0) {
        console.error("usage: verify-ledger-exact.mjs <ledger.jsonl> " +
                      "[--compare <other.jsonl>] [--arm <name>] " +
                      "[--writer <path>] [--anchor <anchor.json>]");
        console.error("       verify-ledger-exact.mjs --selftest");
        process.exit(2);
    }

    const file = argv[0];
    const getOpt = (name) => {
        const i = argv.indexOf(name);
        return i !== -1 && argv[i + 1] ? argv[i + 1] : null;
    };

    const result = verifyFile(file, {
        compareFile: getOpt("--compare"),
        armOverride: getOpt("--arm"),
        writerPath:  getOpt("--writer"),
        anchorPath:  getOpt("--anchor"),
    });

    for (const line of result.report) console.log(line);
    process.exit(result.exit);
}

main();
