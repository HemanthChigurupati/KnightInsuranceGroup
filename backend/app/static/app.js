// Submission Review UI. Plain JavaScript, no build step.
// All document-derived text is inserted with textContent (never innerHTML),
// so content from broker files cannot run as code in the page.

"use strict";

const FIELD_LABELS = {
  business_name: "Business name",
  requested_effective_date: "Requested effective date",
  garaging_state: "Garaging state",
  operations_description: "Operations description",
  declared_vehicle_count: "Declared vehicle count",
  operating_radius_miles: "Operating radius (miles)",
  hazardous_materials: "Hazardous materials",
  loss_runs_present: "Loss runs report attached",
  schedule_vehicle_count: "Vehicle schedule count",
};
const YES_NO_FIELDS = new Set(["hazardous_materials", "loss_runs_present"]);
const KIND_LABELS = {
  broker_email: "Broker email", application: "Application", loss_runs: "Loss runs report",
  vehicle_schedule: "Vehicle schedule", other: "Other document", unknown: "Unknown",
};

let current = null;          // the SubmissionResult being shown
let openField = null;        // field row whose evidence is expanded
let correcting = null;       // field row whose correction form is open
let emailEdited = false;     // user changed the draft since it was generated
let selectedFiles = [];

const $ = (id) => document.getElementById(id);

// Small DOM builder: h("td", {class: "x"}, "text", childNode, ...)
function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") el.className = value;
    else if (key.startsWith("on")) el.addEventListener(key.slice(2), value);
    else el.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

function badge(text, cls = text) {
  return h("span", { class: `badge ${cls}` }, text.replaceAll("_", " "));
}

function fmt(value) {
  if (value === null || value === undefined) return "—";
  if (value === true) return "Yes";
  if (value === false) return "No";
  return String(value);
}

function setStatus(text, kind = "") {
  const el = $("status");
  el.textContent = text;
  el.className = `status ${kind}`;
}

// ---------------------------------------------------------------- API

async function api(method, path, body) {
  const options = { method };
  if (body instanceof FormData) options.body = body;
  else if (body !== undefined) {
    options.headers = { "Content-Type": "application/json" };
    options.body = JSON.stringify(body);
  }
  let resp;
  try {
    resp = await fetch(path, options);
  } catch {
    throw new Error("Could not reach the server. Is it running?");
  }
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    const detail = typeof data.detail === "string" ? data.detail : `Request failed (${resp.status})`;
    throw new Error(detail);
  }
  return data;
}

// ---------------------------------------------------------------- upload

// The packet (files + pasted email) stays together until "New submission", so
// late documents (e.g. loss runs sent a day later) are reviewed with the rest.

function refreshUploadState() {
  const hasInput = selectedFiles.length > 0 || $("email-text").value.trim() !== "";
  $("analyze-btn").disabled = !hasInput;
  $("analyze-btn").textContent = current ? "Re-analyze packet" : "Analyze submission";
  $("new-packet-btn").classList.toggle("hidden", !hasInput && !current);
  $("file-list").replaceChildren(...selectedFiles.map((f) =>
    h("li", {}, f.name, h("button", {
      type: "button", class: "remove", "aria-label": `Remove ${f.name}`, title: "Remove",
      onclick: () => removeFile(f.name),
    }, "×"))));
}

function markPacketChanged() {
  refreshUploadState();
  if (!current) return;
  setStatus("Packet changed. Click “Re-analyze packet” to review it with these files; the results below are from the previous run.");
}

function addFiles(fileList) {
  const incoming = [...fileList];
  $("files").value = "";  // so choosing the same file again still fires "change"
  if (!incoming.length) return;
  const byName = new Map(selectedFiles.map((f) => [f.name, f]));
  for (const f of incoming) byName.set(f.name, f);  // same name replaces the earlier version
  selectedFiles = [...byName.values()];
  markPacketChanged();
}

function removeFile(name) {
  selectedFiles = selectedFiles.filter((f) => f.name !== name);
  markPacketChanged();
}

// Reviewer corrections on the current results, re-applied when the packet is re-analysed.
function currentOverrides() {
  if (!current) return {};
  return Object.fromEntries(Object.entries(current.fields)
    .filter(([, f]) => f.override)
    .map(([name, f]) => [name, { value: f.override.value, note: f.override.note }]));
}

async function analyze(event) {
  event.preventDefault();
  if (current && emailEdited && !confirm("Re-analyzing rewrites the draft email and replaces your edits. Continue?")) return;
  const form = new FormData();
  for (const f of selectedFiles) form.append("files", f);
  const pasted = $("email-text").value.trim();
  if (pasted) form.append("email_text", pasted);
  const overrides = currentOverrides();
  const kept = Object.keys(overrides).length;
  if (kept) form.append("overrides", JSON.stringify(overrides));

  $("analyze-btn").disabled = true;
  setStatus("Processing… reading files and extracting with the model. This can take up to a minute.");
  try {
    const result = await api("POST", "/api/submissions", form);
    openField = null;
    correcting = null;
    show(result);
    const failed = result.stages.filter((s) => s.state === "failed");
    const keptNote = kept ? ` ${kept} reviewer correction(s) kept.` : "";
    if (failed.length) setStatus(`Done with problems: ${failed.map((s) => s.stage).join(", ")} failed. See Processing below.${keptNote}`, "error");
    else setStatus(`Done.${keptNote}`, "ok");
  } catch (err) {
    // Earlier results (if any) stay on screen; the packet is unchanged, so just retry.
    setStatus(`Failed: ${err.message}`, "error");
  } finally {
    refreshUploadState();
  }
}

function newSubmission() {
  if (current && emailEdited && !confirm("Discard this submission and your edits to the draft email?")) return;
  selectedFiles = [];
  $("files").value = "";
  $("email-text").value = "";
  clearResults();
  setStatus("");
  refreshUploadState();
  window.scrollTo({ top: 0 });
}

function clearResults() {
  current = null;
  openField = null;
  correcting = null;
  emailEdited = false;
  $("results").classList.add("hidden");
  $("overall").classList.add("hidden");
  for (const id of ["stages", "open-items"]) $(id).replaceChildren();
  for (const id of ["rules-table", "fields-table", "attachments-table"]) $(id).tBodies[0].replaceChildren();
  $("summary").replaceChildren();
  $("draft-email").value = "";
}

// ---------------------------------------------------------------- render

function show(result, { keepEmail = false } = {}) {
  current = result;
  $("results").classList.remove("hidden");
  const overall = $("overall");
  overall.className = `badge ${result.overall_status}`;
  overall.textContent = result.overall_status.replaceAll("_", " ");
  renderStages();
  renderRules();
  renderFields();
  renderAttachments();
  renderNarrative(keepEmail);
}

function renderStages() {
  $("stages").replaceChildren(
    ...current.stages.map((s) =>
      h("li", {}, h("span", { class: "name" }, s.stage), badge(s.state), h("span", { class: "msg" }, s.message))
    )
  );
}

function renderRules() {
  const rows = current.rules.map((r) => {
    const used = Object.entries(r.inputs_used).map(([name, info]) =>
      h("div", { class: "kv" },
        `${FIELD_LABELS[name]}: ${fmt(info.value)} (${info.state.toLowerCase()})`,
        info.human_override ? h("span", {}, " ", badge("human override", "override")) : null)
    );
    return h("tr", {},
      h("td", {}, h("strong", {}, r.rule_id), " ", r.name),
      h("td", {}, badge(r.status)),
      h("td", {}, r.reason),
      h("td", {}, used));
  });
  $("rules-table").tBodies[0].replaceChildren(...rows);

  const items = current.open_items;
  $("open-items").replaceChildren(
    ...(items.length
      ? items.map((i) => h("li", {}, i.rule_id ? h("strong", {}, `${i.rule_id}: `) : null, i.text))
      : [h("li", { class: "muted" }, "Nothing to resolve.")])
  );
}

function sourceSummary(field) {
  const verified = field.candidates.filter((c) => c.verified && c.evidence);
  if (verified.length) return [...new Set(verified.map((c) => `${c.evidence.source_file} · ${c.evidence.locator}`))].join("; ");
  if (field.sources_checked.length) return `Checked: ${field.sources_checked.join(", ")}`;
  return "—";
}

function renderFields() {
  const body = $("fields-table").tBodies[0];
  body.replaceChildren();
  for (const [name, field] of Object.entries(current.fields)) {
    const overridden = field.override !== null;
    const row = h("tr", { class: "clickable", onclick: () => toggle("evidence", name) },
      h("td", {}, FIELD_LABELS[name]),
      h("td", {}, h("strong", {}, fmt(field.effective_value))),
      h("td", {}, badge(field.effective_state), overridden ? h("span", {}, " ", badge("human override", "override")) : null),
      h("td", { class: "kv" }, sourceSummary(field)),
      h("td", {}, h("button", {
        type: "button", class: "btn small secondary",
        onclick: (e) => { e.stopPropagation(); toggle("correct", name); },
      }, "Correct")));
    body.append(row);
    if (openField === name || correcting === name) body.append(detailRow(name, field));
  }
}

function toggle(kind, name) {
  if (kind === "evidence") openField = openField === name ? null : name;
  else { correcting = correcting === name ? null : name; if (correcting) openField = name; }
  renderFields();
}

function detailRow(name, field) {
  const parts = [];
  if (field.reason) parts.push(h("div", {}, h("span", { class: "muted" }, "Reason: "), field.reason));

  if (field.candidates.length) {
    parts.push(h("ul", { class: "evidence" }, field.candidates.map((c) => {
      const ev = c.evidence || {};
      return h("li", {},
        h("span", { class: "src" }, `${c.source_file} · ${ev.locator || "—"}`), ": ",
        ev.excerpt ? h("span", { class: "excerpt" }, ev.excerpt) : h("em", {}, ev.observation || "no excerpt"),
        c.raw_value !== null && c.raw_value !== undefined ? h("span", { class: "kv" }, `  → ${fmt(c.raw_value)}`) : null,
        c.verified ? null : h("div", { class: "unverified" }, `Not used: ${c.note || "excerpt not found in the document"}`));
    })));
  } else {
    parts.push(h("div", { class: "kv" }, `No value found. Sources checked: ${field.sources_checked.join(", ") || "none"}`));
  }

  if (field.override) {
    parts.push(h("div", { class: "notice warn" },
      h("span", {}, h("strong", {}, "Human override: "), `${fmt(field.override.value)}`,
        field.override.note ? ` — "${field.override.note}"` : "",
        `. Extracted state was ${field.state}${field.value !== null ? ` (${fmt(field.value)})` : ""}.`),
      h("button", { type: "button", class: "btn small secondary", onclick: () => removeOverride(name) }, "Remove override")));
  }

  if (correcting === name) parts.push(correctionForm(name, field));
  return h("tr", { class: "detail" }, h("td", { colspan: "5" }, parts));
}

function correctionForm(name, field) {
  const current_ = field.effective_value;
  const input = YES_NO_FIELDS.has(name)
    ? h("select", { name: "value" },
        h("option", { value: "Yes", selected: current_ === true }, "Yes"),
        h("option", { value: "No", selected: current_ === false }, "No"))
    : h("input", {
        name: "value", required: true, value: current_ ?? "",
        placeholder: name === "requested_effective_date" ? "YYYY-MM-DD" : "Confirmed value",
      });
  const error = h("div", { class: "form-error" });
  const form = h("form", { class: "correct-form", onsubmit: (e) => saveOverride(e, name, error) },
    h("label", {}, "Corrected value "), input,
    h("input", { name: "note", placeholder: "Why? e.g. confirmed with broker by phone" }),
    h("button", { type: "submit", class: "btn small" }, "Save and rerun checks"),
    h("button", { type: "button", class: "btn small secondary", onclick: () => toggle("correct", name) }, "Cancel"),
    error);
  setTimeout(() => input.focus(), 0);
  return form;
}

async function saveOverride(event, name, errorEl) {
  event.preventDefault();
  const data = new FormData(event.target);
  try {
    const result = await api("PUT", `/api/submissions/${current.submission_id}/overrides/${name}`, {
      value: String(data.get("value")).trim(),
      note: String(data.get("note") || "").trim(),
    });
    correcting = null;
    show(result, { keepEmail: true });
    setStatus(`Correction saved for ${FIELD_LABELS[name]}; checks rerun.`, "ok");
  } catch (err) {
    errorEl.textContent = err.message;
  }
}

async function removeOverride(name) {
  try {
    show(await api("DELETE", `/api/submissions/${current.submission_id}/overrides/${name}`), { keepEmail: true });
    setStatus(`Override removed for ${FIELD_LABELS[name]}; checks rerun.`, "ok");
  } catch (err) {
    setStatus(`Failed: ${err.message}`, "error");
  }
}

function renderAttachments() {
  $("attachments-table").tBodies[0].replaceChildren(...current.attachments.map((a) =>
    h("tr", {},
      h("td", {}, a.filename),
      h("td", {}, KIND_LABELS[a.kind] || a.kind, a.pages ? h("span", { class: "kv" }, ` · ${a.pages} page(s)`) : null),
      h("td", {}, a.readable ? badge("Yes", "ok") : badge("No", "failed")),
      h("td", { class: "kv" }, a.note || ""))));
}

function renderNarrative(keepEmail) {
  const stage = current.stages.find((s) => s.stage === "narrative");
  const summary = $("summary");
  const label = $("narrative-label");

  if (current.narrative_source === "llm") {
    label.textContent = `AI-generated by ${current.model_name}`;
    summary.replaceChildren(current.summary);
  } else {
    label.textContent = "";
    summary.replaceChildren(h("div", { class: "notice error" },
      h("span", {}, "AI summary unavailable: ", stage ? stage.message : "not generated"),
      h("button", { type: "button", class: "btn small", onclick: regenerate }, "Retry")));
  }
  // A failed regeneration keeps the earlier text, so also surface that failure.
  if (current.narrative_source === "llm" && stage && stage.state === "failed") {
    summary.prepend(h("div", { class: "notice error" }, `Regeneration failed: ${stage.message}. Showing the previous text.`));
  }
  $("stale-notice").classList.toggle("hidden", !current.narrative_stale);

  if (!keepEmail || !emailEdited) {
    $("draft-email").value = current.draft_email || "";
    $("draft-email").placeholder = current.draft_email ? "" : "No AI draft available. Use the items to resolve above, or retry the summary.";
    emailEdited = false;
  }
}

async function regenerate() {
  if (emailEdited && !confirm("Regenerating replaces your edits to the draft email. Continue?")) return;
  setStatus("Rewriting the summary and draft email…");
  try {
    emailEdited = false;
    show(await api("POST", `/api/submissions/${current.submission_id}/narrative`));
    const stage = current.stages.find((s) => s.stage === "narrative");
    setStatus(stage.state === "ok" ? "Summary and email regenerated." : `Regeneration failed: ${stage.message}`, stage.state === "ok" ? "ok" : "error");
  } catch (err) {
    setStatus(`Failed: ${err.message}`, "error");
  }
}

async function copyEmail() {
  const text = $("draft-email").value;
  try {
    await navigator.clipboard.writeText(text);
    $("copy-status").textContent = "Copied.";
  } catch {
    $("draft-email").select();
    $("copy-status").textContent = "Select-all done; press Ctrl/Cmd+C to copy.";
  }
  setTimeout(() => { $("copy-status").textContent = ""; }, 3000);
}

// ---------------------------------------------------------------- wiring

async function loadHealth() {
  try {
    const health = await api("GET", "/api/health");
    const m = health.llm;
    $("model").textContent = m.configured
      ? `Model: ${m.provider}/${m.model}`
      : `Model: not configured (extraction and summary will fail; rules still run)`;
  } catch {
    $("model").textContent = "Model: server unreachable";
  }
}

function init() {
  $("upload-form").addEventListener("submit", analyze);
  $("files").addEventListener("change", (e) => addFiles(e.target.files));
  $("email-text").addEventListener("input", markPacketChanged);
  $("new-packet-btn").addEventListener("click", newSubmission);
  $("regen-btn").addEventListener("click", regenerate);
  $("copy-btn").addEventListener("click", copyEmail);
  $("draft-email").addEventListener("input", () => { emailEdited = true; });

  const zone = $("dropzone");
  zone.addEventListener("dragover", (e) => { e.preventDefault(); zone.classList.add("drag"); });
  zone.addEventListener("dragleave", () => zone.classList.remove("drag"));
  zone.addEventListener("drop", (e) => { e.preventDefault(); zone.classList.remove("drag"); addFiles(e.dataTransfer.files); });

  loadHealth();
  refreshUploadState();
}

init();
