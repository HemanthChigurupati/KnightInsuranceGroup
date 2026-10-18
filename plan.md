# Plan: Submission Review Assistant (Option D)

> Planning document written before implementation. The README describes what was actually built; where they differ, the README is current.

Knight Insurance, AI Solutions Developer candidate exercise.
**Deadline:** Sunday, October 18, 2026, 10:00 AM PT. The invitation email moved it; the brief and START_HERE.txt still say Sat 11:59 PM.

**Architecture:** FastAPI backend with a deterministic core, plus a simple HTML + vanilla JS page served by FastAPI (no React, no npm).
**LLM:** works with any model behind one small interface. Claude (Anthropic API) or any OpenAI-compatible endpoint (OpenAI, Gemini, Ollama, LM Studio, vLLM, …) is chosen in `.env`; no model or key is preconfigured.
**Principle:** the LLM reads the documents and writes the narrative. Code makes every decision.

**Scope note:** this is **not** a live mailbox tool. The brief puts live mailbox connections out of scope and says "do not send follow-up email automatically; display an editable draft only". The underwriter supplies the packet by hand (uploads the files or pastes the email text). Processing after that is automatic, and the follow-up email is a draft the underwriter copies into their own mail client.

---

## 1. Goal

A locally runnable browser app where an underwriter:

1. Starts from an empty screen and uploads one submission packet (email + PDFs + CSV), or pastes the broker email.
2. Sees the processing, success and failure states.
3. Sees the 8 extracted fields plus the derived `schedule_vehicle_count`, each with its state (KNOWN / MISSING / CONFLICT / INVALID) and evidence (file, locator, excerpt).
4. Sees R1–R5, each with a status, reason and the values or evidence used, plus the overall status.
5. Reads an AI-generated underwriter summary and an **editable draft** broker email. It is never sent.
6. Corrects a field, which becomes a labelled human override that keeps the original, and reruns the checks.
7. Loads another packet without any data from the previous applicant carrying over.

---

## 2. Requirements traceability

| Brief requirement | Where it is handled |
|---|---|
| Empty start; upload or paste; processing/success/failure states | `static/index.html` upload section + status bar; `POST /api/submissions` |
| Extract 8 fields + derive schedule count | `extraction.py` (LLM), `derived.py` (code) |
| Evidence: source file, locator, excerpt; checked sources when missing | `models.Evidence`, `evidence.py`, `reconcile.py` |
| Conflicts: keep both values, value = null, state CONFLICT, no preferred source | `reconcile.py` |
| R1–R5 in ordinary code | `rules.py` |
| Overall READY_FOR_REVIEW / NEEDS_REVIEW | `rules.overall_status()` |
| AI summary + editable draft email | `narrative.py`; summary box + `<textarea>` in `index.html` |
| Human override, original kept, rerun | `PUT /api/submissions/{id}/overrides/{field}`; "Correct" button per field row |
| Packet isolation | New `submission_id` per upload; frontend state reset |
| Model failure: show it, keep deterministic results | `pipeline.py` stage statuses; `ProcessingStatus` |
| Submission text treated as data (prompt injection) | `prompts.py` delimiting; excerpt check in `evidence.py` |
| Tests without live model calls | `tests/` uses `FakeLLM` |
| No secrets | `.env.example`, `.gitignore`, keys only from env vars |

---

## 3. Expected results on the samples (worked out by hand from the rules)

| | SUB-A | SUB-B | SUB-C |
|---|---|---|---|
| R1 Identity + date | PASS | **FLAG** (date "Not provided") | PASS |
| R2 Vehicle count | PASS (3 = 3) | PASS (2 = 2) | **FLAG** (declared 4 ≠ CSV 3) |
| R3 Radius | PASS (150, at the boundary) | UNKNOWN (missing) | UNKNOWN (email 180 vs PDF 80) |
| R4 Hazmat | PASS (No) | UNKNOWN (missing) | UNKNOWN (email Yes vs PDF No) |
| R5 Loss runs | PASS (zero-claims report counts) | **FLAG** (promise ≠ attachment) | **FLAG** |
| Overall | READY_FOR_REVIEW | NEEDS_REVIEW | NEEDS_REVIEW |

Traps to cover explicitly:
- "CA" vs "California" is **not** a conflict.
- In SUB-C, the conflict check must run before the 150-mile threshold.
- The email's "loss runs report is also attached" is a claim. Only the actual attachment counts.

These become the end-to-end test expectations. They are **not** hard-coded into the app.

---

## 4. Architecture

```
 Browser (index.html + app.js)           FastAPI backend
┌────────────────────┐   multipart    ┌──────────────────────────────────────────────┐
│ 1 Upload / paste   │ ─────────────▶ │ api/routes.py                                │
│   status bar       │                │   │                                          │
│ 2 Fields+evidence  │ ◀── JSON ───── │   ▼                                          │
│   [Correct] button │                │ services/pipeline.py                         │
│ 3 Rule results     │                │   1 ingest      files → SourceDocs (+ pages) │
│ 4 Summary          │                │   2 extract     LLM, one call per source     │  ← AI
│ 5 Draft email      │                │   3 evidence    check excerpt is in source    │
└────────────────────┘                │   4 normalize   dates/state/number/bool      │
                                      │   5 derive      CSV count, loss-runs check   │  ← code
                                      │   6 reconcile   email vs PDF → field state   │  ← code
                                      │   7 overrides   apply human corrections      │  ← code
                                      │   8 rules       R1–R5 + overall              │  ← code
                                      │   9 narrative   summary + draft email        │  ← AI
                                      │                                              │
                                      │ store.py  in-memory {submission_id: result}  │
                                      └──────────────────────────────────────────────┘
```

### Why extraction runs once per source
The email and the application PDF are each sent to the LLM **separately**, so the model never sees both and can't pick a winner. `reconcile.py` then compares the normalised values in code, which is what makes SUB-C's conflicts visible and unbiased.

### What is code and what is AI

| Step | Code / AI | Notes |
|---|---|---|
| Read `.txt`/`.eml`, PDF pages (pypdf), CSV | Code | PDF text is tagged by page for the locators |
| Extract field candidates + verbatim excerpts | **AI** | JSON output validated with Pydantic |
| Excerpt verification | Code | An excerpt not found in the source text is rejected, so the value can't be used |
| Normalisation | Code | `Not provided` → MISSING; `California` → `CA`; `150.0` → `150`; `Yes`/`No` → bool; ISO dates |
| `schedule_vehicle_count` | Code | Count non-blank rows after the header; an unparseable CSV gives INVALID |
| `loss_runs_present` | Code | Classify each attachment by its **content** (e.g. a "Loss runs report" / "loss history" heading). Readable match → true; none → false; unreadable or undeterminable candidate → null |
| Reconcile | Code | Agreeing values → KNOWN; differing values → CONFLICT (value null, both alternatives kept); none → MISSING (sources checked listed); bad format → INVALID |
| Rules R1–R5 + overall | Code | Pure functions |
| Summary + draft email | **AI** | The input is the rule results JSON, not the raw documents |

### Model independence
The rest of the app talks to the LLM through **one method**:

```python
class LLMClient(Protocol):
    name: str                                    # shown in the UI, as "<provider>/<model>"
    def complete(self, system: str, user: str) -> str: ...
```

| Adapter | Covers | Selected by |
|---|---|---|
| `AnthropicClient` | Claude models | `LLM_PROVIDER=anthropic`, `LLM_MODEL=…`, `ANTHROPIC_API_KEY` |
| `OpenAICompatibleClient` | OpenAI, Ollama, LM Studio, vLLM, Groq, OpenRouter, Azure, … (all speak the `/v1/chat/completions` format) | `LLM_PROVIDER=openai_compatible`, `LLM_BASE_URL=…`, `LLM_MODEL=…`, `LLM_API_KEY` |
| `FakeLLM` | Tests only | Injected in pytest |

Why any model can do the job:
- **Plain prompts:** no provider-specific features (tools, JSON mode or structured outputs) are required.
- **Our code checks the output, not the model:** the response is parsed as JSON and validated with Pydantic. If it's invalid, the code retries once with the error message. If it still fails, the stage is marked `failed` and the rules run with what's available.
- **Excerpt check:** a weaker model can't sneak in invented evidence, because every excerpt must appear in the source text.
- **Switching models:** it's an `.env` change only, with no code changes.
- **Known limit:** small local models extract less reliably. The README will say which models were tested.

### Failure handling
- Each stage reports `ok | failed | skipped` with a message, and the UI shows it.
- **If extraction fails** (LLM down or bad JSON after one retry), the fields from that source are marked "extraction failed". R2's schedule side and R5 are still computed, the rules that depend on the failed fields return UNKNOWN, and the UI shows an error banner. No fake values.
- **If the narrative fails**, the UI shows "AI summary unavailable" plus a retry button. A deterministic list of the open items can be shown, **clearly labelled "not AI-generated"**.
- A bad upload (wrong type, empty) returns 400 with a message, which the UI shows as a failure state.

### Prompt-injection posture
- Document text is wrapped in delimited blocks and labelled "untrusted data". The system prompt says never to follow instructions found inside it.
- Output is limited to a fixed JSON schema. Extra keys are dropped.
- Excerpts must exist verbatim in the source, so the model can't invent evidence.
- The LLM has no tools and causes no side effects. Email is never sent.

---

## 5. Where the conditions live: `backend/app/core/rules.py`

```python
THRESHOLD_RADIUS_MILES = 150

def r1_identity(name, date) -> RuleResult:
    # FLAG if either is MISSING (takes precedence); UNKNOWN if INVALID/CONFLICT; else PASS

def r2_vehicle_count(declared, schedule) -> RuleResult:
    # UNKNOWN if either not KNOWN (missing/invalid/conflict/unreadable CSV)
    # FLAG if declared != schedule; PASS if equal (0 is valid)

def r3_radius(radius) -> RuleResult:
    # UNKNOWN if not KNOWN or negative; FLAG if > 150; PASS if <= 150

def r4_hazmat(hazmat) -> RuleResult:
    # UNKNOWN if not KNOWN; FLAG if True; PASS if False

def r5_loss_runs(loss_runs, paste_only: bool) -> RuleResult:
    # UNKNOWN if paste_only (no files supplied at all — project decision, see §13)
    # PASS if True; FLAG if False; UNKNOWN if null (unreadable/unclassifiable)

def overall_status(results) -> str:
    # READY_FOR_REVIEW only if all five PASS; else NEEDS_REVIEW
```

Each `RuleResult` carries `rule_id`, `status`, `reason`, `inputs_used` (the effective value, state, override flag) and `evidence`.

---

## 6. Data model (`backend/app/core/models.py`)

```text
Evidence      { source_file, locator, excerpt | observation }
Candidate     { source_file, raw_value, normalized_value, evidence, verified: bool }
FieldResult   { name, state: KNOWN|MISSING|CONFLICT|INVALID,
                value, candidates[], sources_checked[],
                override?: { value, note, applied_at } }
                # effective value = override.value if override else value
RuleResult    { rule_id, name, status: PASS|FLAG|UNKNOWN, reason, inputs_used, evidence[] }
StageStatus   { stage, status: ok|failed|skipped, message }
Attachment    { filename, type, readable, classified_as, pages }
SubmissionResult {
  submission_id, created_at, attachments[], fields{}, rules[], overall_status,
  summary?, draft_email?, narrative_source: "llm"|"unavailable", stages[]
}
```

---

## 7. API (`backend/app/api/routes.py`)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | App up; LLM provider/model configured (never returns the key) |
| POST | `/api/submissions` | Multipart: `files[]` and/or `email_text`. Runs the full pipeline and returns a `SubmissionResult` with a new ID |
| GET | `/api/submissions/{id}` | Fetch the current result |
| PUT | `/api/submissions/{id}/overrides/{field}` | `{value, note}`. Reruns normalise → reconcile → rules **without calling the LLM** and returns the updated result |
| DELETE | `/api/submissions/{id}/overrides/{field}` | Remove an override and rerun the rules |
| POST | `/api/submissions/{id}/narrative` | Regenerate the summary + draft email after overrides |

- Storage is an in-memory dict keyed by UUID. There's no database, which is in scope according to the brief.
- Each upload gets a new ID, so packets can't leak into each other.

---

## 8. UI (simple: one HTML page + vanilla JS, served by FastAPI)

There is no build step and no npm: three static files in `backend/app/static/`. The whole app runs with one `uvicorn` command at `http://localhost:8000`.

**Single page, top to bottom:**

1. **Header:** the app name, the overall status badge (READY_FOR_REVIEW / NEEDS_REVIEW), the model in use and a **New submission** button that clears everything.
2. **Upload:** a multi-file picker (email `.txt` + PDFs + CSV) **and/or** a textarea to paste the broker email text, plus an **Analyze** button. A status line shows *Processing… / Done / Failed: reason*, with each stage marked ✓ / ✗.
3. **Attachments:** a small table of filename, detected type and readable yes/no. This is the R5 evidence.
4. **Extracted fields:** a table of field, value, state (coloured tag), source and a **Correct** button. Clicking a row shows its evidence underneath (file · locator · "excerpt"). CONFLICT rows list both values with their evidence; MISSING rows say "Checked: broker_email.txt, application.pdf".
5. **Rule results:** five rows of rule, status (PASS green / FLAG red / UNKNOWN amber), reason and the values used.
6. **Underwriter summary:** AI text labelled "Generated by <model>", or "AI summary unavailable" with a **Retry** button.
7. **Draft follow-up email:** an editable `<textarea>` with a **Copy** button and the notice "Draft only, not sent". There is no send button.

**State:** one JS object, `currentSubmission`. New submission sets it to `null` and clears the DOM, and every render comes from the latest API response.

---

## 9. Project structure

```
knight-submission-review/
├── README.md                       # setup, run, model, AI tools, architecture, tests, time, limitations
├── plan.md                         # this file
├── .env.example                    # LLM_PROVIDER=anthropic|openai_compatible, LLM_MODEL=, ANTHROPIC_API_KEY=, LLM_BASE_URL=, LLM_API_KEY=
├── .gitignore                      # .env, __pycache__, .venv
├── Makefile                        # make install | run | test
│
├── backend/
│   ├── pyproject.toml              # pinned: fastapi, uvicorn, pydantic, pypdf, python-multipart, anthropic, httpx, pytest
│   ├── app/
│   │   ├── main.py                 # FastAPI app; serves static/ at "/"
│   │   ├── static/
│   │   │   ├── index.html          # the single page (section 8)
│   │   │   ├── app.js              # fetch calls + rendering
│   │   │   └── styles.css
│   │   ├── config.py               # env settings (pydantic-settings), no defaults for secrets
│   │   ├── api/
│   │   │   ├── routes.py           # endpoints in section 7
│   │   │   └── schemas.py          # request/response models (API contract)
│   │   ├── core/                   # pure, deterministic — no I/O, no LLM
│   │   │   ├── models.py           # Evidence, Candidate, FieldResult, RuleResult, ...
│   │   │   ├── normalize.py        # dates, states, numbers, yes/no, "Not provided"
│   │   │   ├── derived.py          # schedule_vehicle_count, loss_runs_present
│   │   │   ├── reconcile.py        # candidates → KNOWN/MISSING/CONFLICT/INVALID; overrides
│   │   │   └── rules.py            # R1–R5 + overall_status   ← all conditions
│   │   ├── ingest/
│   │   │   ├── loader.py           # classify uploads; attachment inventory
│   │   │   ├── email_reader.py     # .txt / pasted text
│   │   │   ├── pdf_reader.py       # pypdf, text per page
│   │   │   └── csv_reader.py       # header check, row records
│   │   ├── llm/
│   │   │   ├── client.py           # LLMClient protocol + factory from .env
│   │   │   ├── anthropic_client.py # Claude adapter
│   │   │   ├── openai_compatible.py# OpenAI / Ollama / LM Studio / vLLM / ...
│   │   │   ├── prompts.py          # extraction + narrative prompts (untrusted-data framing)
│   │   │   ├── extraction.py       # per-source call, JSON validation, one retry
│   │   │   ├── evidence.py         # verify excerpt exists in source text
│   │   │   └── narrative.py        # summary + draft email from rule results
│   │   ├── services/
│   │   │   ├── pipeline.py         # orchestrates stages 1–9, records StageStatus
│   │   │   └── store.py            # in-memory submission store
│   │   └── __init__.py
│   └── tests/
│       ├── conftest.py             # FakeLLM, sample paths
│       ├── test_rules.py           # every branch of R1–R5; R1 precedence; 150 / 150.0 / 150.1; 0 vehicles
│       ├── test_normalize.py       # CA/California, "Not provided", 150.0, bad dates, yes/no variants
│       ├── test_reconcile.py       # conflict keeps both; missing ≠ conflict; override keeps original
│       ├── test_derived.py         # blank CSV rows, header-only, unparseable CSV, zero-loss report, promise ≠ attachment, unreadable PDF
│       ├── test_evidence.py        # fabricated excerpt rejected
│       ├── test_pipeline.py        # SUB-A/B/C end-to-end with FakeLLM → section 3 table; LLM failure path
│       └── test_api.py             # TestClient: upload, override + rerun, isolation between packets
│
└── sample_submissions/             # copied unchanged from the exercise ZIP
    ├── SUB-A/  broker_email.txt, application.pdf, loss_runs.pdf, vehicles.csv
    ├── SUB-B/  broker_email.txt, application.pdf, vehicles.csv
    └── SUB-C/  broker_email.txt, application.pdf, vehicles.csv
```

**Dependency rule:** `core/` imports nothing from `llm/`, `ingest/` or `api/`. That's why the rules and reconciliation are testable without a model.

---

## 10. Testing strategy

- **Unit (core):** table-driven pytest for every PASS/FLAG/UNKNOWN branch. These cover the brief's required cases: missing information, a source conflict, unequal vehicle counts and the 150-mile threshold.
- **Pipeline:** `FakeLLM` returns fixed per-source extractions, so the SUB-A/B/C results must match section 3. A second fake raises an error to test the failure path: R2 and R5 are still computed, and the stage is marked failed.
- **API:** FastAPI `TestClient` covers upload, override then rerun (original kept, override labelled), a second upload having a new ID with no leaked data, and a bad upload returning 400.
- **Live smoke (optional, manual):** `make smoke` runs the real LLM on the 3 samples and prints a comparison against section 3. It is not part of `make test`.
- **UI:** manual walkthrough (it's in the video). Automated UI tests are out of scope given the time.

Command: `make test`, which runs `cd backend && pytest -q`.

---

## 11. Phase-wise execution

Each phase ends with an **exit gate** (what must be true) and a **checkpoint**: I report what was built and the test results, and you approve before the next phase starts.

```
P0 Setup ─▶ P1 Models+Normalize ─▶ P2 Reconcile+Derived+Rules ─▶ P3 Ingest
                                                                   │
              P4 LLM layer (adapters, extraction, evidence) ◀──────┘
                     │
                     ▼
              P5 Pipeline + API ─▶ P6 Narrative ─▶ P7 UI ─▶ P8 Hardening ─▶ P9 Docs & submission
 └──────── deterministic core, no LLM (P0–P3) ────────┘ └──── AI + delivery (P4–P9) ────┘
```

After P5 the app works end to end through the API. After P7 it can be demoed.

---

### Phase 0: Project setup
**Goal:** an empty project that installs, runs and tests from a clean checkout.
- Create the folder structure in section 9, plus `pyproject.toml` with pinned versions, `.gitignore` and `.env.example` (placeholders only).
- `Makefile` targets: `install`, `run`, `test`.
- Copy `sample_submissions/` from the ZIP unchanged.
- `main.py` serves a placeholder `index.html`; `GET /api/health` returns the configured provider and model.

**Exit gate:** `make install && make test` runs (0 tests); `make run` opens `localhost:8000`; no secrets in the repo.
**Checkpoint 0:** you review the skeleton.

---

### Phase 1: Data models + normalisation
**Goal:** the shared vocabulary and all the value-cleaning logic.
- `core/models.py`: `Evidence`, `Candidate`, `FieldResult` (state, value, candidates, sources checked, override), `RuleResult`, `StageStatus`, `Attachment`, `SubmissionResult`.
- `core/normalize.py`, one function per type:
  - text: trim; compare case-insensitively
  - date: YYYY-MM-DD only; an impossible date gives INVALID
  - state: `California` / `ca` → `CA`; unknown values give INVALID
  - number: `150`, `150.0`, `"150 miles"` → 150; non-numeric gives INVALID
  - yes/no: `Yes/No/true/false` → bool; anything ambiguous gives INVALID
  - `Not provided`, empty, `N/A` → MISSING

**Tests:** `test_normalize.py`
**Exit gate:** all normalisation tests pass.
**Checkpoint 1.**

---

### Phase 2: Reconcile + derived fields + rules, ← the conditions
**Goal:** every decision the app makes, in pure code, fully tested.
- `core/reconcile.py`:
  - candidates from the email and the PDF become one `FieldResult`
  - agreeing values → KNOWN; differing values → CONFLICT (value null, both kept); none → MISSING (sources checked listed); bad format → INVALID
  - apply / remove a human override, keeping the original
- `core/derived.py`:
  - `schedule_vehicle_count`: non-blank rows after the header; a bad header or unparseable file gives INVALID
  - `loss_runs_present`: decided by attachment content; true / false / null
- `core/rules.py`: R1–R5 + `overall_status`, exactly as in section 5, including the paste-only decision

**Tests:** `test_reconcile.py`, `test_derived.py`, `test_rules.py`. They cover the cases the brief requires:
- missing information (SUB-B style)
- a source conflict (SUB-C radius 180 vs 80; hazmat Yes vs No)
- unequal vehicle counts (4 vs 3)
- the 150-mile threshold (150 PASS, 150.0 PASS, 150.1 FLAG, −5 UNKNOWN)
- also: R1 precedence (missing + conflict → FLAG), zero vehicles valid, the CA vs California non-conflict, a promise isn't an attachment, a zero-loss report counts, paste-only gives UNKNOWN, an override keeps the original

**Exit gate:** all core tests pass with no network access.
**Checkpoint 2:** the most important review; you see every rule branch tested.

---

### Phase 3: Ingestion
**Goal:** turn uploaded files and pasted text into tagged source documents.
- `ingest/email_reader.py`: `.txt` file or pasted text → `SourceDoc(file, locator="email body", text)`
- `ingest/pdf_reader.py`: pypdf, text per page → locator "PDF page N"; an unreadable PDF is marked `readable=false`
- `ingest/csv_reader.py`: header check and row records → locator "CSV data rows 1–N (header excluded)"
- `ingest/loader.py`: sort uploads by type, build the attachment inventory, reject unsupported or empty files

**Tests:** `test_ingest.py` on SUB-A/B/C, plus a corrupt PDF and a header-only CSV.
**Exit gate:** every sample file is read into the right source type with locators; the inventory shows the SUB-A loss runs PDF as `loss_runs`.
**Checkpoint 3:** the deterministic core is complete.

---

### Phase 4: LLM layer, model-independent
**Goal:** extraction that works with Claude or any OpenAI-compatible model, and can't fabricate evidence.
- `llm/client.py`: the `LLMClient` protocol plus a factory that reads `.env`
- `llm/anthropic_client.py`, `llm/openai_compatible.py`: one `complete(system, user) -> str` each, with timeouts
- `llm/prompts.py`: an extraction prompt (field definitions from field_specification.json, untrusted-data framing, JSON-only output with value + verbatim excerpt for each field)
- `llm/extraction.py`: **one call per source** (email, application PDF); parse JSON → Pydantic; on invalid output, retry once with the error message, then mark the stage `failed`
- `llm/evidence.py`: the excerpt must appear in the source text after whitespace normalisation, otherwise the candidate is rejected (`verified=false`, not used)

**Tests:** `test_extraction.py` and `test_evidence.py` with `FakeLLM`, covering valid output, malformed JSON then a fixed retry, malformed twice, a fabricated excerpt, a provider exception, and an injection-style line in the email ("ignore previous instructions…") that has no effect on the rules.
**Manual check:** a live Claude run on the 3 samples; the extracted candidates match the documents.
**Exit gate:** tests pass offline; the live run is correct.
**Checkpoint 4:** you see the live extraction output.

---

### Phase 5: Pipeline + API
**Goal:** the full backend flow behind HTTP endpoints.
- `services/pipeline.py`: stages 1–9 with a `StageStatus` per stage; if a stage fails, the remaining deterministic results are still returned
- `services/store.py`: an in-memory dict; a new UUID for each upload
- `api/routes.py` + `schemas.py`: the 6 endpoints in section 7; an override reruns reconcile → rules **without** calling the LLM

**Tests:**
- `test_pipeline.py`: SUB-A/B/C with FakeLLM must match the section 3 table exactly; LLM down means R2's schedule side and R5 are still computed and the stage is marked failed
- `test_api.py`: upload, then override then rerun (R3 UNKNOWN → PASS in SUB-C, original kept), a second upload has a new ID with no leaked data, a bad upload returns 400, paste-only gives R2/R5 UNKNOWN

**Exit gate:** `make test` is fully green; the 3 samples work end to end with `curl` against live Claude.
**Checkpoint 5:** the backend is complete.

---

### Phase 6: Narrative
**Goal:** the AI-written underwriter summary and draft follow-up email.
- `llm/narrative.py`: input is **only** the structured results (fields, states, rule results), not the raw documents; output is `{summary, draft_email}`
- The draft email asks for each MISSING / CONFLICT / FLAG / UNKNOWN item, with no invented facts and no coverage promises
- If the call fails, `narrative_source="unavailable"` and the UI gets a clear error; there is never a fake "AI" text
- `POST /api/submissions/{id}/narrative` regenerates it after overrides

**Tests:** FakeLLM success and failure; the prompt input contains every open item.
**Exit gate:** the live narrative for SUB-B mentions the date, radius, hazmat and loss runs; the SUB-C narrative mentions the vehicle count and both conflicts.
**Checkpoint 6.**

---

### Phase 7: UI, plain HTML + JS
**Goal:** the single page in section 8.
- `static/index.html`, `app.js`, `styles.css`, built in this order:
  1. Upload / paste + Analyze + status line + New submission
  2. Attachments table
  3. Fields table with expandable evidence and a Correct button (override form with a note)
  4. Rule results
  5. Summary (with model label / unavailable + Retry)
  6. Draft email textarea + Copy + "Draft only, not sent"

**Manual test script (rehearsal for the video):**
1. Empty start → upload SUB-A → READY_FOR_REVIEW
2. New → SUB-C → see both conflicts → correct the radius to 80 → R3 PASS, original kept
3. New → SUB-B → missing fields + R5 FLAG → no SUB-C data visible
4. Paste-only email → R2/R5 UNKNOWN
5. Stop the network / use a bad key → failure state shown, rules still displayed

**Exit gate:** all 5 scenarios work in the browser.
**Checkpoint 7:** you try the app.

---

### Phase 8: Hardening + clean setup
- Fresh clone → new venv → `make install && make test && make run` following only the README
- Scan for secrets (`git grep` for key patterns); `.env` is git-ignored
- If available, a run with one OpenAI-compatible model (e.g. Ollama) to prove the app is model-independent; record which models were tested

**Exit gate:** the clean setup works first time.

---

### Phase 9: Docs + submission
- `README.md`: setup/run, provider/model + how to switch, AI coding tools (Claude Code), architecture diagram, where the rules live, tests + results, time spent, limitations, unfinished items, the deadline discrepancy, the paste-only R5 decision
- Filled-in `Submission_Template.txt` draft (you add personal details and the LA onsite answer)
- A 5–10 minute video script following the Phase 7 scenarios
- ZIP / repository link

**Exit gate:** the section 12 checklist is all ticked.

---

### Priority

The required flow (P0–P7 core) is never cut. Lowest-priority extras, dropped first if needed: the second-model test in P8 → the narrative Regenerate button → evidence styling polish.

---

## 12. Deliverables checklist

- [ ] Source ZIP / repository link (no `.env`, no keys)
- [ ] README: setup and run commands, provider/model, AI coding tools (Claude Code), architecture, tests, time spent, limitations, unfinished items, the deadline discrepancy note
- [ ] Tests passing with `make test` (no live LLM)
- [ ] 5–10 minute video: upload SUB-A → evidence → rules → switch to SUB-C (conflicts) → override radius → rerun → SUB-B (missing + loss-runs FLAG) → draft email
- [ ] Filled-in Submission_Template.txt

---

## 13. Decisions

| Decision | Choice |
|---|---|
| LLM | Claude or any OpenAI-compatible model, chosen in `.env` (section 4) |
| UI | Simple: one HTML page + vanilla JS served by FastAPI |
| Email intake | Manual upload or paste; no live mailbox (out of scope according to the brief) |
| Follow-up email | Editable draft + Copy button; never sent |
| Paste-only (no files at all) | **R2 → UNKNOWN and R5 → UNKNOWN**, reason "No attachments supplied (paste-only mode)". R2 follows rules.json. **R5 deliberately departs** from rules.json, whose literal reading would give FLAG; the README reports this, as START_HERE.txt asks. As soon as any file is uploaded, R5 follows the normal rule, so SUB-B and SUB-C stay FLAG. This is one line in `rules.py`, easy to change back |

## 13a. Human override: worked example

1. SUB-C: the email says radius **180** and the PDF says **80**, so the field is CONFLICT and R3 is UNKNOWN.
2. The underwriter confirms with the broker that it's 80 miles, clicks **Correct** on `operating_radius_miles`, enters `80` and adds the note "Confirmed by broker by phone".
3. The backend saves the override and reruns the rules **without calling the LLM**. R3 becomes PASS (80 ≤ 150). The overall status recalculates; it stays NEEDS_REVIEW in SUB-C because of R2, R4 and R5.
4. The row now shows `80` with a **Human override** tag. Underneath it still shows the original state CONFLICT with both excerpts (email "180", PDF page 1 "80") and the note.
5. **Remove override** returns the field to its extracted state. **Regenerate** updates the summary and draft email.

## 14. Known limitations (planned)

- In-memory store only: results are lost when the server restarts.
- No OCR. The brief says all PDFs have selectable text.
- Document-type classification for R5 is keyword/heading-based on the extracted text.
- No authentication or mailbox integration (out of scope according to the brief).
