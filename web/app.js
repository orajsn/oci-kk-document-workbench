"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const fieldLabels = {
    kk_number_raw: "Family card number", form_serial: "Form serial number", head_name: "Head of household",
    address: "Address", rt: "RT", rw: "RW", village: "Village or urban ward", district: "District",
    regency_city: "Regency or city", province: "Province", postal_code: "Postal code", issued_date_raw: "Issue date",
    row_number: "Row", full_name: "Full name", nik_raw: "NIK", sex: "Sex", birth_place: "Place of birth",
    birth_date_raw: "Date of birth", religion: "Religion", education: "Education", occupation: "Occupation",
    blood_group: "Blood group", marital_status: "Marital status", marriage_date_raw: "Marriage date",
    relationship_to_head: "Family relationship", nationality: "Citizenship", passport_number: "Passport number",
    residence_permit_number: "Residence permit", father_name: "Father’s name", mother_name: "Mother’s name",
    combined_parent_name: "Parent’s name (legacy layout)"
  };
  const evidenceLabels = {
    READABLE: "Readable", BLANK_ON_DOCUMENT: "Blank on card", UNREADABLE: "Unreadable",
    CROPPED: "Cropped", NOT_PRESENT_IN_LAYOUT: "Absent from this layout", AMBIGUOUS: "Ambiguous", NOT_REVIEWED: "Unreviewed"
  };
  const statusLabels = {
    QUEUED: "Queued", PROCESSING: "Processing", NEEDS_REVIEW: "Needs review", ACCEPTED: "Reviewed", FAILED: "Processing stopped"
  };
  const memberGroups = {
    identity: ["row_number", "full_name", "nik_raw", "sex", "birth_place", "birth_date_raw"],
    personal: ["row_number", "full_name", "religion", "education", "occupation", "blood_group", "marital_status", "marriage_date_raw"],
    family: ["row_number", "full_name", "relationship_to_head", "nationality", "passport_number", "residence_permit_number", "father_name", "mother_name", "combined_parent_name"]
  };
  const state = {
    config: null, jobs: [], selectedId: null, detail: null, draft: null, dirty: false,
    edited: new Set(), conflict: false, saving: false, uploading: false, imageChecking: false, loadingJobs: false,
    detailRequest: 0, jobsSignature: "", jobsRevision: 0, uploadKey: null, pendingNavigation: null,
    imageJobId: null, imageReady: false, rotation: 0, zoom: 1, imageFit: true,
    tab: "household", lastConnection: null, searchTimer: null, batch: [], batchStarted: false, batchSequence: 0,
    view: "overview", page: 1, detailOrder: [], overviewScroll: 0, dataWide: false, latestBatchIds: [], latestBatchCase: "", processingStatus: "UNKNOWN", processingStatusLoading: false, lastProcessingCheck: 0, cloudDisclosure: "", currentModelLabel: ""
  };

  class ApiError extends Error {
    constructor(message, status = 0, code = "") { super(message); this.status = status; this.code = code; }
  }
  async function api(path, options = {}) {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 30000);
    const headers = { Accept: "application/json" };
    const request = { method: options.method || "GET", headers, signal: controller.signal, credentials: "same-origin", cache: "no-store" };
    if (request.method !== "GET") {
      headers["Content-Type"] = "application/json";
      if (state.config?.csrf_token) headers["X-KK-Pilot-Token"] = state.config.csrf_token;
      request.body = JSON.stringify(options.body || {});
    }
    try {
      const response = await fetch(path, request);
      let data;
      try { data = await response.json(); } catch { throw new ApiError("Could not read the service response. Reload the page.", response.status); }
      if (!response.ok) {
        const message = typeof data?.error?.message === "string" ? data.error.message : "Request failed. Try again.";
        throw new ApiError(message, response.status, data?.error?.code || "");
      }
      return data;
    } catch (error) {
      if (error instanceof ApiError) throw error;
      if (error.name === "AbortError") throw new ApiError("The service has not responded. Check the connection and try again.");
      throw new ApiError("Cannot reach the service. Check the connection and try again.");
    } finally { window.clearTimeout(timeout); }
  }
  function el(tag, className, value) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (value !== undefined && value !== null) node.textContent = String(value);
    return node;
  }
  function visible(id, show) { $(id).hidden = !show; }
  function announce(message) { $("announcer").textContent = message; }
  function clone(value) { return JSON.parse(JSON.stringify(value)); }
  function shortDate(value) {
    const date = new Date(value);
    if (!value || Number.isNaN(date.getTime())) return "";
    return new Intl.DateTimeFormat("en-US", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }).format(date);
  }
  function badge(status) {
    const known = Object.hasOwn(statusLabels, status) ? status : "QUEUED";
    return el("span", `status-badge status-${known.toLowerCase().replaceAll("_", "-")}`, statusLabels[status] || String(status || "Unknown"));
  }
  function setConnection(online) {
    state.lastConnection = online;
    const node = $("connection-status");
    node.className = `connection ${online ? "online" : "offline"}`;
    const readyLabel = "Connected";
    node.replaceChildren(el("span", "status-dot"), document.createTextNode(online ? readyLabel : "Disconnected"));
    node.setAttribute("aria-label", online ? readyLabel : "Disconnected");
  }
  function showGlobal(message, retry = false) {
    const node = $("global-message");
    node.replaceChildren(document.createTextNode(message));
    if (retry) {
      const button = el("button", "button button-secondary", "Reconnect");
      button.type = "button"; button.addEventListener("click", initialize); node.append(button);
    }
    node.hidden = false;
  }
  function showReviewMessage(message, error = false) {
    const node = $("review-message"); node.textContent = message;
    node.className = `form-message${error ? " error" : ""}`; node.hidden = false;
  }
  function markDirty() {
    state.dirty = true;
    visible("unsaved-label", true);
    visible("review-message", false);
    $("panel-flags").querySelector(".checks-note").textContent = "Save your changes to refresh the issues.";
  }
  function resetDirty() { state.dirty = false; state.edited.clear(); visible("unsaved-label", false); }
  function requestNavigation(action) {
    if (state.saving) { announce("Wait for the review to finish saving."); return; }
    if (!state.dirty) { action(); return; }
    state.pendingNavigation = action;
    $("discard-dialog").showModal();
    $("keep-editing").focus();
  }
  function normalizePath(path) {
    return String(path || "").replace(/^\$\.?/, "").replace(/^\//, "").replace(/\[(\d+)\]/g, ".$1").replaceAll("/", ".");
  }
  function humanPath(path) {
    const normalized = normalizePath(path);
    const match = normalized.match(/^members\.(\d+)\.(.+)$/);
    if (match) {
      const index = Number(match[1]);
      const row = state.draft?.members?.[index]?.row_number ?? (index + 1);
      return `Member row ${row} · ${fieldLabels[match[2]] || match[2]}`;
    }
    if (normalized.startsWith("household.")) return fieldLabels[normalized.slice(10)] || normalized.slice(10);
    return fieldLabels[normalized] || normalized || "Document";
  }
  function evidenceFor(path) {
    const normalized = normalizePath(path);
    return (state.draft?.field_evidence || []).find((item) => normalizePath(item.path) === normalized);
  }
  function canonicalPath(path) { return normalizePath(path).replace(/^members\.(\d+)\./, "members[$1]."); }
  function evidenceRecord(path, status) {
    const normalized = normalizePath(path); const match = normalized.match(/^members\.(\d+)\./);
    const item = { path: canonicalPath(path), status, section: match ? "members" : "household" };
    if (match) item.row_number = state.draft.members[Number(match[1])].row_number;
    return item;
  }
  function prepareEvidence() {
    if (!Array.isArray(state.draft.field_evidence)) state.draft.field_evidence = [];
    const paths = state.config.household_fields.map((field) => `household.${field}`);
    for (let index = 0; index < state.draft.members.length; index += 1) {
      for (const field of state.config.member_fields) paths.push(`members.${index}.${field}`);
    }
    for (const path of paths) {
      if (!evidenceFor(path)) state.draft.field_evidence.push(evidenceRecord(path, "NOT_REVIEWED"));
    }
  }
  function modeLabel(mode) {
    return {demo: "Synthetic example", reference: "Recorded sample", oci: state.currentModelLabel || "OCI Generative AI"}[mode] || String(mode || "Unknown");
  }
  function checks() {
    const all = [];
    for (const item of state.detail?.validation?.issues || []) {
      if (typeof item === "string") all.push({ path: "", message: item, source: "Validation" });
      else all.push({ ...item, source: "Validation" });
    }
    for (const item of state.draft?.review_flags || []) {
      // Training mode is disclosed in the source label. It is not a field discrepancy.
      if ((state.detail.job.mode === "reference" && item.code === "REFERENCE_REPLAY") ||
          (state.detail.job.mode === "demo" && item.code === "SYNTHETIC_DEMO")) continue;
      all.push({ ...item, source: "Review" });
    }
    for (const item of state.draft?.field_evidence || []) {
      if (["UNREADABLE", "CROPPED", "AMBIGUOUS"].includes(item.status)) {
        all.push({ path: item.path, code: item.status, message: item.note || evidenceLabels[item.status], source: "Image evidence" });
      }
    }
    const seen = new Set();
    return all.filter((item) => {
      const key = `${normalizePath(item.path)}|${item.code || ""}|${item.message || ""}`;
      if (seen.has(key)) return false;
      seen.add(key); return true;
    });
  }
  function fieldWarnings(path) { return checks().filter((item) => normalizePath(item.path) === normalizePath(path)); }
  function fieldInput(path, field, value, isTable = false) {
    const input = el("input", isTable ? "member-cell-input" : "");
    input.type = field === "row_number" ? "number" : "text"; input.value = value === null || value === undefined ? "" : String(value);
    input.autocomplete = "off"; input.spellcheck = false; input.maxLength = 2000;
    if (field === "row_number") { input.min = "1"; input.max = "100"; input.step = "1"; input.required = true; input.classList.add("row-number-input"); }
    input.id = `field-${path.replaceAll(".", "-")}`;
    input.setAttribute("aria-label", humanPath(path));
    input.placeholder = "Empty";
    if (field === "nik_raw" || field === "kk_number_raw" || field === "postal_code") input.inputMode = "text";
    const warnings = fieldWarnings(path);
    if (warnings.length) input.classList.add("flagged");
    if (state.edited.has(path)) input.classList.add("edited");
    input.disabled = state.saving;
    input.addEventListener("input", () => {
      const parts = path.split(".");
      if (parts[0] === "household") state.draft.household[field] = input.value === "" ? null : input.value;
      else if (field === "row_number") {
        const index = Number(parts[1]);
        state.draft.members[index].row_number = Number.isFinite(input.valueAsNumber) ? input.valueAsNumber : null;
        for (const item of state.draft.field_evidence || []) {
          if (normalizePath(item.path).startsWith(`members.${index}.`) && Object.hasOwn(item, "row_number")) item.row_number = state.draft.members[index].row_number;
        }
      } else state.draft.members[Number(parts[1])][field] = input.value === "" ? null : input.value;
      state.edited.add(path); input.classList.add("edited"); markDirty();
    });
    return input;
  }
  function evidenceNode(path) {
    const evidence = evidenceFor(path);
    if (!evidence) return null;
    const matching = state.draft.field_evidence.filter((item) => normalizePath(item.path) === normalizePath(path));
    const kind = evidence.status === "READABLE" ? " readable" : ["UNREADABLE", "CROPPED", "AMBIGUOUS", "NOT_REVIEWED"].includes(evidence.status) ? " warning" : "";
    const node = el("select", `evidence-label evidence-select${kind}`);
    node.setAttribute("aria-label", `Readability of ${humanPath(path)}`);
    if (matching.length > 1) { const option = el("option", "", `Review ${matching.length} notes`); option.value = ""; option.disabled = true; node.append(option); node.classList.add("warning"); }
    for (const status of state.config.evidence_statuses || Object.keys(evidenceLabels)) {
      const option = el("option", "", evidenceLabels[status] || status); option.value = status; node.append(option);
    }
    node.value = matching.length > 1 ? "" : evidence.status;
    if (evidence.note) node.title = String(evidence.note);
    node.addEventListener("change", () => {
      const replacement = evidenceRecord(path, node.value);
      if (typeof evidence.note === "string") replacement.note = evidence.note;
      state.draft.field_evidence = state.draft.field_evidence.filter((item) => normalizePath(item.path) !== normalizePath(path));
      state.draft.field_evidence.push(replacement);
      if (node.querySelector('option[value=""]')) node.querySelector('option[value=""]').remove();
      state.edited.add(`${path}.evidence`); markDirty();
      node.classList.toggle("readable", node.value === "READABLE");
      node.classList.toggle("warning", ["UNREADABLE", "CROPPED", "AMBIGUOUS", "NOT_REVIEWED"].includes(node.value));
    });
    return node;
  }
  function attachFieldNotes(container, path, input) {
    // The evidence control and section guidance already explain the generic review state.
    // Keep substantive validation failures and document evidence notes beside their fields.
    const notes = fieldWarnings(path).filter(item => !(item.source === "Validation" && item.code === "NOT_REVIEWED" && item.message === "Review this field against the source before acceptance."));
    if (!notes.length) return;
    const note = el("span", "field-note", [...new Set(notes.map((item) => item.message || item.code || "Check this field"))].join(" · "));
    note.id = `${input.id}-note`; input.setAttribute("aria-describedby", note.id); container.append(note);
  }
  function renderHousehold() {
    const container = $("household-fields"); container.replaceChildren();
    for (const field of state.config.household_fields) {
      const path = `household.${field}`;
      const item = el("div", `field-item${["address", "head_name"].includes(field) ? " full-width" : ""}`);
      const labelRow = el("div", "field-label-row"); const label = el("label", "", fieldLabels[field] || field);
      const input = fieldInput(path, field, state.draft.household?.[field]); label.htmlFor = input.id;
      labelRow.append(label); const evidence = evidenceNode(path); if (evidence) labelRow.append(evidence);
      item.append(labelRow, input); attachFieldNotes(item, path, input); container.append(item);
    }
  }
  function renderMembers() {
    const requested = $("member-group").value === "all" ? state.config.member_fields : (memberGroups[$("member-group").value] || memberGroups.identity);
    const fields = requested.filter((field) => state.config.member_fields.includes(field));
    const head = $("member-table-head"); const body = $("member-table-body"); head.replaceChildren(); body.replaceChildren();
    const headerRow = el("tr");
    for (const field of fields) { const th = el("th", "", fieldLabels[field] || field); th.scope = "col"; headerRow.append(th); }
    head.append(headerRow);
    const members = state.draft.members || []; visible("members-empty", !members.length);
    $("member-table").closest(".table-scroll").hidden = !members.length;
    for (let index = 0; index < members.length; index += 1) {
      const member = members[index]; const row = el("tr");
      for (const field of fields) {
        const cell = el("td");
        {
          const path = `members.${index}.${field}`; const input = fieldInput(path, field, member[field], true); cell.append(input);
          const evidence = evidenceNode(path); if (evidence) cell.append(evidence);
          attachFieldNotes(cell, path, input);
        }
        row.append(cell);
      }
      body.append(row);
    }
  }
  function renderChecks() {
    const items = checks(); $("flag-count").textContent = String(items.length);
    const container = $("checks-list"); container.replaceChildren();
    if (!items.length) {
      const item = el("div", "check-card clear");
      item.append(el("strong", "", "No formatting issues"), el("p", "", "Compare the values with the image before completing the review.")); container.append(item); return;
    }
    for (const item of items) {
      const card = el("div", "check-card");
      card.append(el("strong", "", humanPath(item.path)), el("p", "", item.message || item.note || item.code || "Needs review"), el("p", "check-source", `${item.source}${item.code ? ` · ${item.code}` : ""}`));
      container.append(card);
    }
  }
  function renderHistory() {
    const history = state.detail.review_history || []; const list = $("review-history"); list.replaceChildren();
    $("history-count").textContent = history.length ? `(${history.length})` : ""; visible("history-empty", !history.length);
    for (const entry of [...history].reverse()) {
      const item = el("li", "history-entry"); const top = el("div", "history-top");
      top.append(badge(entry.decision || entry.status), el("span", "", `Version ${entry.to_version ?? entry.version ?? entry.new_version ?? "—"} · ${shortDate(entry.created_at || entry.timestamp)}`));
      item.append(top);
      if (entry.comment) item.append(el("p", "history-note", entry.comment));
      list.append(item);
    }
  }
  function hasResult(job) {
    return typeof job.result_available === "boolean" ? job.result_available : ["NEEDS_REVIEW", "ACCEPTED"].includes(job.status);
  }
  function needsDispatchCheck(job) {
    return job.status === "QUEUED" && job.dispatch_unconfirmed === true;
  }
  function activityStatus(job) {
    return needsDispatchCheck(job) ? "FAILED" : job.status;
  }
  function processingIssue(job) {
    if (needsDispatchCheck(job)) return "Check whether the file reached the queue before submitting it again.";
    if (job.last_error === "PRIOR_OUTCOME_UNKNOWN") return "Processing stopped before the result was saved. Check the previous request before retrying.";
    return typeof job.last_error === "string" ? job.last_error : job.last_error?.message || "Processing stopped. Contact the application operator.";
  }
  function filteredJobs() {
    const query = $("job-search").value.toLocaleLowerCase().trim();
    const filter = $("status-filter").value, scope = $("list-scope").value;
    const jobs = state.jobs.filter((job) => {
      const statusMatch = !filter || (filter === "ACTIVE" ? ["QUEUED", "PROCESSING"].includes(activityStatus(job)) : filter === "HAS_RESULT" ? hasResult(job) : activityStatus(job) === filter);
      return statusMatch && (scope !== "batch" || state.latestBatchIds.includes(job.id)) && (!query || `${job.case_id || ""} ${job.filename || ""}`.toLocaleLowerCase().includes(query));
    });
    const dateValue = (job, field) => Date.parse(job[field]) || 0;
    const sort = $("job-sort").value;
    return jobs.sort((a, b) => {
      const difference = sort === "case" ? String(a.case_id || "").localeCompare(String(b.case_id || ""), undefined, { numeric: true })
        : sort === "filename" ? String(a.filename || "").localeCompare(String(b.filename || ""), undefined, { numeric: true })
        : sort === "oldest" ? dateValue(a, "created_at") - dateValue(b, "created_at")
        : sort === "updated" ? dateValue(b, "updated_at") - dateValue(a, "updated_at")
        : dateValue(b, "created_at") - dateValue(a, "created_at");
      return difference || String(a.id).localeCompare(String(b.id));
    });
  }
  function resetOverviewFilters() {
    $("job-search").value = ""; $("status-filter").value = ""; $("list-scope").value = "all";
    state.page = 1; renderJobList();
  }
  function updateDetailNavigation() {
    const index = state.detailOrder.indexOf(state.selectedId);
    $("detail-position").textContent = index >= 0 ? `${index + 1} of ${state.detailOrder.length} documents` : "Selected document";
    $("previous-document").disabled = state.saving || index <= 0;
    $("next-document").disabled = state.saving || index < 0 || index >= state.detailOrder.length - 1;
    $("back-to-overview").disabled = state.saving;
    const jump = $("document-jump");
    // Do not rebuild an open select while the operator is choosing a document.
    if (document.activeElement !== jump) {
      jump.replaceChildren();
      for (const id of state.detailOrder) {
        const job = state.jobs.find(item => item.id === id);
        const option = el("option", "", job ? `${job.case_id || "Document"} · ${job.filename || "Image"}` : "Selected document");
        option.value = id; jump.append(option);
      }
      jump.value = state.selectedId || "";
    }
    jump.disabled = state.saving || !state.detailOrder.length;
  }
  function showOverview() {
    const previousId = state.selectedId;
    state.detailRequest += 1; state.selectedId = null; state.detail = null; state.draft = null;
    state.view = "overview"; resetDirty(); state.conflict = false;
    document.body.classList.add("view-overview"); document.body.classList.remove("view-detail");
    visible("overview", true); visible("welcome", true); visible("detail-workspace", false);
    visible("detail", false); visible("detail-loading", false); visible("detail-error", false);
    renderJobList(); document.title = "Family Card Workbench";
    requestAnimationFrame(() => {
      const button = [...$("job-list").querySelectorAll("button[data-job-id]")].find(node => node.dataset.jobId === previousId);
      (button || $("overview-title")).focus({ preventScroll: true });
      window.scrollTo(0, state.overviewScroll);
    });
  }
  function openDocument(id) {
    const filtered = filteredJobs().map(job => job.id);
    state.detailOrder = filtered.includes(id) ? filtered : state.jobs.map(job => job.id);
    if (!state.detailOrder.includes(id)) state.detailOrder.unshift(id);
    loadDetail(id);
  }
  function moveDocument(offset) {
    const index = state.detailOrder.indexOf(state.selectedId), next = state.detailOrder[index + offset];
    if (next) requestNavigation(() => loadDetail(next));
  }
  function renderActivity() {
    const batch = $("list-scope").value === "batch";
    const jobs = batch ? state.jobs.filter(job => state.latestBatchIds.includes(job.id)) : state.jobs;
    const queued = jobs.filter(job => activityStatus(job) === "QUEUED").length;
    const running = jobs.filter(job => job.status === "PROCESSING").length;
    const failed = jobs.filter(job => activityStatus(job) === "FAILED").length;
    const saved = jobs.filter(hasResult).length;
    visible("activity-panel", queued + running + failed > 0);
    $("activity-summary").textContent = `${saved} of ${jobs.length} results saved · ${queued} queued · ${running} processing`;
    $("activity-progress").max = Math.max(1, jobs.length);
    $("activity-progress").value = saved;
    $("activity-failed").hidden = !failed;
    $("activity-failed").textContent = `${failed} need attention`;
  }
  function renderJobList() {
    const jobs = filteredJobs();
    renderActivity();
    const pageSize = Number($("page-size").value) || 25, pages = Math.max(1, Math.ceil(jobs.length / pageSize));
    state.page = Math.min(Math.max(1, state.page), pages);
    const start = (state.page - 1) * pageSize, shown = jobs.slice(start, start + pageSize);
    $("job-count").textContent = String(state.jobs.length);
    $("stat-total-value").textContent = String(state.jobs.length);
    $("stat-processing-value").textContent = String(state.jobs.filter(job => ["QUEUED", "PROCESSING"].includes(activityStatus(job))).length);
    $("stat-review-value").textContent = String(state.jobs.filter(job => job.status === "NEEDS_REVIEW").length);
    $("stat-reviewed-value").textContent = String(state.jobs.filter(job => job.status === "ACCEPTED").length);
    const batchOption = $("list-scope").querySelector('option[value="batch"]');
    batchOption.disabled = !state.latestBatchIds.length;
    batchOption.textContent = state.latestBatchIds.length ? `Last upload (${state.latestBatchIds.length})` : "Last upload";
    const batchView = $("list-scope").value === "batch";
    const optionsActive = batchView || $("status-filter").value || $("job-sort").value !== "newest";
    $("list-options").querySelector("summary").textContent = optionsActive ? "Filter and sort · active" : "Filter and sort";
    $("list-scope-description").textContent = batchView ? `Upload ${state.latestBatchCase || "this session"}.` : "Showing up to 500 recent documents.";
    for (const button of document.querySelectorAll("[data-stat-filter]")) {
      const active = !batchView && !$("job-search").value.trim() && button.dataset.statFilter === $("status-filter").value;
      button.classList.toggle("is-active", active); button.setAttribute("aria-pressed", String(active));
    }
    const focusedId = document.activeElement?.dataset?.jobId;
    const list = $("job-list"); list.replaceChildren();
    for (const job of shown) {
      const row = el("tr", job.id === state.selectedId ? "selected" : "");
      const documentCell = el("td", "document-cell"), documentWrap = el("div", "document-cell-content");
      const icon = el("span", "document-file-icon", "KK"); icon.setAttribute("aria-hidden", "true");
      const documentText = el("div", "document-file-text");
      const caseName = el("strong", "document-case", job.case_id || "No reference"), filename = el("span", "document-filename", job.filename || "Unnamed file");
      filename.title = job.filename || ""; documentText.append(caseName, filename); documentWrap.append(icon, documentText); documentCell.append(documentWrap);
      const processing = el("td", "document-processing");
      const processKind = activityStatus(job) === "FAILED" ? "failed" : job.status === "PROCESSING" ? "processing" : job.status === "QUEUED" ? "queued" : hasResult(job) ? "complete" : "pending";
      const processLabel = needsDispatchCheck(job) ? "Check delivery" : { failed: "Stopped", processing: "Processing", queued: "Queued", complete: "Complete", pending: "Awaiting result" }[processKind];
      processing.append(el("span", `process-badge process-${processKind}`, processLabel));
      const members = el("td", "document-members", Number.isInteger(job.member_count) && job.member_count >= 0 ? job.member_count : "—");
      if (!Number.isInteger(job.member_count)) members.title = hasResult(job) ? "Open the card to see its members." : "Available after extraction.";
      const review = el("td", "document-review");
      review.append(hasResult(job) ? badge(job.status === "ACCEPTED" ? "ACCEPTED" : "NEEDS_REVIEW") : el("span", "cell-pending", "Awaiting result"));
      const updated = el("td", "document-updated", shortDate(job.updated_at || job.created_at) || "—");
      const action = el("td", "document-open"), button = el("button", "button button-secondary open-document", hasResult(job) ? "Open result" : "View status");
      button.type = "button"; button.dataset.jobId = job.id; button.setAttribute("aria-label", `Open ${job.case_id || "document"}, ${job.filename || "image"}`);
      button.addEventListener("click", () => requestNavigation(() => openDocument(job.id))); action.append(button);
      row.append(documentCell, processing, members, review, updated, action); list.append(row);
      if (focusedId === job.id) button.focus({ preventScroll: true });
    }
    const filtered = !!$("job-search").value.trim() || !!$("status-filter").value || batchView;
    visible("queue-empty", !jobs.length && !state.loadingJobs && $("queue-error").hidden); visible("documents-table-wrap", !!jobs.length);
    $("queue-empty-title").textContent = filtered ? "No matching documents" : "No documents yet";
    $("queue-empty-copy").textContent = filtered ? "Change the search or clear the filters to see other documents." : "Upload a photo or scan of a family card to begin.";
    visible("welcome-upload", !filtered); visible("clear-filters", filtered);
    $("page-summary").textContent = jobs.length ? `${start + 1}–${Math.min(start + pageSize, jobs.length)} of ${jobs.length} documents` : `0 documents`;
    $("previous-page").disabled = state.page <= 1; $("next-page").disabled = state.page >= pages;
    updateDetailNavigation();
  }
  async function loadJobs(silent = false) {
    if (state.loadingJobs) return;
    state.loadingJobs = true;
    if (!silent) { visible("queue-loading", true); visible("queue-error", false); visible("queue-empty", false); }
    $("refresh-jobs").disabled = true;
    const requestRevision = state.jobsRevision;
    try {
      const data = await api("/api/jobs");
      if (!Array.isArray(data.jobs)) throw new ApiError("Could not read the document list. Reload the page.");
      if (state.jobsRevision !== requestRevision) { visible("queue-error", false); setConnection(true); return; }
      const signature = JSON.stringify(data.jobs);
      state.jobs = data.jobs;
      if (signature !== state.jobsSignature || !silent) { state.jobsSignature = signature; renderJobList(); }
      visible("queue-error", false); setConnection(true);
    } catch (error) {
      setConnection(false);
      if (!silent || !state.jobs.length) { visible("queue-error", true); visible("queue-empty", false); }
      if (!silent) announce(error.message);
    } finally { state.loadingJobs = false; visible("queue-loading", false); $("refresh-jobs").disabled = false; if (!$("queue-error").hidden && !state.jobs.length) visible("queue-empty", false); else renderJobList(); }
  }
  function updateListJob(job) {
    state.jobsRevision += 1;
    const index = state.jobs.findIndex((item) => item.id === job.id);
    if (index < 0) state.jobs.unshift(job); else state.jobs[index] = { ...state.jobs[index], ...job };
    state.jobsSignature = ""; renderJobList();
  }
  let nikStatusEpoch = 0, nikStatusTimer = null;
  function refreshNikRoute(id) {
    const epoch = ++nikStatusEpoch;
    clearTimeout(nikStatusTimer);
    const panel = $("nik-routing"), text = $("nik-routing-text"), link = $("nik-routing-link");
    panel.hidden = true; link.hidden = true;
    if (state.config?.runtime !== "private_cloud") return;
    const poll = async () => {
      if (state.selectedId !== id || epoch !== nikStatusEpoch) return;
      try {
        const info = await api("/api/jobs/" + encodeURIComponent(id) + "/nik-routing");
        if (state.selectedId !== id || epoch !== nikStatusEpoch) return;
        if (info.stage === "previous" && !info.review) return;
        panel.hidden = false;
        if (info.stage === "complete") {
          text.textContent = info.review > 0
            ? info.review + (info.review === 1 ? " NIK needs review." : " NIKs need review.") + " Open a case to compare the digits with the print."
            : "NIK checks are complete. No NIKs went to the review queue.";
          text.textContent += " Members: " + info.members + " · Flagged: " + info.flagged + " · Cleared automatically: " + info.cleared + ".";
        } else {
          text.textContent = {previous:"NIK cases from an earlier review.",extracting:"NIK checks are waiting for the extraction.",checking:"The card has been saved. The system is checking each member’s NIK.",failed:"Extraction failed. No result is available for review."}[info.stage];
        }
        if (info.review > 0) { link.href = "/nik-review?job=" + encodeURIComponent(id); link.hidden = false; }
        if (info.stage === "extracting" || info.stage === "checking") nikStatusTimer = setTimeout(poll,10000);
      } catch (_) {
        if (state.selectedId !== id || epoch !== nikStatusEpoch) return;
        panel.hidden = false; text.textContent = "NIK check status is unavailable.";
        nikStatusTimer = setTimeout(poll,15000);
      }
    };
    poll();
  }
  function renderDetail(data) {
    if (!data.job) throw new ApiError("Incomplete document details. Reload the page.");
    const job = { ...data.job, result_available: !!data.extraction, member_count: Array.isArray(data.extraction?.members) ? data.extraction.members.length : null }; state.detail = data; state.draft = data.extraction ? clone(data.extraction) : null;
    state.conflict = false; resetDirty();
    visible("welcome", false); visible("detail-loading", false); visible("detail-error", false); visible("detail", true); visible("conflict-panel", false); visible("review-message", false);
    refreshNikRoute(job.id);
    $("detail-case").textContent = job.case_id; $("detail-filename").textContent = job.filename;
    $("detail-status").replaceChildren(needsDispatchCheck(job) ? el("span", "status-badge status-failed", "Check delivery") : badge(job.status)); $("detail-version").textContent = `Version ${job.version}`;
    $("detail-time").textContent = `Uploaded ${shortDate(job.created_at)}`;
    const source = job.mode === "reference" ? "Recorded sample" : job.mode === "demo" ? "Synthetic example" : job.mode === "oci" ? "OCI Generative AI" : modeLabel(job.mode);
    $("mode-notice").textContent = "Reading source · " + source;
    const active = ["QUEUED", "PROCESSING"].includes(activityStatus(job)); const failed = activityStatus(job) === "FAILED";
    visible("job-progress", active); visible("job-failure", failed);
    if (active) $("job-progress").replaceChildren(el("span", "spinner"), document.createTextNode(job.status === "QUEUED" ? "This document is in the queue." : job.mode === "oci" ? "The model is reading the image." : "Preparing the practice result."));
    if (failed) {
      $("job-failure-copy").textContent = processingIssue(job);
      const exhausted = Number(job.attempts) >= Number(job.max_attempts);
      $("retry-job").disabled = exhausted; $("retry-job").textContent = exhausted ? "Attempt limit reached" : "Retry processing";
      visible("retry-job", state.config?.capabilities?.manual_retry !== false);
    }
    visible("extraction-area", !!state.draft); visible("no-extraction", !state.draft);
    if (!state.draft) {
      $("no-extraction").querySelector("h3").textContent = failed ? "Result unavailable" : "Waiting for results";
      $("no-extraction").querySelector("p").textContent = failed ? state.config?.capabilities?.manual_retry === false ? "Ask the operator to resume processing. The original image is still available." : "Check the issue and retry processing. The original image is still available." : "Results appear when processing finishes.";
    } else {
      prepareEvidence();
      $("layout-family").value = state.draft.layout_family || "unknown";
      $("member-count").textContent = String(state.draft.members?.length || 0);
      renderHousehold(); renderMembers(); renderChecks();
      $("panel-flags").querySelector(".checks-note").textContent = "Issues include formatting and image readability.";
      $("review-comment").value = ""; $("review-decision").value = "NEEDS_REVIEW";
      $("save-review").disabled = false; $("save-review").textContent = "Save review";
      setTab(state.tab);
    }
    renderHistory(); updateListJob(job); updateDetailNavigation();
    $("detail").querySelector(".review-workspace").classList.toggle("data-wide", state.dataWide);
    if (state.imageJobId !== job.id) loadImage(job.id);
    document.title = `${job.case_id} · Document Workbench`;
  }
  async function loadDetail(id, options = {}) {
    if (!state.config) { showGlobal("Reconnect before opening a document.", true); return; }
    if (state.view === "overview") state.overviewScroll = window.scrollY;
    state.view = "detail"; document.body.classList.remove("view-overview"); document.body.classList.add("view-detail");
    visible("overview", false); visible("detail-workspace", true);
    if (!state.detailOrder.includes(id)) state.detailOrder = [id];
    const requestId = ++state.detailRequest;
    state.selectedId = id; resetDirty(); renderJobList();
    if (!options.silent) {
      visible("detail", false); visible("detail-error", false); visible("detail-loading", true); window.scrollTo(0, 0);
    }
    try {
      const data = await api(`/api/jobs/${encodeURIComponent(id)}`);
      if (requestId !== state.detailRequest || id !== state.selectedId) return;
      renderDetail(data); setConnection(true);
      if (!options.silent) announce(`Opened ${data.job.filename}. ${statusLabels[data.job.status] || data.job.status}.`);
    } catch (error) {
      if (requestId !== state.detailRequest) return;
      if (!options.silent) {
        visible("detail-loading", false); visible("detail", false); visible("detail-error", true); $("detail-error-copy").textContent = error.message;
      }
      setConnection(false);
    }
  }
  function setTab(name, focus = false) {
    state.tab = name;
    for (const value of ["household", "members", "flags"]) {
      const selected = value === name; const tab = $(`tab-${value}`);
      tab.classList.toggle("active", selected); tab.setAttribute("aria-selected", String(selected)); tab.tabIndex = selected ? 0 : -1;
      visible(`panel-${value}`, selected); if (selected && focus) tab.focus();
    }
  }
  function loadImage(id, reload = false) {
    state.imageJobId = id; state.imageReady = false;
    if (!reload) { state.rotation = 0; state.zoom = 1; state.imageFit = true; $("contrast-control").value = "100"; $("grayscale-control").checked = false; }
    visible("image-loading", true); visible("image-error", false); $("original-image").hidden = true; $("image-dimensions").textContent = "";
    $("original-image").src = `/api/jobs/${encodeURIComponent(id)}/image${reload ? `?reload=${Date.now()}` : ""}`;
  }
  function renderImage() {
    if (!state.imageReady) return;
    const image = $("original-image"); const viewport = $("image-viewport"); const canvas = $("image-canvas");
    const turned = Math.abs(state.rotation % 180) === 90;
    const naturalWidth = turned ? image.naturalHeight : image.naturalWidth;
    const naturalHeight = turned ? image.naturalWidth : image.naturalHeight;
    const roomWidth = Math.max(100, viewport.clientWidth - 32); const roomHeight = Math.max(100, viewport.clientHeight - 32);
    if (state.imageFit) state.zoom = Math.min(roomWidth / naturalWidth, roomHeight / naturalHeight, 1);
    const scaledWidth = naturalWidth * state.zoom; const scaledHeight = naturalHeight * state.zoom;
    // In fit mode keep the canvas within the fractional CSS viewport, even at browser zoom.
    canvas.style.width = state.imageFit ? "100%" : `max(100%, ${scaledWidth + 32}px)`;
    canvas.style.height = state.imageFit ? "100%" : `max(100%, ${scaledHeight + 32}px)`;
    image.style.width = `${image.naturalWidth * state.zoom}px`; image.style.height = `${image.naturalHeight * state.zoom}px`;
    image.style.left = "50%"; image.style.top = "50%";
    image.style.transform = `translate(-50%, -50%) rotate(${state.rotation}deg)`;
    $("zoom-value").value = `${Math.round(state.zoom * 100)}%`;
    renderImageTone();
  }
  function renderImageTone() {
    const image = $("original-image");
    image.style.filter = `contrast(${$("contrast-control").value}%) grayscale(${$("grayscale-control").checked ? 1 : 0})`;
    $("contrast-value").value = `${$("contrast-control").value}%`;
  }
  function changeZoom(factor) { state.imageFit = false; state.zoom = Math.min(4, Math.max(.05, state.zoom * factor)); renderImage(); }
  function safeDownloadName(suffix) { return `kk-review-${String(state.selectedId || "draft").replace(/[^a-z0-9_-]/gi, "").slice(0, 60)}-${suffix}.json`; }
  function downloadJson(data, filename) {
    const blob = new Blob([JSON.stringify(data, null, 2) + "\n"], { type: "application/json" });
    const url = URL.createObjectURL(blob); const anchor = document.createElement("a"); anchor.href = url; anchor.download = filename;
    document.body.append(anchor); anchor.click(); anchor.remove(); window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  async function saveReview() {
    if (state.saving || state.conflict || !state.draft || !state.detail) return;
    for (const input of $("extraction-area").querySelectorAll("input")) { if (!input.reportValidity()) return; }
    state.saving = true; const jobId = state.selectedId; updateDetailNavigation();
    $("save-review").disabled = true; $("save-review").textContent = "Saving…"; visible("review-message", false);
    const controls = $("extraction-area").querySelectorAll("input, select, textarea"); for (const control of controls) control.disabled = true;
    try {
      const data = await api(`/api/jobs/${encodeURIComponent(jobId)}/review`, { method: "POST", body: {
        expected_version: state.detail.job.version, decision: $("review-decision").value, extraction: state.draft, comment: $("review-comment").value
      } });
      if (state.selectedId !== jobId) return;
      state.saving = false; renderDetail(data); showReviewMessage(`Review saved as version ${data.job.version}. ${statusLabels[data.job.status] || data.job.status}.`); announce("Review saved.");
    } catch (error) {
      if (state.selectedId !== jobId) return;
      if (error.status === 409) { state.conflict = true; visible("conflict-panel", true); $("conflict-panel").scrollIntoView({ behavior: "smooth", block: "nearest" }); announce("A newer version is available. Your edits are still on screen."); }
      else showReviewMessage(error.code === "REVIEW_BLOCKED" ? `${error.message} Save as Needs review and check the issues.` : error.message, true);
    } finally {
      state.saving = false; updateDetailNavigation();
      if (state.selectedId === jobId) { for (const control of controls) control.disabled = false; $("save-review").disabled = state.conflict; $("save-review").textContent = "Save review"; }
    }
  }
  async function retryJob() {
    if (!state.detail || state.saving || state.config?.capabilities?.manual_retry === false) return;
    const id = state.selectedId; $("retry-job").disabled = true;
    try {
      const data = await api(`/api/jobs/${encodeURIComponent(id)}/retry`, { method: "POST" });
      if (id === state.selectedId) renderDetail(data); updateListJob(data.job); announce("Request sent.");
    } catch (error) { if (id === state.selectedId) { $("job-failure-copy").textContent = error.message; $("retry-job").disabled = false; } }
  }
  function randomKey() {
    if (globalThis.crypto?.randomUUID) return crypto.randomUUID();
    return `${Date.now()}-${Array.from(crypto.getRandomValues(new Uint8Array(16)), (value) => value.toString(16).padStart(2, "0")).join("")}`;
  }
  function openUpload() {
    if (!state.config) return;
    $("upload-error").hidden = true; renderBatch(); $("upload-dialog").showModal(); $("case-id").focus();
  }
  function closeUpload() { if (!state.uploading) $("upload-dialog").close(); }
  function modeDescription() {
    const mode = $("processing-mode").value;
    const help = {
      demo: "Shows the same synthetic example for each image.",
      reference: "Replays the saved sample for this image without calling AI.",
      oci: "The approved image will be sent to the model for extraction."
    };
    $("mode-description").textContent = help[mode] || "";
    visible("upload-boundary", mode === "oci");
    $("upload-boundary-title").textContent = "Processing location";
    $("upload-boundary-copy").textContent = state.cloudDisclosure || "Approved images are sent to OCI.";
  }
  const batchMaxFiles = 50, batchMaxBytes = 100 * 1024 * 1024;
  function fileSize(file) { return `${(file.size / 1024 / 1024).toFixed(2)} MB`; }
  function batchCase(row) {
    const prefix = $("case-id").value.trim();
    return prefix && (state.batch.length === 1 ? prefix : `${prefix}-${String(row.number).padStart(3, "0")}`);
  }
  function fileProblem(file) {
    if (!file.size) return "The file is empty.";
    if (file.size > state.config.max_upload_bytes) return `File size exceeds ${Math.floor(state.config.max_upload_bytes / 1048576)} MB.`;
    if ((file.type && !state.config.accepted_mime_types.includes(file.type)) || (!file.type && !/\.(jpe?g|png)$/i.test(file.name))) return "Choose a JPG or PNG image.";
    if (file.name.length > 255) return "The filename exceeds 255 characters.";
    return "";
  }
  function renderBatch() {
    const list = $("batch-list");
    const previousRows = new Map(Array.from(list.children, node => [node.dataset.key, node]));
    const total = state.batch.reduce((sum, row) => sum + row.file.size, 0);
    $("batch-count").textContent = state.batch.length ? `${state.batch.length} files · ${(total / 1048576).toFixed(2)} MB` : "No files selected";
    const done = state.batch.filter(row => row.jobId).length;
    $("upload-progress-bar").max = Math.max(1, state.batch.length);
    $("upload-progress-bar").value = done;
    visible("upload-progress-bar", state.batchStarted && state.batch.length > 0);
    const uncertain = state.batch.filter(row => row.status === "uncertain").length;
    const rejected = state.batch.filter(row => row.status === "rejected").length;
    $("batch-progress").textContent = state.batchStarted ? `${done} files received${uncertain ? ` · ${uncertain} delivery unconfirmed` : ""}${rejected ? ` · ${rejected} failed to send` : ""}.` : "";
    visible("view-uploaded-documents", done > 0); $("view-uploaded-documents").disabled = state.uploading;
    $("clear-batch").hidden = !state.batch.length || state.uploading;
    $("check-image").disabled = state.uploading || state.imageChecking;
    $("case-id").disabled = state.uploading || state.batchStarted;
    $("processing-mode").disabled = state.uploading || state.batchStarted;
    $("upload-file").disabled = state.uploading || state.batchStarted;
    $("file-drop").classList.toggle("drop-disabled", state.uploading || state.batchStarted);
    $("submit-upload").disabled = state.uploading || !state.batch.some(row => row.status === "ready");
    $("submit-upload").textContent = state.uploading ? "Uploading…" : "Upload files";
    for (const row of state.batch) {
      let li = previousRows.get(row.key);
      if (!li) {
        li = el("li", "batch-item"); li.dataset.key = row.key;
        const thumb = el("img", "batch-thumb"); thumb.src = row.url; thumb.alt = "";
        li.append(thumb, el("div", "batch-info"), el("div", "batch-actions"));
        list.append(li);
      }
      previousRows.delete(row.key);
      const info = li.children[1]; info.replaceChildren(); info.append(el("strong", "batch-name", row.file.name), el("span", "batch-meta", `${fileSize(row.file)} · ${row.caseId || batchCase(row) || "Enter a batch reference"}`));
      let label = row.message || ({ready:"Ready to upload", uploading:"Uploading…", submitted:"File received", uncertain:"Delivery is unconfirmed. Check its status before resubmitting.", rejected:"Not sent"}[row.status] || row.status);
      if (row.jobId) { const job = state.jobs.find(item => item.id === row.jobId); label = `Received · ${job ? (hasResult(job) ? "Result saved" : (needsDispatchCheck(job) ? "Check delivery" : statusLabels[job.status] || job.status)) : "Awaiting status"}`; }
      info.append(el("span", `batch-status ${["invalid", "rejected", "uncertain"].includes(row.status) ? "batch-warning" : ""}`, label));
      const actions = li.children[2]; actions.replaceChildren();
      if (!state.uploading && row.status === "uncertain") { const check = el("button", "text-button", "Check status"); check.type = "button"; check.addEventListener("click", () => checkBatchStatus(row)); actions.append(check); }
      if (!state.uploading && row.jobId) { const view = el("button", "text-button", "Open document"); view.type = "button"; view.addEventListener("click", () => { closeUpload(); requestNavigation(() => openDocument(row.jobId)); }); actions.append(view); }
      if (!state.uploading && !state.batchStarted) { const remove = el("button", "icon-button batch-remove", "×"); remove.type = "button"; remove.setAttribute("aria-label", `Remove ${row.file.name}`); remove.addEventListener("click", () => { URL.revokeObjectURL(row.url); state.batch = state.batch.filter(item => item !== row); renderBatch(); fileChanged(); }); actions.append(remove); }
    }
    for (const stale of previousRows.values()) stale.remove();
  }
  function addFiles(files) {
    if (state.uploading || state.batchStarted || !state.config) return;
    let rejected = 0, sizeRejected = 0;
    for (const file of files) {
      if (state.batch.length >= batchMaxFiles) { rejected++; continue; }
      if (state.batch.reduce((sum, row) => sum + row.file.size, 0) + file.size > batchMaxBytes) { sizeRejected++; continue; }
      const problem = fileProblem(file);
      state.batch.push({ file, number: ++state.batchSequence, key: randomKey(), url: URL.createObjectURL(file), status: problem ? "invalid" : "ready", message: problem, jobId: null, caseId: null });
    }
    $("upload-error").textContent = [rejected ? `${rejected} files exceed the 50-file limit.` : "", sizeRejected ? `${sizeRejected} files exceed the 100 MB batch limit.` : ""].filter(Boolean).join(" ");
    visible("upload-error", !!(rejected || sizeRejected));
    $("upload-file").value = ""; renderBatch(); fileChanged();
  }
  function fileChanged() {
    const row = state.batch.length === 1 ? state.batch[0] : null;
    const enabled = state.config?.capabilities?.upload_image_check === true;
    visible("preparation-preview", enabled && !!row && row.status !== "invalid");
    const image = $("selected-original");
    const url = enabled && row && row.status !== "invalid" ? row.url : null;
    if (image.getAttribute("src") === url) return;
    if (url) image.src = url; else image.removeAttribute("src");
    $("selected-dimensions").textContent = "";
    $("preparation-status").textContent = "Check whether all edges of the card are visible.";
  }
  async function checkImage() {
    const row = state.batch.length === 1 ? state.batch[0] : null; const file = row?.file;
    if (!file || state.uploading || state.imageChecking || !state.config?.capabilities?.upload_image_check || row.status === "invalid") return;
    state.imageChecking = true; $("check-image").disabled = true; $("preparation-status").textContent = "Checking image…";
    try {
      const content = await readFileBase64(file);
      const result = await api("/api/image-check", {method: "POST", body: {content_base64: content}});
      if (state.batch.length !== 1 || state.batch[0] !== row) return;
      const boundary = result.document_boundary_available ? "Document edges detected." : "Document edges could not be detected.";
      $("preparation-status").textContent = [boundary, ...result.review_reasons, "Check the edges of the original image."].join(" ");
    } catch (error) { if (state.batch.length === 1 && state.batch[0] === row) $("preparation-status").textContent = error.message; }
    finally { state.imageChecking = false; $("check-image").disabled = state.uploading; }
  }
  async function checkBatchStatus(row) {
    row.message = "Checking the document list…"; renderBatch();
    try {
      if (state.config.runtime === "private_cloud") {
        const result = await api("/api/submission-status", {method: "POST", body: {case_id: row.caseId, idempotency_key: row.key}});
        if (!state.batch.includes(row)) return;
        if (result.job_id) {
          row.jobId = result.job_id; row.status = "submitted"; row.message = "";
          if (!state.latestBatchIds.includes(row.jobId)) state.latestBatchIds.push(row.jobId);
          await loadJobs(true);
        } else {
          row.message = "Upload has not been recorded yet. Check again after submission completes.";
        }
        renderBatch();
        return;
      }
      const data = await api(`/api/jobs?query=${encodeURIComponent(row.caseId)}`);
      if (!Array.isArray(data.jobs)) throw new Error("Could not read the document list.");
      const matches = data.jobs.filter(job => job.case_id === row.caseId && job.filename === row.file.name && job.mode === row.mode);
      row.message = matches.length ? `${matches.length} files have this reference and filename. Check the list before uploading again.` : "No matching file yet. Check the status before resubmitting.";
      await loadJobs(true);
    } catch { row.message = "Upload status is unavailable."; }
    renderBatch();
  }
  function readFileBase64(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result).split(",", 2)[1]);
      reader.onerror = () => reject(new ApiError("Could not read the file. Choose the JPG or PNG again."));
      reader.readAsDataURL(file);
    });
  }
  async function upload(event) {
    event.preventDefault(); if (state.uploading || !state.config) return;
    const mode = $("processing-mode").value, caseId = $("case-id").value.trim();
    const showError = message => { $("upload-error").textContent = message; visible("upload-error", true); };
    visible("upload-error", false);
    if (!state.batch.some(row => row.status === "ready")) { showError("Choose at least one JPG or PNG image."); return; }
    if (!state.config.modes.some(item => item.id === mode && item.available)) { showError("Select an available reading mode."); return; }
    if (!caseId || state.batch.some(row => batchCase(row).length > 128)) { showError("Shorten the reference to leave room for the file sequence (128 characters total)."); return; }
    if (state.config.runtime === "private_cloud" && !/^[A-Za-z0-9][A-Za-z0-9_.:-]*$/.test(caseId)) { showError("Start the reference with a letter or digit. Use letters, digits, underscores, periods, colons or hyphens."); return; }
    if (!state.batchStarted) { state.latestBatchIds = []; state.latestBatchCase = caseId; }
    state.batchStarted = true; state.uploading = true;
    for (const row of state.batch) { row.caseId = batchCase(row); row.mode = mode; }
    $("close-upload").disabled = true; $("cancel-upload").disabled = true; renderBatch();
    for (const row of state.batch.filter(item => item.status === "ready")) {
      row.status = "uploading"; row.message = ""; renderBatch();
      let content;
      try { content = await readFileBase64(row.file); }
      catch (error) { row.status = "rejected"; row.message = error.message; renderBatch(); continue; }
      try {
        const data = await api("/api/jobs", { method: "POST", body: {filename: row.file.name, content_base64: content, case_id: row.caseId, idempotency_key: row.key, mode} });
        if (!data.job?.id) throw new ApiError("The service has not returned a job ID.");
        row.jobId = data.job.id; row.status = "submitted"; row.message = "";
        if (!state.latestBatchIds.includes(row.jobId)) state.latestBatchIds.push(row.jobId); updateListJob(data.job);
      } catch (error) {
        const definitive = error.status >= 400 && error.status < 500 && error.status !== 408 && error.status !== 409 && error.status !== 429;
        row.status = definitive ? "rejected" : "uncertain";
        row.message = definitive ? `Not sent · ${error.message}` : "Delivery is unconfirmed. Check its status before resubmitting.";
      }
      content = null; renderBatch();
    }
    state.uploading = false; $("close-upload").disabled = false; $("cancel-upload").disabled = false;
    renderBatch(); await loadJobs(true); announce($("batch-progress").textContent + " Open the document list to see processing status.");
  }
  function configure(config) {
    const required = ["household_fields", "member_fields", "modes", "accepted_mime_types"];
    if (required.some((key) => !Array.isArray(config[key])) || !config.csrf_token || !(Number(config.max_upload_bytes) > 0)) throw new ApiError("Service configuration is incomplete. Contact the application operator.");
    const cloud = config.runtime === "private_cloud" && config.local_only === false && config.private_access === "ssh_tunnel";
    if (config.local_only !== true && !cloud) throw new ApiError("This service environment is unsupported.");
    if (cloud && (config.data_region !== "ap-batam-1" || !Array.isArray(config.inference_regions) || !config.inference_regions.length || !config.inference_regions.every((region) => typeof region === "string" && /^[a-z0-9-]+$/.test(region)) || config.live_oci_enabled !== true || config.modes.length !== 1 || config.modes[0].id !== "oci" || config.capabilities?.manual_retry !== false)) throw new ApiError("Cloud configuration is incomplete. Contact the application operator.");
    state.config = config;
    $("nik-review-nav").hidden = config.runtime !== "private_cloud";
    state.cloudDisclosure = "";
    state.currentModelLabel = typeof config.model_display_name === "string" && config.model_display_name.trim() ? config.model_display_name.trim().slice(0, 120) : "OCI Generative AI";
    const select = $("processing-mode"); select.replaceChildren(el("option", "", "Choose a reading mode")); select.firstElementChild.value = "";
    for (const mode of config.modes) {
      const label = cloud && mode.id === "oci" ? `${state.currentModelLabel} (OCI GenAI)` : modeLabel(mode.id);
      const option = el("option", "", `${label}${mode.available ? "" : " · unavailable"}`); option.value = mode.id; option.disabled = !mode.available; select.append(option);
    }
    const available = config.modes.some((item) => item.available);
    const live = config.live_oci_enabled === true;
    $("privacy-copy").textContent = live ? "Data is stored on this computer. OCI mode sends approved images to the AI service." : "Images, practice results and review decisions stay on this computer.";
    $("welcome-note").textContent = `${live ? "OCI mode accepts approved images only." : "Local practice uses sample data without AI calls."}`;
    $("runtime-label").textContent = cloud ? "OCI Batam" : "Local practice";
    if (cloud) {
      state.cloudDisclosure = "Images and results are stored in OCI Batam. AI extraction takes place outside Indonesia.";
      $("privacy-copy").textContent = state.cloudDisclosure;
      $("welcome-note").textContent = `Decisions are recorded under ${config.reviewer_identity || "registered operator"}`;
    }
    visible("retry-job", config.capabilities?.manual_retry !== false);
    modeDescription();
    $("new-upload").disabled = !available; $("welcome-upload").disabled = !available; fileChanged(); renderBatch();
  }
  function renderProcessingStatus(status) {
    state.processingStatus = status;
    for (const id of ["processing-readiness", "upload-processing-readiness"]) {
      const node = $(id); node.replaceChildren(); node.hidden = status === "AVAILABLE";
      node.classList.toggle("readiness-unknown", status === "UNKNOWN");
      if (status === "PAUSED") node.append(el("strong", "", state.config?.live_oci_enabled ? "AI extraction is paused. " : "Processing is paused. "), document.createTextNode("Uploaded files remain in the queue."));
      else if (status === "UNKNOWN") node.append(el("strong", "", "Service status is unavailable. "), document.createTextNode("You can still open saved files. New uploads may need to wait."));
    }
  }
  async function loadProcessingStatus(force = false) {
    const config = state.config;
    if (!config || (config.runtime !== "private_cloud" && config.capabilities?.processing_status !== true)) return;
    if (state.processingStatusLoading || (!force && Date.now() - state.lastProcessingCheck < 15000)) return;
    state.processingStatusLoading = true;
    try {
      const data = await api("/api/processing-status");
      if (state.config !== config) return;
      const valid = (data.status === "AVAILABLE" && data.processing_available === true) || (data.status === "PAUSED" && data.processing_available === false) || (data.status === "UNKNOWN" && data.processing_available === null);
      renderProcessingStatus(valid ? data.status : "UNKNOWN");
    } catch { if (state.config === config) renderProcessingStatus("UNKNOWN"); }
    finally { state.processingStatusLoading = false; state.lastProcessingCheck = Date.now(); }
  }
  async function initialize() {
    visible("global-message", false);
    try {
      const config = await api("/api/config"); configure(config); setConnection(true);
      if (config.runtime === "private_cloud" || config.capabilities?.processing_status === true) { renderProcessingStatus("UNKNOWN"); loadProcessingStatus(true); }
      else { visible("processing-readiness", false); visible("upload-processing-readiness", false); }
    } catch (error) { state.config = null; $("new-upload").disabled = true; $("welcome-upload").disabled = true; setConnection(false); showGlobal(error.message, true); }
    await loadJobs();
  }
  async function poll() {
    if (document.hidden || state.saving || state.uploading) return;
    await loadJobs(true); loadProcessingStatus();
    if (state.batch.length && !$("batch-list").contains(document.activeElement)) renderBatch();
    if (state.view === "detail" && state.detail && ["QUEUED", "PROCESSING"].includes(state.detail.job.status) && !state.dirty) await loadDetail(state.selectedId, { silent: true });
  }

  $("new-upload").addEventListener("click", () => requestNavigation(() => { if (state.detail) renderDetail(state.detail); openUpload(); }));
  $("welcome-upload").addEventListener("click", openUpload);
  $("close-upload").addEventListener("click", closeUpload); $("cancel-upload").addEventListener("click", closeUpload);
  $("upload-dialog").addEventListener("cancel", (event) => { if (state.uploading) event.preventDefault(); });
  $("selected-original").addEventListener("load", () => { $("selected-dimensions").textContent = `${$("selected-original").naturalWidth} × ${$("selected-original").naturalHeight} pixels`; });
  $("check-image").addEventListener("click", checkImage);
  $("upload-form").addEventListener("submit", upload); $("upload-file").addEventListener("change", event => addFiles(event.target.files));
  $("case-id").addEventListener("input", renderBatch);
  for (const type of ["dragenter", "dragover"]) $("file-drop").addEventListener(type, event => { event.preventDefault(); if (!state.batchStarted) $("file-drop").classList.add("drag-active"); });
  for (const type of ["dragleave", "drop"]) $("file-drop").addEventListener(type, event => { event.preventDefault(); $("file-drop").classList.remove("drag-active"); if (type === "drop") addFiles(event.dataTransfer.files); });
  $("clear-batch").addEventListener("click", () => {
    if (state.batch.some(row => ["ready", "uncertain"].includes(row.status)) && !window.confirm("Clear this list? Unsubmitted files and uncertain submission details will be lost.")) return;
    for (const row of state.batch) URL.revokeObjectURL(row.url);
    state.batch = []; state.batchStarted = false; state.batchSequence = 0; visible("upload-error", false); renderBatch(); fileChanged();
  }); $("processing-mode").addEventListener("change", modeDescription);
  $("keep-editing").addEventListener("click", () => { state.pendingNavigation = null; $("discard-dialog").close(); });
  $("discard-changes").addEventListener("click", () => { const action = state.pendingNavigation; state.pendingNavigation = null; $("discard-dialog").close(); resetDirty(); if (action) action(); });
  $("discard-dialog").addEventListener("cancel", () => { state.pendingNavigation = null; });
  $("refresh-jobs").addEventListener("click", () => loadJobs()); $("queue-retry").addEventListener("click", () => loadJobs());
  $("toggle-density").addEventListener("click", () => {
    const compact = document.body.classList.toggle("compact-rows");
    $("toggle-density").setAttribute("aria-pressed", String(compact));
    $("toggle-density").textContent = compact ? "Expand row spacing" : "Compact rows";
  });
  for (const button of document.querySelectorAll("[data-activity-filter]")) button.addEventListener("click", () => {
    $("status-filter").value = button.dataset.activityFilter;
    $("job-search").value = ""; state.page = 1; renderJobList();
    $("status-filter").focus({preventScroll: true});
  });
  $("document-jump").addEventListener("change", () => {
    const id = $("document-jump").value;
    $("document-jump").value = state.selectedId || "";
    if (id && id !== state.selectedId) requestNavigation(() => loadDetail(id));
  });
  $("job-search").addEventListener("input", () => { state.page = 1; renderJobList(); });
  for (const id of ["status-filter", "list-scope", "job-sort", "page-size"]) $(id).addEventListener("change", () => { state.page = 1; renderJobList(); });
  $("previous-page").addEventListener("click", () => { state.page -= 1; renderJobList(); });
  $("next-page").addEventListener("click", () => { state.page += 1; renderJobList(); });
  document.addEventListener("click", event => {
    const options = $("list-options");
    if (options.open && !options.contains(event.target)) options.open = false;
  });
  $("list-options").addEventListener("keydown", event => {
    if (event.key === "Escape") {
      $("list-options").open = false;
      $("list-options").querySelector("summary").focus();
    }
  });
  $("clear-filters").addEventListener("click", resetOverviewFilters);
  for (const button of document.querySelectorAll("[data-stat-filter]")) button.addEventListener("click", () => { $("job-search").value = ""; $("list-scope").value = "all"; $("status-filter").value = button.dataset.statFilter; state.page = 1; renderJobList(); });
  $("back-to-overview").addEventListener("click", () => requestNavigation(showOverview));
  $("previous-document").addEventListener("click", () => moveDocument(-1));
  $("next-document").addEventListener("click", () => moveDocument(1));
  $("view-uploaded-documents").addEventListener("click", () => { closeUpload(); requestNavigation(() => { $("list-scope").value = "batch"; $("status-filter").value = ""; $("job-search").value = ""; state.page = 1; state.overviewScroll = 0; showOverview(); }); });
  $("toggle-data-width").addEventListener("click", () => { state.dataWide = !state.dataWide; $("detail").querySelector(".review-workspace").classList.toggle("data-wide", state.dataWide); $("toggle-data-width").setAttribute("aria-pressed", String(state.dataWide)); $("toggle-data-width").textContent = state.dataWide ? "Show original image" : "Expand table"; requestAnimationFrame(() => { if (!state.dataWide) renderImage(); }); });
  $("detail-retry").addEventListener("click", () => { if (state.selectedId && state.config) loadDetail(state.selectedId); else initialize(); });
  $("reload-detail").addEventListener("click", () => requestNavigation(() => loadDetail(state.selectedId)));
  $("load-latest").addEventListener("click", () => requestNavigation(() => loadDetail(state.selectedId)));
  $("download-draft").addEventListener("click", () => downloadJson({ draft: true, note: "Unsaved local edits. This is not an accepted record.", job_id: state.selectedId, based_on_version: state.detail.job.version, extraction: state.draft, decision: $("review-decision").value, comment: $("review-comment").value }, safeDownloadName("unsaved-draft")));
  $("export-job").addEventListener("click", async () => {
    const jobId = state.selectedId; const filename = safeDownloadName("saved-record");
    $("export-job").disabled = true;
    try { const data = await api(`/api/jobs/${encodeURIComponent(jobId)}/export`); downloadJson(data, filename); announce("Saved data downloaded."); }
    catch (error) { showGlobal(error.message); }
    finally { $("export-job").disabled = false; }
  });
  $("retry-job").addEventListener("click", retryJob); $("save-review").addEventListener("click", saveReview);
  $("review-comment").addEventListener("input", markDirty); $("review-decision").addEventListener("change", markDirty);
  $("member-group").addEventListener("change", renderMembers);
  $("layout-family").addEventListener("change", () => { if (state.draft) { state.draft.layout_family = $("layout-family").value; markDirty(); } });
  for (const name of ["household", "members", "flags"]) {
    $(`tab-${name}`).addEventListener("click", () => setTab(name));
    $(`tab-${name}`).addEventListener("keydown", (event) => {
      const names = ["household", "members", "flags"]; let index = names.indexOf(name);
      if (event.key === "ArrowRight") index = (index + 1) % names.length;
      else if (event.key === "ArrowLeft") index = (index + names.length - 1) % names.length;
      else if (event.key === "Home") index = 0; else if (event.key === "End") index = names.length - 1; else return;
      event.preventDefault(); setTab(names[index], true);
    });
  }
  $("original-image").addEventListener("load", () => {
    state.imageReady = true; $("original-image").hidden = false; visible("image-loading", false); visible("image-error", false);
    $("image-dimensions").textContent = `${$("original-image").naturalWidth} × ${$("original-image").naturalHeight}`; renderImage();
  });
  $("original-image").addEventListener("error", () => { state.imageReady = false; visible("image-loading", false); visible("image-error", true); });
  $("retry-image").addEventListener("click", () => loadImage(state.selectedId, true));
  $("rotate-left").addEventListener("click", () => { state.rotation = (state.rotation - 90) % 360; renderImage(); });
  $("rotate-right").addEventListener("click", () => { state.rotation = (state.rotation + 90) % 360; renderImage(); });
  $("zoom-in").addEventListener("click", () => changeZoom(1.25)); $("zoom-out").addEventListener("click", () => changeZoom(.8));
  $("fit-image").addEventListener("click", () => { state.imageFit = true; renderImage(); });
  $("reset-image").addEventListener("click", () => { state.rotation = 0; state.imageFit = true; $("contrast-control").value = "100"; $("grayscale-control").checked = false; renderImage(); });
  $("contrast-control").addEventListener("input", renderImageTone); $("grayscale-control").addEventListener("change", renderImageTone);
  if (globalThis.ResizeObserver) new ResizeObserver(() => { if (state.imageFit) renderImage(); }).observe($("image-viewport"));
  else window.addEventListener("resize", renderImage);
  window.addEventListener("beforeunload", (event) => { if (state.dirty || state.uploading || state.saving || state.batch.some(row => ["ready", "uncertain"].includes(row.status))) { event.preventDefault(); event.returnValue = ""; } });
  document.addEventListener("visibilitychange", () => { if (!document.hidden) poll(); });
  initialize(); window.setInterval(poll, 4000);
})();
