# Submission Review Assistant

A small local web app that helps an underwriter review a fictional commercial auto submission. It reads a broker email and its attachments, extracts the specified fields **with source evidence**, evaluates rules **R1–R5 in plain code**, and shows an AI-written underwriter summary and an **editable draft** follow-up email. Nothing is ever sent.

- Upload a packet (email `.txt`/`.eml`, application/loss runs `.pdf`, vehicle schedule `.csv`) or paste the email.
- Each field shows its state (KNOWN / MISSING / CONFLICT / INVALID) and file · location · verbatim excerpt.
- Email-vs-application conflicts are shown side by side and never silently resolved.
- A reviewer can correct any field. The correction is labelled as a human override, the original extraction and evidence are kept, and the checks rerun immediately.
- Model-independent: works with Claude or any OpenAI-compatible model (OpenAI, Gemini, a local Ollama model, ...). No model or key is preconfigured; you choose one in `.env`.

---

## 1. Setup and run

Requires **Python 3.11+**. Tested on Python 3.13 on Linux and Windows.

```bash
make install            # creates .venv and installs exact pinned versions (backend/requirements.lock)
cp .env.example .env    # then set your model and key (see section 2)
make test               # 332 tests, no live model calls, no key needed
make run                # open http://127.0.0.1:8000
```

Without `make` (e.g. Windows):

```bash
python -m venv .venv
.venv/bin/python -m pip install -c backend/requirements.lock -e "backend[dev]"      # Windows: .venv\Scripts\python
cd backend && ../.venv/bin/python -m pytest                                        # tests
.venv/bin/python -m uvicorn app.main:app --app-dir backend --port 8000             # run (from project root)
```

Optional live check with the real model (makes billed API calls): `make smoke` prints extraction, evidence, rules, summary and draft email for all three sample packets.

The sample packets from the exercise are included unchanged in `sample_submissions/`.

---

## 2. Model / provider

No model or key ships with the project. Copy `.env.example` to `.env`, uncomment one option and fill in your own model name and key.

| Setting (`.env`) | Claude (Anthropic API) | Any OpenAI-compatible server |
|---|---|---|
| `LLM_PROVIDER` | `anthropic` | `openai_compatible` |
| `LLM_MODEL` | the Claude model to use | the model name your server expects |
| Key | `ANTHROPIC_API_KEY` | `LLM_API_KEY` (leave empty for local servers) |
| Other | `LLM_EFFORT` (default `medium`) | `LLM_BASE_URL`: the server's `/v1`-style base URL |

- **Model independence:** the app talks to models through one method, `complete(system, user) -> str`. Prompts are plain text, and our own code validates every reply, so no provider-specific features are required. The model in use is shown in the page header and on the summary.
- **Claude:** uses the official `anthropic` Python SDK. Server-side refusal fallback (`fallbacks: "default"`) is enabled for Claude models that support it.
- **OpenAI-compatible base URLs:** OpenAI `https://api.openai.com/v1`, Google Gemini `https://generativelanguage.googleapis.com/v1beta/openai/`, Ollama (local, no key) `http://localhost:11434/v1`, LM Studio `http://localhost:1234/v1`.
- **Rate limits:** free tiers can be small (Gemini's allowed about 20 requests per model per day when tested). Each analysis uses 3 model calls: one per source plus the narrative.
- **No model configured or model down:** the app still runs. Extraction and the narrative are shown as **failed** with the reason, and the deterministic results (CSV vehicle count, loss-runs check, rules) are still displayed. No simulated model output is ever shown.

---

## 3. Using the app

1. **Supply one packet:** choose or drop the files, and/or paste the broker email, then click **Analyze submission**. A file can be removed with its **×**.
2. **Processing:** each stage (ingest, extract, rules, narrative) shows ok / failed / skipped with a message.
3. **Rule results:** R1–R5 with status, reason and the values used, plus **Items to resolve** (computed by code).
4. **Extracted fields:** click a row to see its evidence. **Correct** saves a confirmed value with a note, and the checks rerun without a model call. **Remove override** restores the extracted state.
5. **Underwriter summary:** AI-generated and labelled with the model name. After a correction it is marked out of date, with a **Regenerate** button.
6. **Draft follow-up email:** editable, with **Copy to clipboard**. Draft only; it is never sent.

**Late documents:** the packet stays together after a review. If, say, the loss runs or the vehicle schedule arrive later, add them and click **Re-analyze packet**: the whole packet is reviewed again together, and reviewer corrections are carried over (applied before the rules and the narrative). Choosing a file with the same name replaces the earlier version.

**New submission** (next to the Analyze button) clears everything without reloading the page. Each analysis gets a new ID.

---

## 4. Architecture

```
Browser: one HTML page + plain JS  ──HTTP/JSON──▶  FastAPI (backend/app)
                                                    │
  ingest/    files → text per page / CSV rows; attachment inventory
  llm/       extraction: ONE call per source (email, application PDF), verbatim value + excerpt
             evidence check: excerpt must exist in the source; value must appear in it
  core/      normalise → derive (CSV count, loss runs) → reconcile (email vs PDF)
             → overrides → RULES R1–R5 → open items          ← no LLM, no I/O
  llm/       narrative: summary + draft email written FROM the rule results
  services/  pipeline (stage statuses) + in-memory store
```

| Folder | Responsibility |
|---|---|
| `backend/app/core/` | **Pure deterministic logic.** `models.py` (data types), `normalize.py`, `reconcile.py`, `derived.py`, **`rules.py` (all rule conditions)**, `open_items.py`. Imports nothing from `llm/`, `ingest/` or `api/` |
| `backend/app/ingest/` | Reads `.txt`/`.eml`, PDF (pypdf, layout mode, per page), CSV; classifies attachments; rejects bad packets |
| `backend/app/llm/` | `client.py` (interface + factory), `anthropic_client.py`, `openai_compatible.py`, `prompts.py`, `extraction.py`, `evidence.py`, `narrative.py` |
| `backend/app/services/` | `pipeline.py` (runs and records each stage), `store.py` |
| `backend/app/api/` | REST endpoints |
| `backend/app/static/` | The UI (`index.html`, `app.js`, `styles.css`) |

### API

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | Provider, model and whether it is configured (never the key) |
| POST | `/api/submissions` | Multipart `files` and/or `email_text`, plus optional `overrides` (JSON `{"field": {"value", "note"}}`, carried over on re-analysis); returns the full result (400 for an unusable packet, 422 for an unusable override) |
| GET | `/api/submissions/{id}` | Stored result |
| PUT / DELETE | `/api/submissions/{id}/overrides/{field}` | Set (`{"value","note"}`) or remove a human override; reruns the rules (no LLM call) |
| POST | `/api/submissions/{id}/narrative` | Regenerate the summary and draft email |

### Key design decisions

1. **The LLM reads; code decides.** The model only reports what a document says (value plus verbatim excerpt) and writes prose. Normalisation, conflict detection, rules, overall status and the list of items to ask the broker are all ordinary code.
2. **One model call per source.** The email and the application PDF are extracted separately, so the model never sees both and cannot pick a winner. `core/reconcile.py` compares them: agreeing → KNOWN, differing → CONFLICT (value null, both values and their evidence kept), silent source → not a conflict.
3. **Evidence is verified, not trusted.** An excerpt must occur in the source text (ignoring whitespace and case), and the value must appear in it as a whole token (so "80" is not accepted inside "180"). Unverified values are shown but never used. This also blunts prompt injection: a fabricated "Hazardous materials: No" is rejected because it is not in the document.
4. **Derived fields come from the files, not the email.** `schedule_vehicle_count` counts non-blank CSV rows after the header. `loss_runs_present` is decided by an attachment's heading, so an email promising loss runs never counts.
5. **Unread is not absent.** If extraction fails, fields become unresolved ("extraction failed; value unknown") rather than MISSING, so R1 gives UNKNOWN instead of a false "missing" FLAG.
6. **Open items are computed by code.** The narrative model turns them into prose. It receives only the structured results (never the raw documents) and is told not to approve, decline, quote, price or bind.
7. **Simple by choice:** no database, no frontend build, no agent framework.

---

## 5. Rules and sample results

All conditions are in [`backend/app/core/rules.py`](backend/app/core/rules.py). Each rule reads a field's *effective* value (an override if present), so corrections take effect while the original extraction is preserved.

| Rule | Order of checks |
|---|---|
| R1 Identity + effective date | any MISSING → **FLAG** (takes precedence) → any CONFLICT/INVALID → **UNKNOWN** → **PASS** |
| R2 Vehicle count | either count unresolved / CSV unreadable → **UNKNOWN** → different → **FLAG** → equal → **PASS** (0 = 0 passes) |
| R3 Radius | unresolved (checked *before* the threshold) or negative → **UNKNOWN** → `> 150` → **FLAG** → **PASS** (150 and 150.0 pass; 150.1 flags) |
| R4 Hazardous materials | not an explicit, non-conflicting Yes/No → **UNKNOWN** → Yes → **FLAG** → No → **PASS** |
| R5 Loss runs | readable loss runs/history report attached → **PASS**; none attached → **FLAG**; unreadable/unclassifiable candidate → **UNKNOWN** |
| Overall | **READY_FOR_REVIEW** only if all five PASS, else **NEEDS_REVIEW**. Neither is a coverage decision |

Expected results on the samples (worked out from the rules, and asserted by the tests):

| | R1 | R2 | R3 | R4 | R5 | Overall |
|---|---|---|---|---|---|---|
| SUB-A | PASS | PASS (3 = 3) | PASS (150) | PASS | PASS (zero-loss report counts) | READY_FOR_REVIEW |
| SUB-B | FLAG (date not provided) | PASS (2 = 2) | UNKNOWN (missing) | UNKNOWN (missing) | FLAG (only promised) | NEEDS_REVIEW |
| SUB-C | PASS | FLAG (4 ≠ 3) | UNKNOWN (email 180 vs PDF 80) | UNKNOWN (Yes vs No) | FLAG | NEEDS_REVIEW |

### Interpretation choices
- **Paste-only (no files at all):** R2 → UNKNOWN (no CSV), and **R5 → UNKNOWN** with "No attachments supplied (paste-only mode)". This is a deliberate project decision; a literal reading of rules.json would give R5 = FLAG. As soon as any file is uploaded, R5 follows the rule as written. It is one line in `rules.py` to change.
- One source gives a usable value and the other an unusable one (e.g. "3" vs "3 or 4"): the field is INVALID, so neither source is silently preferred.
- "Not provided", "N/A", "TBD", "Unknown" and blank are MISSING. Hazmat answers such as "None" or "Sometimes" are INVALID (not an explicit Yes/No). Absence is never treated as "No".
- `CA` / `California` normalise to `CA`. Text compares ignoring case, spacing and a trailing period (SUB-A's email ends its operations line with "." and the PDF does not).
- Numeric dates like `11/01/2026` are read as US month/day. Past and future dates are both allowed.
- A two-digit year means 20YY: `11/01/26` and `Nov 1, 26` are 2026-11-01.
- Counts and miles may be written in words: `two vehicles`, `twenty-five`, `a hundred and fifty miles`. Two different numbers (`two or three`, `3 or 4`) or a fraction in words (`one and a half`) are INVALID.
- Vehicle schedule: if the same VIN appears for different vehicle IDs (ignoring case, spaces and dashes), or the same vehicle ID appears twice, the count is INVALID, so R2 is UNKNOWN and the broker is asked to confirm whether it is a duplicate entry or a VIN / ID error. The question appears in Items to resolve and in the draft email.
- Fields are extracted from the broker email and the application PDF(s). Other PDFs are inventoried but not mined for fields.

---

## 6. Tests

```bash
make test        # 332 passed (about 5 s); no network, no key
```

| File | Covers |
|---|---|
| `test_rules.py` | Every PASS/FLAG/UNKNOWN branch; R1 precedence; **missing information**; **source conflict** before the threshold; **unequal vehicle counts**; **150 / 150.0 / 150.1 / −5**; zero vehicles; paste-only; overrides; all three samples |
| `test_reconcile.py` | Agree / conflict / silent source / invalid; CA vs California; unverified values ignored; override keeps the original |
| `test_normalize.py` | Dates (incl. two-digit years), states, counts and miles (incl. numbers in words), Yes/No, absence markers |
| `test_derived.py` | CSV counting (blank rows, header-only, bad header, unreadable, duplicate VINs / vehicle IDs); loss-runs detection (promise ≠ attachment, zero-loss report, unreadable) |
| `test_ingest.py` | Real sample files; corrupt, scanned, binary, empty and unsupported files; packet errors; isolation |
| `test_extraction.py` | Evidence check, reply parsing, retry, provider failure, hallucinated values, **prompt injection** |
| `test_llm_clients.py` | Both adapters with stubbed transports: request shape, error mapping, key never in errors |
| `test_pipeline.py`, `test_api.py`, `test_narrative.py` | End to end on the samples with a fake model; model-down behaviour; overrides over HTTP; re-analysis with a late file keeping corrections; packet isolation; open items and narrative; stale/regenerate |

During development the UI was also driven in Chromium (Playwright) through 27 checks: empty start, upload, evidence, an invalid then a valid correction, regenerate, switching packets with no leftover data, paste-only, the no-model failure state, phone-width layout and console errors. That browser script is not part of `make test`, and it predates the late-documents / **Re-analyze packet** flow, which was checked by hand and through its API test.

---

## 7. Security and privacy
- API keys are read from `.env` / environment only, stored as secret values, and never returned by the API or included in error messages. `.env` is git-ignored.
- Document text is treated as untrusted data: it is delimited in prompts and labelled untrusted, excerpts are verified against the source, and the UI inserts all document text with `textContent` (never as HTML).
- Uploads: at most 20 files and 10 MB each; directory parts of file names are stripped. The server binds to `127.0.0.1`.
- Only the supplied fictional data is used.

---

## 8. AI and development tools
- **Runtime model:** any model, chosen in `.env`: Claude via the Anthropic API, or any OpenAI-compatible model. The live verification in section 10 used Google Gemini through the OpenAI-compatible adapter.
- **AI coding assistant:** Claude Code (Anthropic) was used to plan, write code and tests, and review. I directed the design choices (architecture option, model independence, simple UI, paste-only R5 behaviour) and reviewed each phase. See `plan.md` for the phase plan.
- **Libraries:** FastAPI, Pydantic, pypdf, anthropic, httpx, pytest.

## 9. Time spent
Approximately 4 hours, including breaks.

---

## 10. Known limitations and unfinished items
- **Live model verification:** `make smoke` with a Google Gemini Flash-Lite model through the OpenAI-compatible adapter: SUB-A, SUB-B and SUB-C all matched the expected table in section 5 exactly, every stage succeeded, and all 48 extracted values passed the evidence check (none rejected). A larger Gemini Flash model was also run successfully through the app. A full live run with Claude was not done; a real request to the Anthropic API was confirmed to be sent and handled. Extraction quality of small local models is untested.
- **Rate limits:** the OpenAI-compatible adapter does not retry on 429 / 503. A busy or over-quota provider shows the stage as failed with the provider's message; clicking Analyze again retries. (The Anthropic SDK retries twice by itself.)
- **Known edge cases not handled** (found in review, documented rather than fixed): a radius written as a comparison (`over 150`, `150+`) is read as 150; a hazmat answer such as `No, except propane` is read as No; a totals row in the CSV is counted as a vehicle; an application PDF whose heading lacks the word "application", or a loss runs heading below the first three lines, is not recognised; an HTML-only `.eml` body is not read; differently worded operations descriptions in the email and the application are reported as a CONFLICT.
- **Storage:** results are kept in memory (last 100) and lost on restart. Two corrections saved to the same submission at the same instant could overwrite each other; acceptable for a single-user local tool.
- **Documents:** no OCR (scanned PDFs are reported as unreadable, as the brief scopes them out). Loss-runs detection is based on the document heading. Day/month numeric dates are not supported.
- **UI tests:** the browser walkthrough is not included in the automated suite.
- No authentication, mailbox connection or deployment (out of scope).

## 11. Notes for the reviewer
- The invitation email moved the deadline to **Sunday, October 18, 2026, 10:00 AM PT**; the brief and START_HERE.txt still state Saturday 11:59 PM.
- No inconsistencies were found between the PDF brief and the JSON rule/field files. The paste-only R5 behaviour above is the one deliberate deviation, and it is documented.

## Project structure
```
├── README.md · plan.md · .env.example · .gitignore · Makefile
├── backend/
│   ├── pyproject.toml · requirements.lock
│   ├── app/{api, core, ingest, llm, services, static}/ · main.py · config.py
│   ├── scripts/live_extract.py      (make smoke)
│   └── tests/                       (332 tests)
└── sample_submissions/SUB-A, SUB-B, SUB-C
```
