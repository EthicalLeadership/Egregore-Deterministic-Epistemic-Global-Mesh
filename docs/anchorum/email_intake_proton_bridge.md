# Email Intake & Search for Proton Bridge — Assessment & Implementation Plan

**Deliverable on approval:** this report is saved verbatim to `docs/anchorum/email_intake_proton_bridge.md` (the "single Markdown document" requested). No code is written in this phase; implementation follows after review.

---

## 1. Current State

### 1.1 What exists today

**Chat command layer** — `src/egregore/application/chat_interpreter.py` (1256 lines)
- `parse_command()` (lines 255–299): slash commands + NL prefixes (`ingest/load/import`→`ingest`, `legal`→`legal`, etc.). Plain text defaults to `ask`.
- Dispatch table `_COMMAND_HANDLERS` (lines 1220–1234): `{help, ask, model, agents, agent, models, ingest, compare, integrity, check, hold, dossier, legal}`. Unknown commands fall back to `_cmd_dossier`.
- **There is NO `/intake`, `/email`, or `/fetch` chat command** — no slash command, no NL pattern.
- `ChatContext` (frozen dataclass, lines 32–40): `session_id, user_id, role, env, identity`. Entry point `execute_message()` (line 1245), called from the WebSocket route `src/egregore/http_api/http/v1/ws_chat.py:92`.
- Privileged commands (`/ingest`, `/hold`, `/agent`, …) gated by `_require_privilege` → `PermissionService` / `Action.CHAT_ADMIN` (lines 302–314).
- `/ingest` (lines 547–587) only ingests `.zarc` archives — not email.

**Email ingestion that already exists (desktop-only, manual)**
- `email_ingest.py` (repo root, 605 lines) — Tk desktop "Email" tab, wired at `anchorum_desktop.py:389`. Consent-gated IMAP fetch, **stdlib only** (`imaplib` + `email`):
  - `ImapConfig{host, port=993, user, password, use_ssl}` (lines 54–64); `_connect` uses `IMAP4_SSL` (67–70).
  - `list_folders()` (87–102), `count_messages()` (105–125), `parse_message_metadata()` (135–160).
  - `stage_email_fetch()` (169–274): read-only `SELECT`, `SEARCH ALL`, `FETCH (RFC822)`; writes raw `{uid}.eml` + `{uid}.json` metadata sidecar + job `manifest.json` under `fetched/{case}/email__{account}__{ts}/{folder}/`, each artifact SHA-256'd, manifest bound to the consent-ledger hash.
  - Consent protocol reused from `file_fetch.py`: default-deny modal, hash-chained `email_consent_ledger.jsonl` (`ConsentLedger`, fail-closed `verify()`).
  - **Limitations:** UI form has no default host (nothing Proton-specific); `SEARCH ALL` only (no criteria); Tk imported at module top (lines 28, 33–34) so the module is not importable on a headless server; no chat/API entry point.
- `src/egregore/infrastructure/imap_connector.py` (180 lines) — generic `IMAPConnector` (`fetch_unseen` only). **Dead code**: no production import anywhere in `src/`; only its own test references it.

**Factory "intake" endpoints (unrelated to email search)**
- `src/egregore/interface/factory_router.py`: `POST /v1/intake`, `/v1/intake/chat`, `/v1/intake/email` (takes a JSON email envelope body — does NOT connect to a mailbox), `/v1/intake/anchorum`.
- `src/egregore/http_api/http/v1/intake.py`: `POST /v1/intake/upload` (multipart file → dossier).

**RAG pipeline** — `src/egregore/interface/case_rag.py` (317 lines)
- Per-case ChromaDB `PersistentClient` at `rag/cases/{case_id}/` (`EGREGORE_CASE_RAG_ROOT`), collection `"case"`.
- `_gather_case_documents()` (88–203) ingests: report JSON, transcript sidecars, and **staged evidence under `fetched/{case_id}/`** — suffixes `.txt .md .csv .json .eml .html .htm .log` (`_TEXT_SUFFIXES`, line 33), ≤ 2 MiB each.
- Chunking: 1200 chars / 200 overlap. Shared sentence-transformer embedder via `rag_api._get_embedder`.
- Rebuild: delete + recreate under a lock (`index_case`, 206–250). Endpoints: `POST /api/v1/anchorum/cases/{id}/reindex` (`anchorum_http.py:549-558`), `GET .../index` (541–547). Chat retrieves top-k=4 with distance cutoff 1.4 (`anchorum_http.py:240-271`).
- **Gap:** `.eml` files are indexed as *raw RFC822 text* — headers, base64/quoted-printable bodies and MIME boundaries pollute chunks. No MIME-aware body extraction before embedding.

**Forensic batch runner** — `src/anchorum/forensic/core/batch_runner.py`
- Already detects `ContainerType.EMAIL` (magic bytes `From `/`Return-Path:`/`Received:`/`MIME-Version:`) and extracts metadata via `extraction/email.py` (stdlib `BytesParser`; Received-chain, dates, addresses, attachments). SHA-256 IS the artifact id (`ingestion.py:319`).
- **Gap:** staged `fetched/` emails are NOT auto-submitted to `run_batch`; they only become RAG text.

**Model selection / orchestrator**
- `src/egregore/application/model_selector.py`: deterministic `ModelSelector.select(task_type)`, `ALLOWED_TASKS = ("general","code","legal","fast")`; manifest `config/model_profiles.json` fail-closed.
- `POST /v1/orchestrate/chat/completions` (`http_api/http/v1/orchestrate.py`) auto-selects by `task_type` through governed `InferenceService` (M1–M4), SSE streaming.
- Chat routing `_active_model()` (chat_interpreter 159–190): session `CHAT_MODEL` → env → selector.

**Agents**
- `AgentRegistry` (`application/agent_registry.py`) scans `<repo>/agents/`; `AgentRunner` (`application/agent_runner.py`) runs an agent as a subprocess with `TaskIntent`, role gating, timeout. Existing agents: `claude-agent`, `example-agent`.

### 1.2 Where the gaps are

| Gap | Evidence |
|---|---|
| No `/intake` chat command | `_COMMAND_HANDLERS` (chat_interpreter.py:1220–1234); no NL pattern (255–299) |
| No chat→IMAP path | `email_ingest.py` is desktop-Tk only; `imap_connector.py` unused in prod |
| No Proton Bridge config | Zero hits for `127.0.0.1:1143`, `PROTON_BRIDGE_*`; only evidence *filenames* inside report JSONs |
| No IMAP search criteria | `stage_email_fetch` hardcodes `SEARCH ALL` |
| No STARTTLS support | `_connect` only does `IMAP4_SSL`; Proton Bridge on 1143 is STARTTLS |
| Raw `.eml` indexed verbatim | `case_rag._TEXT_SUFFIXES` includes `.eml`; no MIME body extraction |
| Staged emails skip forensics | `fetched/` never fed to `run_batch` automatically |

---

## 2. Root Cause of the MOLSON-2026 Failure

The user typed (in effect): *"intake all emails in the proton bridge"* for case MOLSON-2026.

1. **No command matched.** `parse_command()` has no `intake`/`email`/`fetch` pattern. The message fell through to the default `("ask", [text])` branch (chat_interpreter.py:299) — i.e., it was treated as a *question to the LLM*, not an action.
2. **The LLM had no tool to act with.** There is no chat-initiated IMAP path anywhere in the stack. The only IMAP code is (a) the desktop Email tab (manual, Tk, consent-dialog driven) and (b) the orphaned `imap_connector.py`. Neither is reachable from `execute_message()`.
3. **Why the AI "saw" only empty/truncated headers:** the case context the model received came from `_case_context()` / `_retrieve_case_chunks()` in `anchorum_http.py`, which surface whatever is already in the case workspace — the few `.eml` files previously staged into `fetched/MOLSON-2026/` (or the dossier). Those files were raw RFC822; the ones present were empty or truncated, and `case_rag` indexes `.eml` *verbatim*, so the retrieved chunks were header fragments. The model then hallucinated an intake attempt around the only artifacts it could see.
4. **Why no Proton Bridge search happened:** there is no Proton Bridge host default, no credential env var (`PROTON_BRIDGE_EMAIL/PASSWORD`) read anywhere, no STARTTLS connector, and no code path from chat to `imaplib`. The system did exactly what it is built to do — answer from existing case data.
5. **What explicit command would have worked:** none in chat. The only working path was the **desktop app's Email tab** (`anchorum_desktop.py` → `EmailIngestTab`) with the Bridge host/port typed in by hand — a GUI-only, whole-folder `SEARCH ALL` fetch with no case-targeted search.

---

## 3. Required Changes (file-by-file)

Design principles: reuse the existing consent/ledger/manifest protocol from `email_ingest.py`/`file_fetch.py`; keep the fetch path **deterministic and LLM-free** (LLM only summarizes results afterwards); fail closed on credentials, TLS, and consent.

### 3.1 NEW `src/egregore/infrastructure/email_intake.py` (headless core)

Lift the pure logic out of `email_ingest.py` (which stays as the Tk front-end) into a headless module:

```python
@dataclass(frozen=True)
class BridgeConfig:          # extends ImapConfig
    host: str = "127.0.0.1"
    port: int = 1143
    user: str = ""           # from EGREGORE_PROTON_BRIDGE_EMAIL (or PROTON_BRIDGE_EMAIL)
    password: str = ""       # from EGREGORE_PROTON_BRIDGE_PASSWORD
    starttls: bool = True    # Proton Bridge 1143 = STARTTLS; use_ssl for 993-style

@dataclass(frozen=True)
class EmailSearchSpec:       # the deterministic query contract
    case_id: str
    keywords: tuple[str, ...] = ()      # OR'd: TEXT/SUBJECT/FROM per keyword
    folders: tuple[str, ...] = ()       # empty = all LIST-returned folders
    sender: str | None = None
    recipient: str | None = None
    since: str | None = None            # IMAP date 01-Jan-2026
    before: str | None = None
    max_messages: int = 500             # hard cap, fail-closed overflow flag

def connect(cfg) -> imaplib.IMAP4:      # IMAP4 + starttls() + login(); cert verified
def list_folders(conn) -> list[str]
def build_search_criteria(spec) -> str  # IMAP SEARCH string, e.g. '(SINCE ..) (OR TEXT "x" SUBJECT "x")'
def search_and_stage(cfg, spec, consent_hash, on_progress=None) -> IntakeManifest
```

- `search_and_stage`: for each folder (sorted), `SELECT` **read-only** (`readonly=True`), `UID SEARCH <criteria>`, sort UIDs ascending (determinism), `UID FETCH (RFC822)`, write `{uid}.eml` + `{uid}.json` sidecar (existing `parse_message_metadata`) into `fetched/{case_id}/email__{account}__{UTCts}/{folder}/` — the exact layout `stage_email_fetch` already produces — plus per-file SHA-256 (`_sha256_file`, 1 MiB streaming).
- Manifest additionally records: full `EmailSearchSpec`, folder list, per-folder UID lists, totals, and `consent_hash`. Same query + same mailbox state ⇒ identical UID lists ⇒ identical manifest (modulo timestamp dir).
- **NEW:** write `{uid}.txt` — decoded plain-text body extraction (stdlib `email`: walk MIME parts, `text/plain` preferred, `text/html` stripped fallback, `get_payload(decode=True)` + charset decode, errors→`replace`). This is what ChromaDB should index, not raw RFC822.

### 3.2 MODIFY `email_ingest.py` (desktop tab)

- Import shared logic from the new module (`ImapConfig`, `list_folders`, `count_messages`, `parse_message_metadata`, staging helpers) instead of duplicating; keep the Tk dialog and consent flow unchanged. Net deletion of duplicated code. (Alternative: leave desktop tab untouched in phase 1 and only add the new module — see Options.)

### 3.3 MODIFY `src/egregore/application/chat_interpreter.py`

- Add NL pattern: `lowered.startswith(("intake ", "email intake ", "fetch email"))` → `("intake", rest.split())` (next to the other prefixes, ~line 294).
- Add `_cmd_intake(args, context)` handler + register `"intake"` in `_COMMAND_HANDLERS`. Gate with `_require_privilege` (admin/operator), same as `/ingest`.
- Command grammar (explicit, documented in `/help`):
  ```
  /intake email <case_id> [keywords...] [from:<addr>] [to:<addr>]
          [since:YYYY-MM-DD] [before:YYYY-MM-DD] [folders:Inbox,Archive,Junk,Trash]
          [max:<n>]
  /intake folders                     # list Bridge folders + message counts (read-only)
  ```
- Handler flow:
  1. Parse args → `EmailSearchSpec` (reject unknown tokens, fail closed).
  2. Load `BridgeConfig` from env; **refuse with a clear error if credentials unset** (never prompt for a password in chat, never echo it).
  3. **Consent gate (chat equivalent of the modal):** reply with the exact scope (account, folders, per-folder match counts via `UID SEARCH` without FETCH) and require the user to reply `/intake confirm <token>` within N minutes; the token and scope are stored in `context.env`, and grant/refusal are appended to `email_consent_ledger.jsonl` (reuse `ConsentLedger` from `file_fetch.py` — import path shim or move ledger into `src/egregore/`). Default-deny: anything other than confirm = refusal, ledgered.
  4. On confirm: run `search_and_stage` in a threadpool with `on_progress` callbacks → chat progress messages (`folder Archive: 37/112 messages`).
  5. On completion: trigger `case_rag.index_case(case_id)` (rebuild) and optionally submit the staging dir to `POST /batch/sync` (forensic `ContainerType.EMAIL` extraction) — flag-gated `run_forensics:true`, default off.
  6. Return summary: folders searched, messages matched/fetched, staging path, manifest SHA-256, consent ledger hash, reindex stats.
- Errors (auth failure, connection refused, TLS error) → concise chat error, ledgered, never include credentials.

### 3.4 MODIFY `src/egregore/interface/case_rag.py`

- In `_gather_case_documents` staged-evidence branch: when a `.eml` sibling `{name}.txt` exists (from 3.1), index the `.txt` and skip the raw `.eml` (or index raw `.eml` only when no extracted text exists). Prevents base64/header pollution of embeddings. Keep the `.eml` itself as the hashed evidence of record.

### 3.5 Config / env

- `.env` (documented, not committed secrets): `EGREGORE_PROTON_BRIDGE_HOST=127.0.0.1`, `EGREGORE_PROTON_BRIDGE_PORT=1143`, `EGREGORE_PROTON_BRIDGE_EMAIL`, `EGREGORE_PROTON_BRIDGE_PASSWORD`, optional `EGREGORE_INTAKE_MAX_MESSAGES=500`, `EGREGORE_INTAKE_FORENSICS=off`.
- `AGENTS.md`: add a short "Email intake" section (required by repo convention since we touch documented behavior).

### 3.6 What is deliberately NOT changed

- No new LLM in the fetch path — intake is deterministic tooling. After staging, the normal legal chat (`task_type="legal"` via `EgregoreModelClient` / orchestrator) answers over the refreshed case RAG. If a post-intake *summary* is wanted, the handler calls the orchestrator with `task_type="fast"` — model selection stays in `ModelSelector`, untouched.
- `factory_router.py` `/v1/intake/email` stays as-is (different concern: envelope normalization).
- `imap_connector.py` — either delete as dead code or leave; not reused (it is unseen-only polling, wrong shape).

---

## 4. Dependencies

**None.** Stdlib only, consistent with `email_ingest.py`:
- `imaplib` (IMAP4 + STARTTLS), `email` (MIME parse/decode), `hashlib`, `ssl`, `json`, `threading`.
- ChromaDB / sentence-transformers already present (`case_rag.py`).
- **Rejected:** `mail-parser` (stdlib `email` suffices and is already used in two places), `python-gnupg` (Bridge already decrypts; PGP is out of scope), any third-party IMAP client.

---

## 5. Security Considerations

1. **Credentials:** read only from env (`EGREGORE_PROTON_BRIDGE_PASSWORD`) or a root-only `0600` file under `secrets/`; never accepted as a chat argument, never written to manifests, telemetry, ledgers, or logs. Error paths sanitize exception strings (imaplib can echo the LOGIN command on failure — catch `IMAP4.error` and log a static message).
2. **TLS:** STARTTLS with certificate verification on (`ssl.create_default_context()`); refuse to proceed if verification fails (fail-closed). Bridge presents a locally-signed cert by default → document pinning the Bridge cert fingerprint in env, or verified `check_hostname=False` + explicit fingerprint match. No `CERT_NONE` blanket bypass.
3. **Read-only mailbox:** `SELECT … readonly=True`; only `FETCH`/`SEARCH`/`LIST`/`STATUS` — no `STORE`, `EXPUNGE`, `MOVE`, `APPEND`. Enforced by a whitelist wrapper around the connection.
4. **Consent & audit:** chat consent mirrors the desktop protocol — default-deny, scope shown before fetch, confirm token expires, every request/grant/refusal/error hash-chained into `email_consent_ledger.jsonl`; manifest records the authorizing consent hash. Privileged command (`Action.CHAT_ADMIN`) only.
5. **Abuse controls:** `max_messages` cap (default 500, hard ceiling 5000); case_id validated against the same regex used by `anchorum_http` (`_validate_case_id`-style) to prevent path traversal in the staging dir; folder names sanitized via `_sanitize_component`; per-run timeout.
6. **Evidence integrity:** raw RFC822 bytes are the evidence (SHA-256 at acquisition, existing convention). The extracted `.txt` is an *unverified derivative* — same rule as transcripts/LLM enrichment — and must never replace the `.eml` in the index of record.

---

## 6. Integration Points

- **Chat interpreter:** new `intake` command + NL prefix + `_cmd_intake` handler + `_COMMAND_HANDLERS` entry + `/help` text. Session consent state in `ChatContext.env` (same place `CHAT_MODEL`/`chat_history` live).
- **RAG:** staging layout is already exactly what `case_rag._gather_case_documents` scans (`fetched/{case_id}/`, `.eml`/`.txt` suffixes) — only the `.eml`-vs-`.txt` preference (3.4) and a post-intake `index_case()` call are needed. No schema change.
- **Forensics (optional):** staging dir submittable to existing `POST /batch/sync` — `ContainerType.EMAIL` extraction already exists.
- **Orchestrator / InferenceService:** untouched; intake uses no model. Post-intake summaries use `task_type="fast"` through the existing governed path (M1–M4 intact).
- **Provenance:** optional `emit_zarc_event`/`Provenance` `email_intake_completed` event when `ANCHORUM_SIGNING_KEY` is present, mirroring `artifact_extracted` conventions (degrade gracefully standalone).
- **Desktop app:** unchanged behavior; optionally refactored to share the new core module.

---

## 7. Testing Plan

**Unit tests** (new `tests/infrastructure/test_email_intake.py`, pattern follows `tests/test_email_ingest.py`):
- `build_search_criteria`: keywords/from/to/since/before combinations → exact IMAP strings; dates validated; injection-safe quoting of keywords (`"`/`\`/non-ASCII).
- Fake IMAP class (duck-typed `imaplib.IMAP4`): scriptable `LIST`/`SELECT`/`UID SEARCH`/`UID FETCH` responses → assert deterministic staging: sorted UIDs, exact file layout, SHA-256 matches content, manifest records spec + consent hash + per-folder UID lists.
- Determinism: same fake server state + same spec ⇒ identical per-folder UID lists and file set across two runs (only the timestamp dir differs).
- Body extraction: multipart/mixed fixture with quoted-printable + base64 + HTML-only + attachment → `.txt` contains decoded plain text, no base64, attachment bytes excluded.
- Fail-closed: auth error, TLS failure, missing env credentials, `max_messages` overflow flag, read-only enforced (fake raises on `STORE` → assert no mutating command ever issued).
- Chat layer: `parse_command("intake all emails …")` → `("intake", …)`; `_cmd_intake` without confirm returns scope + pending token; refusal ledgered; non-admin rejected.

**Integration test:**
- Throwaway local IMAP server fixture (e.g. `twisted`-free: a minimal asyncio IMAP stub speaking just enough of the protocol, or `greenmail`-style container if available) seeded with 5 messages across Inbox/Archive/Junk/Trash; run `/intake email TEST-CASE keyword` end-to-end → assert staged files, manifest, ledger chain `verify()`, then `index_case("TEST-CASE")` and a `query_case` hit on message body text.
- Live manual verification (not CI): real Proton Bridge at 127.0.0.1:1143, `/intake folders`, then a narrow keyword search for MOLSON-2026, verify counts against the Proton web UI.

**Regression:** rerun `.venv/bin/python scripts/legal_kill_test.py` after any `case_rag.py` change (AGENTS.md requirement).

---

## 8. Implementation sequencing (post-review)

1. `src/egregore/infrastructure/email_intake.py` + unit tests (fake IMAP).
2. `chat_interpreter.py` command + consent gate + tests.
3. `case_rag.py` `.txt` preference + regression tests.
4. Optional: desktop `email_ingest.py` dedup refactor; zarc provenance event; `/batch/sync` hook.
5. Docs: `docs/anchorum/email_intake_proton_bridge.md` (this report) + `AGENTS.md` section + `.env.example` entries.
