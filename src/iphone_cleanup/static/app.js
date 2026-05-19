/* global fetch, EventSource, document, window */
const STATUS_POLL_MS =
  typeof window.__APP_POLL_MS__ === "number" && window.__APP_POLL_MS__ > 0
    ? window.__APP_POLL_MS__
    : 5000;
const STATUS_POLL_BUSY_MS = Math.max(1000, Math.min(STATUS_POLL_MS, 2000));
let lastPhase = "";
let lastScanKind = "";
let reviewThumbEdge = 1024;
let groupsPageSize = 30;
let previewPageSize = 24;
let imagesPerGroupPage = 12;
let lastLoadedGroupCounts = { exact: -1, fuzzy: -1 };
let autoReviewEnabled = true;

const REVIEW_SIZE_FILTERS = [
  { id: "all", label: "All sizes", short: "All", deleteScope: "all duplicate sets" },
  { id: "2", label: "Exactly 2 photos", short: "2 photos", deleteScope: "2-photo sets only" },
  { id: "3", label: "Exactly 3 photos", short: "3 photos", deleteScope: "3-photo sets only" },
  { id: "4", label: "Exactly 4 photos", short: "4 photos", deleteScope: "4-photo sets only" },
  { id: "5plus", label: "5 or more photos", short: "5+ photos", deleteScope: "5+ photo sets only" },
];

const reviewPagerState = {
  exact: { page: 0, total: 0, loading: false, dialogOpen: false, sizeFilter: "all", sizeCounts: null },
  fuzzy: { page: 0, total: 0, loading: false, dialogOpen: false, sizeFilter: "all", sizeCounts: null },
};

const docPreviewPager = { page: 0, total: 0, scope: "older_than_90d", vf: false };
const deletePreviewPager = { exact: { page: 0, total: 0 }, fuzzy: { page: 0, total: 0 } };

function formatLibraryCount(n) {
  const v = Number(n);
  if (!Number.isFinite(v) || v < 0) return "—";
  if (v >= 1000000) return `${(v / 1000000).toFixed(1).replace(/\.0$/, "")}M`;
  if (v >= 10000) return `${Math.round(v / 1000)}k`;
  if (v >= 1000) return `${(v / 1000).toFixed(1).replace(/\.0$/, "")}k`;
  return String(Math.round(v));
}

function libraryIndexLabel(s) {
  const n = s.library_indexed_count;
  if (n == null || n <= 0) return "";
  return ` (${formatLibraryCount(n)} photos indexed on this mount)`;
}
let mountPath = "";
let thumbnailMountRoot = "";
let lastDocumentBatches = [];
let docPreviewDebounceTimer = null;
let lastDocPreviewKey = "";
let statusPollTimer = null;
let statusPollInFlight = false;
let statusPollFailStreak = 0;
let uiBootstrapDone = false;
const STATUS_FETCH_TIMEOUT_MS = 12000;
let lastSnapshot = null;
let activeExactScanSessionId = "";
let activeFuzzyScanSessionId = "";
let scanSessionsLoadingExact = false;
let scanSessionsLoadingFuzzy = false;

const SCAN_WORKFLOWS = {
  exact: {
    kind: "exact",
    sessionListId: "exactScanSessionList",
    emptyId: "exactScanWorkspaceEmpty",
    loadingId: "exactScanWorkspaceLoading",
    teaserId: "exactDupReviewTeaser",
    hintId: "exactScanRunHint",
    groupsId: "exactGroups",
    dlgId: "dlgExactDupReview",
    btnOpenId: "btnExactDupReviewOpen",
    btnCloseId: "btnExactDupReviewClose",
    btnCloseFooterId: "btnExactDupReviewCloseFooter",
    btnDeleteId: "btnDeleteExact",
    btnScanId: "btnScan",
    btnCancelId: "btnScanCancelExact",
    dlgDeleteId: "dlgDeleteExact",
    deleteSummaryId: "deleteExactPreviewSummary",
    deleteStripId: "deleteExactPreviewStrip",
    confirmInputId: "confirmInputExact",
    btnConfirmDeleteId: "btnConfirmDeleteExact",
    btnCancelDeleteId: "btnCancelDeleteExact",
    panelId: "exactDupPanel",
    activeSnapshotKey: "active_exact_scan_session_id",
    groupCountSnapshotKey: "exact_group_count",
    pagerPrevId: "btnExactReviewPrev",
    pagerNextId: "btnExactReviewNext",
    pagerLabelId: "exactReviewPageLabel",
    titleId: "exactDupPopoutHeading",
    sizeSliderId: "exactReviewSizeSlider",
    sizeValueId: "exactReviewSizeValue",
    sizeHintId: "exactReviewSizeHint",
  },
  fuzzy: {
    kind: "fuzzy",
    sessionListId: "fuzzyScanSessionList",
    emptyId: "fuzzyScanWorkspaceEmpty",
    loadingId: "fuzzyScanWorkspaceLoading",
    teaserId: "fuzzyDupReviewTeaser",
    hintId: "fuzzyScanRunHint",
    groupsId: "fuzzyGroups",
    dlgId: "dlgFuzzyDupReview",
    btnOpenId: "btnFuzzyDupReviewOpen",
    btnCloseId: "btnFuzzyDupReviewClose",
    btnCloseFooterId: "btnFuzzyDupReviewCloseFooter",
    btnDeleteId: "btnDeleteFuzzy",
    btnScanId: "btnScanFuzzy",
    btnCancelId: "btnScanCancelFuzzy",
    dlgDeleteId: "dlgDeleteFuzzy",
    deleteSummaryId: "deleteFuzzyPreviewSummary",
    deleteStripId: "deleteFuzzyPreviewStrip",
    confirmInputId: "confirmInputFuzzy",
    btnConfirmDeleteId: "btnConfirmDeleteFuzzy",
    btnCancelDeleteId: "btnCancelDeleteFuzzy",
    panelId: "fuzzyRollPanel",
    activeSnapshotKey: "active_fuzzy_scan_session_id",
    groupCountSnapshotKey: "fuzzy_group_count",
    pagerPrevId: "btnFuzzyReviewPrev",
    pagerNextId: "btnFuzzyReviewNext",
    pagerLabelId: "fuzzyReviewPageLabel",
    titleId: "fuzzyDupPopoutHeading",
    sizeSliderId: "fuzzyReviewSizeSlider",
    sizeValueId: "fuzzyReviewSizeValue",
    sizeHintId: "fuzzyReviewSizeHint",
  },
};

function reviewPagerKey(kind) {
  return kind === "fuzzy" ? "fuzzy" : "exact";
}

function sizeFilterFromSliderIndex(idx) {
  const i = Number(idx);
  return REVIEW_SIZE_FILTERS[Number.isFinite(i) ? Math.max(0, Math.min(4, i)) : 0]?.id || "all";
}

function sliderIndexFromSizeFilter(filterId) {
  const i = REVIEW_SIZE_FILTERS.findIndex((x) => x.id === filterId);
  return i >= 0 ? i : 0;
}

function sizeFilterMeta(filterId) {
  return REVIEW_SIZE_FILTERS.find((x) => x.id === filterId) || REVIEW_SIZE_FILTERS[0];
}

function sizeFilterQueryParam(sizeFilter) {
  return `size_filter=${encodeURIComponent(sizeFilter || "all")}`;
}

function reviewTotalPages(total) {
  if (!total || total <= 0) return 0;
  return Math.ceil(total / groupsPageSize);
}

function clampReviewPage(page, total) {
  const tp = reviewTotalPages(total);
  if (tp <= 0) return 0;
  return Math.max(0, Math.min(page, tp - 1));
}

function workflowCfg(kind) {
  return SCAN_WORKFLOWS[kind === "fuzzy" ? "fuzzy" : "exact"];
}

function activeSessionIdFor(kind) {
  return kind === "fuzzy" ? activeFuzzyScanSessionId : activeExactScanSessionId;
}

function setActiveSessionIdFor(kind, id) {
  if (kind === "fuzzy") activeFuzzyScanSessionId = id || "";
  else activeExactScanSessionId = id || "";
}

function formatHumanBytes(bytes) {
  const n = Number(bytes);
  if (!Number.isFinite(n) || n < 0) return "—";
  if (n < 1024) return `${n} B`;
  let v = n / 1024;
  const units = ["KB", "MB", "GB", "TB"];
  let ui = 0;
  while (v >= 1024 && ui < units.length - 1) {
    v /= 1024;
    ui += 1;
  }
  const label = units[ui];
  return `${v < 10 ? v.toFixed(1) : Math.round(v)} ${label}`;
}

function scrollToLiveProgress() {
  const hub = document.getElementById("activityHub");
  if (hub) hub.scrollIntoView({ behavior: "smooth", block: "start" });
}

function fmtEpochSeconds(ts) {
  if (typeof ts !== "number" || !Number.isFinite(ts)) return "—";
  try {
    return new Date(ts * 1000).toISOString();
  } catch {
    return String(ts);
  }
}

function isSessionBusy(s) {
  const phase = s.phase || "";
  if (phase === "mounting" || phase === "scanning" || phase === "deleting" || phase === "unmounting") {
    return true;
  }
  return (s.jobs || []).some((j) => j.running);
}

function mergeSnapshotPatch(patch) {
  const base = lastSnapshot ? { ...lastSnapshot } : {};
  const next = { ...base, ...patch };
  if (patch.jobs && base.jobs) {
    next.jobs = patch.jobs;
  }
  applySnapshot(next);
  return next;
}

function optimisticBusyPhase(phase, scanKind) {
  const patch = { phase };
  if (scanKind) patch.scan_running_kind = scanKind;
  if (phase === "scanning") {
    patch.jobs = [
      {
        running: true,
        kind: "scan",
        label:
          scanKind === "fuzzy"
            ? "Fuzzy roll batch (bursts & same-scene sets)…"
            : "Scanning library for duplicates…",
        message: "Starting…",
      },
    ];
  } else if (phase === "mounting") {
    patch.jobs = [{ running: true, kind: "mount", label: "Mounting iPhone media…", message: "Starting…" }];
  } else if (phase === "deleting") {
    patch.jobs = [{ running: true, kind: "delete", label: "Deleting from phone…", message: "Starting…" }];
  } else if (phase === "unmounting") {
    patch.jobs = [{ running: true, kind: "unmount", label: "Unmounting…", message: "Starting…" }];
  }
  mergeSnapshotPatch(patch);
}

function phaseHeadline(s) {
  const phase = s.phase || "idle";
  const label = s.phase_label || phase;
  const rk = s.scan_running_kind || lastScanKind || "";
  if (phase === "scanning" && rk === "fuzzy") {
    return `${label} (fuzzy roll batch)`;
  }
  if (phase === "scanning" && rk === "exact") {
    return `${label} (exact duplicate scan)`;
  }
  return label;
}

function buildLiveStatusSummary(s) {
  const phaseLine = phaseHeadline(s);
  const running = (s.jobs || []).filter((j) => j.running);
  if (running.length) {
    const j = running[0];
    const label = j.label || j.kind || "Working";
    const msg = j.message || "…";
    return `${phaseLine} — ${label}: ${msg}`;
  }
  if (!s.run_session_active && s.run_session_active !== undefined) {
    return "Session ended — restart with scripts/run.sh";
  }
  return phaseLine;
}

/** Lines shown in the main activity log (phase + jobs + server log). */
function buildActivityLogLines(s) {
  const out = [];
  const phase = s.phase || "idle";
  out.push(`════════ CURRENT PHASE: ${String(phase).toUpperCase()} ════════`);
  out.push(phaseHeadline(s));
  const running = (s.jobs || []).filter((j) => j.running);
  for (const j of running) {
    const label = j.label || j.kind || "job";
    const msg = j.message || "…";
    let line = `▶ ${label}: ${msg}`;
    const tc = j.progress_total;
    const cur = j.progress_current;
    if (typeof tc === "number" && tc > 0 && typeof cur === "number") {
      const pct = Math.min(100, Math.max(0, (100 * cur) / tc));
      line += ` (${cur}/${tc}, ${pct.toFixed(1)}%)`;
    }
    out.push(line);
  }
  if (s.last_error) {
    out.push(`⚠ ${s.last_error}`);
  }
  out.push("──────── session log (newest at bottom) ────────");
  const log = s.activity_log || [];
  if (log.length) {
    out.push(...log);
  } else {
    out.push(
      "(no log lines yet — connect USB, mount, scan, delete, and document steps append here with timestamps)",
    );
  }
  return out;
}

function statusPollIntervalMs(s) {
  if (s && isSessionBusy(s)) return STATUS_POLL_BUSY_MS;
  return STATUS_POLL_MS;
}

function markRefreshed(s) {
  const label = document.getElementById("lastRefreshLabel");
  if (label) {
    const t = new Date();
    const hh = String(t.getHours()).padStart(2, "0");
    const mm = String(t.getMinutes()).padStart(2, "0");
    const ss = String(t.getSeconds()).padStart(2, "0");
    const everySec = Math.round(statusPollIntervalMs(s) / 1000);
    label.textContent = `Refreshed ${hh}:${mm}:${ss} · every ${everySec}s`;
  }
  const spinner = document.getElementById("refreshSpinner");
  if (spinner) spinner.classList.add("pulse");
  window.setTimeout(() => {
    spinner?.classList.remove("pulse");
  }, 450);
}

function updateRefreshSpinner(s) {
  const spinner = document.getElementById("refreshSpinner");
  if (!spinner) return;
  const busy = isSessionBusy(s);
  spinner.classList.toggle("spinning", busy);
  spinner.classList.toggle("hidden", !busy);
}

/** Single status line, compact progress bar, and unified activity log. */
function renderUnifiedActivityHub(s) {
  const summaryEl = document.getElementById("liveStatusSummary");
  const logEl = document.getElementById("activityLog");
  const progressWrap = document.getElementById("activityProgressWrap");
  const progressInner = document.getElementById("activityProgressInner");
  if (!summaryEl || !logEl) return;

  summaryEl.textContent = buildLiveStatusSummary(s);

  const running = (s.jobs || []).filter((j) => j.running);
  const job = running[0];
  if (progressWrap && progressInner) {
    if (job) {
      progressWrap.classList.remove("hidden");
      progressWrap.setAttribute("aria-hidden", "false");
      const tc = job.progress_total;
      const cur = job.progress_current;
      progressInner.classList.remove("indeterminate");
      if (typeof tc === "number" && tc > 0 && typeof cur === "number") {
        const pct = Math.min(100, Math.max(0, (100 * cur) / tc));
        progressInner.style.width = `${pct}%`;
      } else {
        progressInner.style.width = "";
        progressInner.classList.add("indeterminate");
      }
    } else {
      progressWrap.classList.add("hidden");
      progressWrap.setAttribute("aria-hidden", "true");
      progressInner.style.width = "";
      progressInner.classList.remove("indeterminate");
    }
  }

  logEl.textContent = buildActivityLogLines(s).join("\n");
  logEl.scrollTop = logEl.scrollHeight;
  updateRefreshSpinner(s);
}

function renderActivityHubMinimal(message) {
  const summaryEl = document.getElementById("liveStatusSummary");
  const logEl = document.getElementById("activityLog");
  if (summaryEl) summaryEl.textContent = message;
  if (logEl) logEl.textContent = message;
}

function clearThumbStrip(el) {
  el.innerHTML = "";
}

function fillMountThumbStrip(el, relpaths) {
  clearThumbStrip(el);
  for (const rel of relpaths || []) {
    const img = document.createElement("img");
    img.loading = "lazy";
    img.decoding = "async";
    img.width = 72;
    img.height = 72;
    img.alt = rel;
    img.src = `/api/thumbnail?relpath=${encodeURIComponent(rel)}`;
    el.appendChild(img);
  }
}

function fillHoldingThumbStrip(el, samples) {
  clearThumbStrip(el);
  for (const s of samples || []) {
    if (!s || !s.batch_id || !s.stored) continue;
    const img = document.createElement("img");
    img.loading = "lazy";
    img.decoding = "async";
    img.width = 72;
    img.height = 72;
    img.alt = s.stored;
    img.src = `/api/documents/holding-thumbnail?batch_id=${encodeURIComponent(s.batch_id)}&stored=${encodeURIComponent(s.stored)}`;
    el.appendChild(img);
  }
}

function docThumbPagerElements(ui = "panel") {
  if (ui === "dlg") {
    return {
      pager: "docDlgPreviewPager",
      label: "docDlgPreviewPagerLabel",
      prev: "btnDocDlgPreviewPrev",
      next: "btnDocDlgPreviewNext",
    };
  }
  return {
    pager: "docPreviewPager",
    label: "docPreviewPagerLabel",
    prev: "btnDocPreviewPrev",
    next: "btnDocPreviewNext",
  };
}

function updateThumbPagerUi(pagerEl, labelEl, prevBtn, nextBtn, page, total, pageSize) {
  const tp = total > 0 ? Math.ceil(total / pageSize) : 0;
  if (!pagerEl || !labelEl) return;
  if (total > pageSize) {
    pagerEl.classList.remove("hidden");
    labelEl.textContent = `Preview page ${page + 1} of ${tp} · ${total} image(s)`;
  } else if (total > 0) {
    pagerEl.classList.remove("hidden");
    labelEl.textContent = `${total} image(s)`;
  } else {
    pagerEl.classList.add("hidden");
    labelEl.textContent = "";
  }
  if (prevBtn) prevBtn.disabled = page <= 0 || tp <= 1;
  if (nextBtn) nextBtn.disabled = page >= tp - 1 || tp <= 1;
}

/** @returns {{ ok: boolean, count: number }} */
async function fillDocPreviewInto(summaryEl, stripEl, opts) {
  const { scope, vf, page = 0, pagerUi = "panel" } = opts;
  clearThumbStrip(stripEl);
  const offset = page * previewPageSize;
  const res = await fetch(
    `/api/documents/preview?scope=${encodeURIComponent(scope)}&include_visual_fallback=${vf ? "true" : "false"}&offset=${offset}&limit=${previewPageSize}`,
  );
  if (!res.ok) {
    const j = await res.json().catch(() => ({}));
    summaryEl.textContent = typeof j.detail === "string" ? j.detail : `Could not load matches (${res.status}).`;
    stripEl.classList.add("hidden");
    return { ok: false, count: 0 };
  }
  const j = await res.json();
  const n = j.count ?? 0;
  const bytes = j.total_bytes ?? 0;
  if (n === 0) {
    summaryEl.innerHTML = "<strong>No matches</strong> for these options — nothing would be removed.";
  } else {
    summaryEl.innerHTML = `<strong>${n}</strong> image(s) · about <strong>${formatHumanBytes(bytes)}</strong> <span class="muted">(${j.scope}, visual fallback ${j.include_visual_fallback ? "on" : "off"})</span>`;
  }
  fillMountThumbStrip(stripEl, j.thumbnail_sample_relpaths || []);
  stripEl.classList.toggle("hidden", n === 0);
  const thumbTotal = j.thumbnail_sample_total ?? (j.thumbnail_sample_relpaths || []).length;
  docPreviewPager.page = page;
  docPreviewPager.total = thumbTotal;
  docPreviewPager.scope = scope;
  docPreviewPager.vf = vf;
  docPreviewPager.ui = pagerUi;
  const pe = docThumbPagerElements(pagerUi);
  updateThumbPagerUi(
    document.getElementById(pe.pager),
    document.getElementById(pe.label),
    document.getElementById(pe.prev),
    document.getElementById(pe.next),
    page,
    thumbTotal,
    previewPageSize,
  );
  return { ok: true, count: n, thumbTotal };
}

function scheduleDocPanelPreview() {
  if (docPreviewDebounceTimer) clearTimeout(docPreviewDebounceTimer);
  docPreviewDebounceTimer = setTimeout(runDocPanelPreview, 380);
}

let lastMountForDoc = "";

async function runDocPanelPreview() {
  docPreviewDebounceTimer = null;
  const summaryEl = document.getElementById("docPreviewSummary");
  const stripEl = document.getElementById("docPreviewStrip");
  if (!summaryEl || !stripEl) return;
  if (!mountPath) {
    summaryEl.textContent = "Mount the phone to see matches.";
    stripEl.classList.add("hidden");
    clearThumbStrip(stripEl);
    return;
  }
  const ph = document.getElementById("phase")?.textContent || "";
  if (ph === "deleting" || ph === "unmounting" || ph === "mounting") return;
  const scopeEl = document.querySelector('input[name="docScope"]:checked');
  const scope = scopeEl ? scopeEl.value : "older_than_90d";
  const vf = document.getElementById("docVisualFallback")?.checked ?? false;
  const key = `${mountPath}|${scope}|${vf}`;
  if (key === lastDocPreviewKey) return;
  lastDocPreviewKey = key;
  docPreviewPager.page = 0;
  summaryEl.textContent = "Loading matches…";
  await fillDocPreviewInto(summaryEl, stripEl, { scope, vf, page: 0 });
}

async function loadDeletePreviewDialog(kind, page = 0) {
  const wf = workflowCfg(kind);
  const summary = document.getElementById(wf.deleteSummaryId);
  const strip = document.getElementById(wf.deleteStripId);
  const btnGo = document.getElementById(wf.btnConfirmDeleteId);
  const pagerKey = reviewPagerKey(kind);
  const sizeFilter = reviewPagerState[pagerKey].sizeFilter || "all";
  const sfMeta = sizeFilterMeta(sizeFilter);
  clearThumbStrip(strip);
  if (btnGo) btnGo.disabled = true;
  const offset = page * previewPageSize;
  const res = await fetch(
    `/api/delete/preview?kind=${encodeURIComponent(wf.kind)}&offset=${offset}&limit=${previewPageSize}&${sizeFilterQueryParam(sizeFilter)}`,
  );
  if (!res.ok) {
    const j = await res.json().catch(() => ({}));
    summary.textContent = typeof j.detail === "string" ? j.detail : `Preview failed (${res.status}).`;
    return;
  }
  const j = await res.json();
  const n = j.file_count ?? 0;
  const bytes = j.total_bytes ?? 0;
  const gw = j.groups_with_deletions ?? 0;
  const dg = j.duplicate_group_count ?? 0;
  if (n === 0) {
    summary.innerHTML =
      "<strong>Nothing queued to delete.</strong> In every duplicate group the keeper is the only file, or there are no duplicate groups.";
  } else {
    summary.innerHTML = `About to remove <strong>${n}</strong> image file(s) from <strong>${sfMeta.deleteScope}</strong>, freeing roughly <strong>${formatHumanBytes(bytes)}</strong>, across <strong>${gw}</strong> group(s) with extras in this view (of <strong>${dg}</strong> group(s) in this view). Other set sizes are not affected.`;
  }
  fillMountThumbStrip(
    strip,
    (j.thumbnail_samples || []).map((x) => x.relpath).filter(Boolean),
  );
  const thumbTotal = j.thumbnail_sample_total ?? (j.thumbnail_samples || []).length;
  deletePreviewPager[pagerKey].page = page;
  deletePreviewPager[pagerKey].total = thumbTotal;
  const pagerPrefix = pagerKey === "fuzzy" ? "Fuzzy" : "Exact";
  updateThumbPagerUi(
    document.getElementById(`delete${pagerPrefix}PreviewPager`),
    document.getElementById(`delete${pagerPrefix}PreviewPagerLabel`),
    document.getElementById(`btnDelete${pagerPrefix}PreviewPrev`),
    document.getElementById(`btnDelete${pagerPrefix}PreviewNext`),
    page,
    thumbTotal,
    previewPageSize,
  );
  if (btnGo) btnGo.disabled = n === 0;
}

async function loadDocRemoveDialogPreview(page = 0) {
  const scopeEl = document.querySelector('input[name="docScope"]:checked');
  const scope = scopeEl ? scopeEl.value : "older_than_90d";
  const vf = document.getElementById("docVisualFallback").checked;
  const summary = document.getElementById("docDlgPreviewSummary");
  const strip = document.getElementById("docDlgPreviewStrip");
  const btnGo = document.getElementById("btnDocConfirm");
  if (btnGo) btnGo.disabled = true;
  const r = await fillDocPreviewInto(summary, strip, { scope, vf, page, pagerUi: "dlg" });
  if (btnGo) btnGo.disabled = !r.ok || r.count === 0;
}

function populateFinalizeBatchSelect() {
  const sel = document.getElementById("docFinalizeBatchSelect");
  const wrap = document.getElementById("finalizeBatchPickWrap");
  if (!sel || !wrap) return;
  sel.innerHTML = "";
  const batches = lastDocumentBatches || [];
  if (!batches.length) {
    const o = document.createElement("option");
    o.value = "";
    o.textContent = "No batches in holding";
    sel.appendChild(o);
    return;
  }
  const ph = document.createElement("option");
  ph.value = "";
  ph.textContent = "Choose a batch…";
  sel.appendChild(ph);
  for (const b of batches) {
    const o = document.createElement("option");
    o.value = b.batch_id;
    o.textContent = `${b.batch_id} (${b.pending_restore_files} file(s))`;
    sel.appendChild(o);
  }
}

async function loadFinalizeDialogPreview() {
  const all = document.getElementById("docFinalizeAll").checked;
  const sel = document.getElementById("docFinalizeBatchSelect");
  const bid = sel && !all ? String(sel.value || "").trim() : "";
  const summary = document.getElementById("finalizePreviewSummary");
  const strip = document.getElementById("finalizePreviewStrip");
  const btnFin = document.getElementById("btnDocFinalizeConfirm");
  clearThumbStrip(strip);
  if (btnFin) btnFin.disabled = true;
  if (!all && !bid) {
    summary.innerHTML = "Choose <strong>All holding batches</strong> or pick one batch from the list.";
    if (btnFin) btnFin.disabled = true;
    return;
  }
  const q = all ? "all_batches=true" : `batch_id=${encodeURIComponent(bid)}`;
  const res = await fetch(`/api/documents/finalize-preview?${q}`);
  if (!res.ok) {
    const j = await res.json().catch(() => ({}));
    summary.textContent = typeof j.detail === "string" ? j.detail : `Preview failed (${res.status}).`;
    return;
  }
  const j = await res.json();
  const n = j.file_count ?? 0;
  const bytes = j.total_bytes ?? 0;
  const bc = j.batch_count ?? 0;
  if (n === 0) {
    summary.textContent = "Nothing in the holding area for this selection.";
  } else {
    summary.innerHTML = `Will permanently drop <strong>${n}</strong> file(s) from the Mac holding area (~<strong>${formatHumanBytes(bytes)}</strong>), across <strong>${bc}</strong> batch(es).`;
  }
  fillHoldingThumbStrip(strip, j.samples || []);
  if (btnFin) btnFin.disabled = n === 0;
}

function toast(msg) {
  const stack = document.getElementById("toastStack");
  const el = document.createElement("div");
  el.className = "toast";
  el.textContent = msg;
  stack.appendChild(el);
  setTimeout(() => el.remove(), 6500);
}

function setBanner(text, isError) {
  const b = document.getElementById("banner");
  if (!text) {
    b.classList.add("hidden");
    b.textContent = "";
    return;
  }
  b.textContent = text;
  b.classList.toggle("error", Boolean(isError));
  b.classList.remove("hidden");
}

function updateWorkflowReviewTeaser(s, kind) {
  const wf = workflowCfg(kind);
  const el = document.getElementById(wf.teaserId);
  if (!el) return;
  const gc =
    typeof s[wf.groupCountSnapshotKey] === "number"
      ? s[wf.groupCountSnapshotKey]
      : 0;
  const phase = s.phase || "";
  const runningKind = s.scan_running_kind || "";
  const hasMount = Boolean(s.mount_path);
  const label = kind === "fuzzy" ? "fuzzy burst" : "exact duplicate";
  if (!hasMount && gc === 0) {
    el.textContent = `Mount the iPhone to run a ${label} scan, or pick a saved scan above to review without remounting.`;
  } else if (!hasMount && gc > 0) {
    el.textContent = `${gc} ${label} group(s) loaded from a saved scan. Open review below (mount the iPhone to see photos and delete files).`;
  } else if (phase === "scanning" && runningKind === kind) {
    el.textContent = `${kind === "fuzzy" ? "Fuzzy" : "Exact"} scan in progress — watch the log at the top; use Cancel in this section or Unmount in step 6.`;
  } else if (gc === 0) {
    el.textContent = `No ${label} groups loaded yet. Run a scan in this section when ready.`;
  } else {
    el.textContent = `${gc} ${label} group(s). Open review — keep marks start from auto-ranked picks; tap tiles to adjust.`;
  }
}

function updateAllWorkflowReviewTeasers(s) {
  updateWorkflowReviewTeaser(s, "exact");
  updateWorkflowReviewTeaser(s, "fuzzy");
}

/** Drive the numbered guide, button disabled states, and the “Next” line from `/api/status` snapshots. */
function updateGuidedUI(s) {
  const trusted = Boolean(s.device && s.device.trusted);
  const hasMount = Boolean(s.mount_path);
  const phase = s.phase || "idle";
  const exactGc = typeof s.exact_group_count === "number" ? s.exact_group_count : 0;
  const fuzzyGc = typeof s.fuzzy_group_count === "number" ? s.fuzzy_group_count : 0;
  const cancelPending = Boolean(s.scan_cancel_pending);
  const runningKind = s.scan_running_kind || "";

  let current = 1;
  if (phase === "mounting") {
    current = 2;
  } else if (!trusted) {
    current = 1;
  } else if (!hasMount) {
    current = 2;
  } else if (phase === "scanning" && runningKind === "fuzzy") {
    current = 4;
  } else if (phase === "scanning" || phase === "mounted") {
    current = 3;
  } else if (phase === "reviewing" || phase === "deleting") {
    current = exactGc > 0 || fuzzyGc > 0 ? 3 : 5;
  } else if (phase === "unmounting") {
    current = 6;
  } else {
    current = 6;
  }

  document.querySelectorAll("[data-guide-step]").forEach((li) => {
    const n = Number(li.getAttribute("data-guide-step"));
    li.classList.remove("is-current", "is-done", "is-pending");
    li.removeAttribute("aria-current");
    if (n < current) {
      li.classList.add("is-done");
    } else if (n === current) {
      li.classList.add("is-current");
      li.setAttribute("aria-current", "step");
    } else {
      li.classList.add("is-pending");
    }
  });

  const na = document.getElementById("nextAction");
  if (!na) return;
  let next = "";
  if (!trusted) {
    next = "Connect USB, unlock the iPhone, tap Trust if asked, then check the device.";
  } else if (!hasMount) {
    next =
      phase === "mounting"
        ? "Mounting — status and log at the top refresh every 5 seconds."
        : "Mount iPhone media so this Mac can read your library.";
  } else if (phase === "scanning") {
    const which = runningKind === "fuzzy" ? "Fuzzy roll" : runningKind === "exact" ? "Exact duplicate" : "Scan";
    next = cancelPending
      ? `Stop requested — ${which} scan exits quickly between photos; when idle you can use the other workflow section or unmount.`
      : `${which} scan running — each file is logged at the top. Cancel in that workflow section below, or Unmount in step 6.`;
  } else if (phase === "mounted") {
    next =
      "Optional: use Exact duplicate photos or Fuzzy roll sections below (separate workflows), or document cleanup. Unmount before unplugging.";
  } else if (phase === "reviewing") {
    if (exactGc > 0 || fuzzyGc > 0) {
      const parts = [];
      if (exactGc > 0) parts.push(`${exactGc} exact group(s)`);
      if (fuzzyGc > 0) parts.push(`${fuzzyGc} fuzzy group(s)`);
      next = `Review in each workflow section (${parts.join(", ")}). Delete only removes unmarked files per workflow.`;
    } else {
      next = "No duplicate groups loaded — document cleanup below is optional, then unmount when done.";
    }
  } else if (phase === "deleting") {
    next =
      "Deletion running — status and log at the top refresh every 5 seconds. Unmount in step 6 stays available if you need to disconnect (close Finder windows on the iPhone volume first; if the volume is busy, wait a moment and retry).";
  } else if (phase === "unmounting") {
    next = "Unmounting…";
  } else if (hasMount) {
    next = "When finished editing, unmount (safe), then unplug USB.";
  } else {
    next = "";
  }
  na.textContent = next;

  const canStartScan =
    hasMount && (phase === "mounted" || phase === "reviewing") && phase !== "scanning";
  const mountBusy =
    phase === "mounting" || phase === "scanning" || phase === "deleting" || phase === "unmounting";
  const btnMount = document.getElementById("btnMount");
  if (btnMount) btnMount.disabled = !trusted || mountBusy;

  const exactBtn = document.getElementById("btnScan");
  if (exactBtn) exactBtn.disabled = !canStartScan;
  const fuzzyBtn = document.getElementById("btnScanFuzzy");
  if (fuzzyBtn) fuzzyBtn.disabled = !canStartScan;
  const exactCancel = document.getElementById("btnScanCancelExact");
  if (exactCancel) {
    exactCancel.disabled = phase !== "scanning" || runningKind !== "exact";
    exactCancel.textContent = cancelPending && runningKind === "exact" ? "Stopping…" : "Cancel exact scan";
  }
  const fuzzyCancel = document.getElementById("btnScanCancelFuzzy");
  if (fuzzyCancel) {
    fuzzyCancel.disabled = phase !== "scanning" || runningKind !== "fuzzy";
    fuzzyCancel.textContent = cancelPending && runningKind === "fuzzy" ? "Stopping…" : "Cancel fuzzy scan";
  }

  const exactHint = document.getElementById("exactScanRunHint");
  if (exactHint) {
    if (phase === "mounting") {
      exactHint.textContent = "Mount in progress — exact scan stays disabled until Mounted.";
    } else if (phase === "scanning" && runningKind === "exact") {
      exactHint.textContent = cancelPending
        ? "Stop requested — exact scan exits quickly between photos."
        : "Exact scan running — same-size near-identical files. Only this workflow’s cancel applies.";
    } else if (phase === "scanning" && runningKind === "fuzzy") {
      exactHint.textContent = "Fuzzy scan is running — wait for it to finish or cancel it in the Fuzzy roll section.";
    } else if (canStartScan) {
      exactHint.textContent =
        `Finds same-size near-identical duplicates across the library (full walk; fine for 10k–100k+ photos).${libraryIndexLabel(s)} Independent from fuzzy roll below.`;
    } else {
      exactHint.textContent = "";
    }
  }

  const fuzzyHint = document.getElementById("fuzzyScanRunHint");
  if (fuzzyHint) {
    const bs = typeof s.fuzzy_roll_batch_size === "number" ? s.fuzzy_roll_batch_size : 0;
    const fzTotal = typeof s.fuzzy_roll_total === "number" ? s.fuzzy_roll_total : null;
    const fzNext = typeof s.fuzzy_roll_next_start === "number" ? s.fuzzy_roll_next_start : 0;
    const fzEx = Boolean(s.fuzzy_roll_exhausted);
    if (phase === "mounting") {
      fuzzyHint.textContent = "Mount in progress — fuzzy scan stays disabled until Mounted.";
    } else if (phase === "scanning" && runningKind === "fuzzy") {
      fuzzyHint.textContent = cancelPending
        ? "Stop requested — fuzzy batch exits quickly between photos."
        : bs <= 0
          ? "Fuzzy scan running — entire library in one pass (capture-time order)."
          : `Fuzzy batch running — ~${bs} photos per click in capture-time order.`;
    } else if (phase === "scanning" && runningKind === "exact") {
      fuzzyHint.textContent = "Exact scan is running — wait for it to finish or cancel it in the Exact duplicate section.";
    } else if (canStartScan) {
      let progress = "";
      if (fzTotal != null && fzTotal > 0) {
        progress = fzEx
          ? ` Roll fully indexed (${fzTotal} photos). Run Fuzzy roll scan again to clear fuzzy groups and rescan (reuses cached hashes).`
          : ` Next batch: photo index ${fzNext} of ${fzTotal} (~${bs} per run).`;
      }
      const batchNote =
        bs <= 0
          ? " One click scans every photo on the mount (features cached on this Mac for faster re-runs)."
          : " Large libraries: run batches until the roll is fully indexed.";
      fuzzyHint.textContent =
        `Bursts and same-scene color sets in capture-time order (~2 min gap max between shots).${libraryIndexLabel(s)}${progress}${batchNote}`;
    } else {
      fuzzyHint.textContent = "";
    }
  }

  const mountNeeded = !hasMount;
  for (const kind of ["exact", "fuzzy"]) {
    const wf = workflowCfg(kind);
    const gc = kind === "fuzzy" ? fuzzyGc : exactGc;
    const dupOpen = document.getElementById(wf.btnOpenId);
    if (dupOpen) {
      dupOpen.disabled =
        gc === 0 || phase === "deleting" || phase === "unmounting" || phase === "mounting";
    }
    const delBtn = document.getElementById(wf.btnDeleteId);
    if (delBtn) {
      delBtn.disabled =
        mountNeeded ||
        gc === 0 ||
        phase === "scanning" ||
        phase === "deleting" ||
        phase === "mounting";
    }
  }
  const docFs = document.getElementById("docScopeFieldset");
  if (docFs) docFs.disabled = mountNeeded;

  ["btnDocRemove", "btnDocUndo", "btnDocFinalize"].forEach((id) => {
    document.getElementById(id).disabled = mountNeeded;
  });
}

function relPath(fullPath, mount) {
  if (!mount || !fullPath) return "";
  const m = String(mount).replace(/\/+$/, "");
  const fp = String(fullPath).replace(/\/+$/, "");
  if (fp === m) return "";
  if (fp.startsWith(`${m}/`)) return fp.slice(m.length + 1);
  return "";
}

function reviewThumbRel(fullPath, relpaths, index) {
  const fromApi = Array.isArray(relpaths) ? relpaths[index] : "";
  if (fromApi) return fromApi;
  return relPath(fullPath, mountPath) || relPath(fullPath, thumbnailMountRoot);
}

function updateLastDelete(ledger) {
  const panel = document.getElementById("lastDeletePanel");
  const body = document.getElementById("lastDeleteBody");
  if (!panel || !body) return;
  if (!ledger || ledger.deleted_count == null) {
    panel.classList.add("hidden");
    return;
  }
  panel.classList.remove("hidden");
  let text = `Deleted: ${ledger.deleted_count} · Failed: ${ledger.failed_count} · Skipped: ${ledger.skipped_count}.`;
  if (ledger.failed_count > 0) {
    text += " Check logs under data/logs (see ReadMe).";
  }
  body.textContent = text;
}

function updateDocumentBatchInfo(snapshot) {
  const el = document.getElementById("docBatchInfo");
  if (!el) return;
  const batches = snapshot.document_batches || [];
  if (!batches.length) {
    el.textContent = "No files waiting in the Mac holding area (undo buffer).";
    return;
  }
  const parts = batches.map((b) => `${b.batch_id}: ${b.pending_restore_files} file(s)`);
  el.textContent = `Holding: ${parts.join(" · ")}`;
}

function updateDocumentLedger(snapshot) {
  const panel = document.getElementById("docLedgerPanel");
  const body = document.getElementById("docLedgerBody");
  if (!panel || !body) return;
  const ledger = snapshot.document_last_ledger;
  if (!ledger || ledger.removed_from_device == null) {
    panel.classList.add("hidden");
    return;
  }
  panel.classList.remove("hidden");
  let t = `Removed from device: ${ledger.removed_from_device}`;
  if (ledger.batch_id) t += ` · batch ${ledger.batch_id}`;
  const fails = ledger.failed || [];
  if (fails.length) t += ` · issues: ${fails.length} (see app log)`;
  body.textContent = t;
}

function scrollToWorkflowPanel(kind) {
  const wf = workflowCfg(kind);
  const panel = document.getElementById(wf.panelId);
  if (panel) {
    panel.scrollIntoView({ behavior: "smooth", block: "start" });
    const btn = document.getElementById(wf.btnScanId);
    if (btn && !btn.disabled) btn.focus({ preventScroll: true });
  }
}

function setScanWorkspaceLoading(kind, on) {
  const wf = workflowCfg(kind);
  if (kind === "fuzzy") scanSessionsLoadingFuzzy = on;
  else scanSessionsLoadingExact = on;
  const wrap = document.getElementById(wf.loadingId);
  if (!wrap) return;
  wrap.classList.toggle("hidden", !on);
  wrap.setAttribute("aria-busy", on ? "true" : "false");
}

function scanSessionsLoadingFor(kind) {
  return kind === "fuzzy" ? scanSessionsLoadingFuzzy : scanSessionsLoadingExact;
}

function renderScanSessionList(kind, sessions, activeId) {
  const wf = workflowCfg(kind);
  const list = document.getElementById(wf.sessionListId);
  const empty = document.getElementById(wf.emptyId);
  if (!list || !empty) return;
  setActiveSessionIdFor(kind, activeId || "");
  list.innerHTML = "";
  const items = sessions || [];
  empty.classList.toggle("hidden", items.length > 0);
  list.classList.toggle("hidden", items.length === 0);
  for (const sess of items) {
    const sid = String(sess.id || "");
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "scan-session-item";
    btn.setAttribute("role", "option");
    btn.setAttribute("aria-selected", sid === activeSessionIdFor(kind) ? "true" : "false");
    if (sid === activeSessionIdFor(kind)) btn.classList.add("active");
    const gc = sess.group_count ?? 0;
    const active = sid === activeSessionIdFor(kind);
    btn.innerHTML = `<span class="scan-session-item-title">${sess.label || sid}</span><span class="scan-session-item-meta">${gc} group(s)${active ? " · active" : ""} — click to load &amp; review</span>`;
    btn.addEventListener("click", () => activateScanSession(kind, sid));
    list.appendChild(btn);
  }
}

async function fetchScanSessions(kind) {
  const res = await fetch(`/api/scan/sessions?kind=${encodeURIComponent(kind)}`);
  if (!res.ok) return null;
  return res.json();
}

async function refreshScanSessions(kind, preferredActiveId) {
  const data = await fetchScanSessions(kind);
  if (!data) return;
  const active = preferredActiveId || data.active_session_id || "";
  renderScanSessionList(kind, data.sessions || [], active);
}

async function refreshAllScanSessions(preferred) {
  await refreshScanSessions("exact", preferred?.exact);
  await refreshScanSessions("fuzzy", preferred?.fuzzy);
}

async function activateScanSession(kind, sessionId) {
  if (!sessionId || scanSessionsLoadingFor(kind)) return;
  const reopenOnly = sessionId === activeSessionIdFor(kind);
  setScanWorkspaceLoading(kind, true);
  const wf = workflowCfg(kind);
  document.querySelectorAll(`#${wf.sessionListId} .scan-session-item`).forEach((el) => {
    el.disabled = true;
  });
  try {
    if (!reopenOnly) {
      const res = await fetch("/api/scan/sessions/activate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId }),
      });
      const j = await res.json().catch(() => ({}));
      if (!res.ok) {
        toast(typeof j.detail === "string" ? j.detail : `Could not load scan (${res.status}).`);
        return;
      }
      await refreshAllScanSessions({ [kind]: sessionId });
      const kindCount =
        typeof j.kind_group_count === "number"
          ? j.kind_group_count
          : kind === "fuzzy"
            ? j.fuzzy_group_count
            : j.exact_group_count;
      await refreshStatusFromServer();
      if ((kindCount ?? 0) > 0) {
        await openPagedReview(kind, { page: 0 });
        toast(j.session?.label ? `Loaded ${j.session.label}` : "Scan loaded — review opened.");
      } else {
        toast(
          j.session?.label
            ? `${j.session.label} has no duplicate groups yet. Mount the phone and run a new scan to refresh it.`
            : "Scan loaded but has no groups — run a new scan when the phone is mounted.",
        );
      }
      return;
    }
    await refreshAllScanSessions({ [kind]: sessionId });
    const snap = lastSnapshot || (await fetchStatusSnapshot());
    const gc =
      kind === "fuzzy"
        ? snap?.fuzzy_group_count ?? 0
        : snap?.exact_group_count ?? 0;
    if (gc > 0) {
      await openPagedReview(kind, { page: 0 });
      toast("Review opened for the active saved scan.");
    } else {
      toast("This saved scan has no duplicate groups. Run a new scan when the phone is mounted.");
    }
  } finally {
    setScanWorkspaceLoading(kind, false);
    document.querySelectorAll(`#${wf.sessionListId} .scan-session-item`).forEach((el) => {
      el.disabled = false;
    });
  }
}

function applySnapshot(s) {
  if (!s || typeof s !== "object") return;
  renderUnifiedActivityHub(s);

  const phaseEl = document.getElementById("phase");
  if (phaseEl) phaseEl.textContent = s.phase || "unknown";

  if (typeof s.groups_page_size === "number" && s.groups_page_size > 0) {
    groupsPageSize = s.groups_page_size;
  }
  if (typeof s.review_thumbnail_max_edge === "number" && s.review_thumbnail_max_edge > 0) {
    reviewThumbEdge = s.review_thumbnail_max_edge;
  }
  if (typeof s.preview_page_size === "number" && s.preview_page_size > 0) {
    previewPageSize = s.preview_page_size;
  }
  if (typeof s.images_per_group_page === "number" && s.images_per_group_page > 0) {
    imagesPerGroupPage = s.images_per_group_page;
  }
  const mp = s.mount_path || "";
  const exactActive = s.active_exact_scan_session_id || "";
  const fuzzyActive = s.active_fuzzy_scan_session_id || "";
  if (exactActive !== activeExactScanSessionId || fuzzyActive !== activeFuzzyScanSessionId) {
    activeExactScanSessionId = exactActive;
    activeFuzzyScanSessionId = fuzzyActive;
    refreshAllScanSessions({ exact: exactActive, fuzzy: fuzzyActive });
  }
  if (mp !== lastMountForDoc) {
    lastMountForDoc = mp;
    lastDocPreviewKey = "";
  }
  lastDocumentBatches = s.document_batches || [];

  mountPath = s.mount_path || "";
  thumbnailMountRoot = s.configured_mount_point || mountPath || "";
  const mountEl = document.getElementById("mount");
  if (mountEl) mountEl.textContent = mountPath || "—";
  const dev = s.device;
  const deviceEl = document.getElementById("device");
  if (deviceEl) {
    if (dev && dev.trusted) {
      deviceEl.textContent = `${dev.name || "iPhone"} (${dev.udid || ""})`;
    } else if (dev && dev.error) {
      deviceEl.textContent = dev.error;
    } else {
      deviceEl.textContent = "Not connected / not trusted";
    }
  }
  if (s.last_error) {
    setBanner(s.last_error, true);
  } else {
    setBanner("", false);
  }
  updateLastDelete(s.last_delete_ledger);
  updateDocumentBatchInfo(s);
  updateDocumentLedger(s);
  markRefreshed(s);
  updateAllWorkflowReviewTeasers(s);
  lastSnapshot = s;
  if (s.phase && s.phase !== lastPhase) {
    if (
      s.phase === "mounted" &&
      lastPhase &&
      (lastPhase === "mounting" || lastPhase === "device_detected" || lastPhase === "idle")
    ) {
      toast("iPhone media mounted.");
    } else if (s.phase === "reviewing" && lastPhase === "scanning") {
      const rk = s.scan_running_kind || lastScanKind || "exact";
      const count = rk === "fuzzy" ? s.fuzzy_group_count : s.exact_group_count;
      toast(rk === "fuzzy" ? "Fuzzy roll batch finished." : "Exact duplicate scan finished.");
      refreshAllScanSessions({
        exact: s.active_exact_scan_session_id,
        fuzzy: s.active_fuzzy_scan_session_id,
      });
      if (autoReviewEnabled && count > 0) {
        openPagedReview(rk, { page: 0 });
      }
    }
    if (s.phase === "scanning" && s.scan_running_kind) lastScanKind = s.scan_running_kind;
    lastPhase = s.phase;
  }
  updateGuidedUI(s);

  const phase = s.phase || "";
  if (mp && phase !== "deleting" && phase !== "unmounting" && phase !== "mounting") {
    scheduleDocPanelPreview();
  }
}


function updateReviewSizeFilterUi(kind) {
  const wf = workflowCfg(kind);
  const key = reviewPagerKey(kind);
  const st = reviewPagerState[key];
  const meta = sizeFilterMeta(st.sizeFilter);
  const slider = document.getElementById(wf.sizeSliderId);
  const valueEl = document.getElementById(wf.sizeValueId);
  const hintEl = document.getElementById(wf.sizeHintId);
  const btnDel = document.getElementById(wf.btnDeleteId);
  const idx = sliderIndexFromSizeFilter(st.sizeFilter);
  if (slider) {
    slider.value = String(idx);
    slider.setAttribute("aria-valuenow", String(idx));
  }
  if (valueEl) valueEl.textContent = meta.label;
  const counts = st.sizeCounts || {};
  const inView = st.total ?? counts[st.sizeFilter] ?? counts.all ?? 0;
  if (hintEl) {
    if (st.sizeFilter === "all") {
      hintEl.textContent =
        counts.all != null
          ? `${counts.all} set(s) total · delete affects every set where you left extras un-kept.`
          : "Delete affects every set where you left extras un-kept.";
    } else {
      const bucket = counts[st.sizeFilter] ?? 0;
      hintEl.textContent = `${inView} set(s) in this view (${bucket} with ${meta.short} in scan). Delete and keep changes apply only here — other sizes are untouched.`;
    }
  }
  if (btnDel) {
    const kindLabel = wf.kind === "fuzzy" ? "fuzzy bursts" : "exact duplicates";
    btnDel.textContent =
      st.sizeFilter === "all"
        ? `Delete non-kept ${kindLabel}…`
        : `Delete non-kept (${meta.short} only)…`;
  }
}

function updateGroupsPagerUi(kind) {
  const wf = workflowCfg(kind);
  const st = reviewPagerState[reviewPagerKey(kind)];
  const pager = document.getElementById(`${kind === "fuzzy" ? "fuzzy" : "exact"}GroupsPager`);
  const label = document.getElementById(wf.pagerLabelId);
  const prev = document.getElementById(wf.pagerPrevId);
  const next = document.getElementById(wf.pagerNextId);
  const tp = reviewTotalPages(st.total);
  if (!pager || !label) return;
  const sf = sizeFilterMeta(st.sizeFilter);
  if (st.total > 0 && tp > 1) {
    pager.classList.remove("hidden");
    label.textContent = `Page ${st.page + 1} of ${tp} · ${st.total} ${sf.short} set(s)`;
  } else if (st.total > 0) {
    pager.classList.remove("hidden");
    label.textContent = `${st.total} ${sf.short} set(s) on this page — scroll each row to compare photos`;
  } else {
    pager.classList.add("hidden");
    label.textContent = "No groups on this scan";
  }
  if (prev) prev.disabled = st.loading || st.page <= 0 || tp <= 1;
  if (next) next.disabled = st.loading || st.page >= tp - 1 || tp <= 1;
}

function renderOneGroup(kind, g, keep) {
  const paths = g.paths || [];
  const rawK = keep[g.id];
  const keepList = Array.isArray(rawK) ? rawK : rawK ? [rawK] : [];
  const keepSet = new Set(keepList);
  const nk = keepSet.size;
  let sk = "";
  if (g.scan_kind === "fuzzy") {
    const reasonLabels = {
      visual: "Burst",
      palette: "Same palette",
      color: "Color match",
      grid_exact: "Scene grid",
      mixed: "Mixed",
    };
    const r = g.fuzzy_match_reason;
    const label = reasonLabels[r] || "Fuzzy set";
    const mod = g.fuzzy_link_strength === "moderate" ? " · moderate" : "";
    sk = `${label}${mod} · `;
  }
  const suffix =
    nk === paths.length && paths.length > 0 ? " — all marked keep (no deletes here)" : "";

  const section = document.createElement("section");
  section.className = "review-set group";
  section.dataset.groupId = String(g.id);

  const header = document.createElement("div");
  header.className = "review-set-header group-header";
  const titleText = document.createElement("span");
  titleText.className = "group-title-text";
  titleText.textContent = `${sk}Set ${g.id} · ${nk}/${paths.length} keep · ~${Math.round((g.bytesSavedIfOneKept || 0) / 1024)} KB${suffix}`;
  const btnKeepAll = document.createElement("button");
  btnKeepAll.type = "button";
  btnKeepAll.className = "ghost keep-all-in-group";
  btnKeepAll.textContent = "Keep all in set";
  btnKeepAll.addEventListener("click", async (ev) => {
    ev.preventDefault();
    if (!paths.length) return;
    await fetch("/api/selection", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ group_id: g.id, keep_paths: [...paths] }),
    });
    await loadReviewPage(kind, reviewPagerState[kind === "fuzzy" ? "fuzzy" : "exact"].page);
  });
  header.appendChild(titleText);
  header.appendChild(btnKeepAll);

  const tiles = document.createElement("div");
  tiles.className = "tiles review-set-photos";
  tiles.style.setProperty("--set-photo-count", String(Math.max(paths.length, 1)));
  tiles.setAttribute("role", "list");
  tiles.setAttribute("aria-label", `Photos in set ${g.id}`);

  const relpaths = g.relpaths || [];

  for (let pi = 0; pi < paths.length; pi += 1) {
    const p = paths[pi];
    const tile = document.createElement("div");
    tile.className = "tile review-photo-tile";
    tile.setAttribute("role", "listitem");
    const rel = reviewThumbRel(p, relpaths, pi);
    const sel = keepSet.has(p);
    if (sel) tile.classList.add("selected");
    tile.setAttribute("aria-pressed", sel ? "true" : "false");
    tile.setAttribute("aria-label", `${sel ? "Keeping" : "Not keeping"} ${p.split("/").pop() || p}`);

    const mark = document.createElement("span");
    mark.className = "tile-keep-mark";
    mark.setAttribute("aria-hidden", "true");
    mark.textContent = sel ? "✓" : "";

    const media = document.createElement("div");
    media.className = "tile-media";
    const img = document.createElement("img");
    img.className = "review-photo-img";
    img.loading = "lazy";
    img.decoding = "async";
    const baseName = p.split("/").pop() || "photo";
    if (rel) {
      img.src = `/api/thumbnail?relpath=${encodeURIComponent(rel)}&max_edge=${reviewThumbEdge}`;
      img.alt = baseName;
    } else {
      img.classList.add("review-photo-missing");
      img.alt = `${baseName} — mount the iPhone to load this photo.`;
    }
    media.appendChild(img);

    const cap = document.createElement("div");
    cap.className = "cap";
    cap.textContent = p.split("/").pop() || p;

    tile.appendChild(mark);
    tile.appendChild(media);
    tile.appendChild(cap);
    tile.addEventListener("click", async () => {
      const res = await fetch("/api/selection", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ group_id: g.id, toggle_path: p }),
      });
      if (!res.ok) return;
      const nowSel = !tile.classList.contains("selected");
      tile.classList.toggle("selected", nowSel);
      tile.setAttribute("aria-pressed", nowSel ? "true" : "false");
      tile.setAttribute(
        "aria-label",
        `${nowSel ? "Keeping" : "Not keeping"} ${p.split("/").pop() || p}`,
      );
      mark.textContent = nowSel ? "✓" : "";
    });
    tiles.appendChild(tile);
  }

  section.appendChild(header);
  section.appendChild(tiles);
  return section;
}

async function loadReviewPage(kind, page, opts = {}) {
  const wf = workflowCfg(kind);
  const key = reviewPagerKey(kind);
  const st = reviewPagerState[key];
  const root = document.getElementById(wf.groupsId);
  if (!root || st.loading) return;
  st.loading = true;
  updateGroupsPagerUi(kind);
  updateReviewSizeFilterUi(kind);
  try {
    const offset = page * groupsPageSize;
    const res = await fetch(
      `/api/scan/groups?kind=${encodeURIComponent(wf.kind)}&offset=${offset}&limit=${groupsPageSize}&${sizeFilterQueryParam(st.sizeFilter)}`,
    );
    if (!res.ok) return;
    const data = await res.json();
    const groups = data.groups || [];
    const keep = data.keep || {};
    st.total = typeof data.total === "number" ? data.total : groups.length;
    st.page = clampReviewPage(page, st.total);
    if (data.size_counts) st.sizeCounts = data.size_counts;
    if (data.size_filter) st.sizeFilter = data.size_filter;
    root.innerHTML = "";
    if (!groups.length) {
      const empty = document.createElement("p");
      empty.className = "hint muted review-page-empty";
      const sf = sizeFilterMeta(st.sizeFilter);
      empty.textContent =
        st.sizeFilter === "all"
          ? "No duplicate groups on this page."
          : `No ${sf.short} sets on this page. Try another size or All.`;
      root.appendChild(empty);
    }
    for (const g of groups) {
      root.appendChild(renderOneGroup(kind, g, keep));
    }
    root.scrollTop = 0;
    const title = document.getElementById(wf.titleId);
    if (title) {
      const tp = reviewTotalPages(st.total);
      const sf = sizeFilterMeta(st.sizeFilter);
      title.textContent =
        wf.kind === "fuzzy"
          ? `Fuzzy burst review · ${sf.short} · page ${st.page + 1}${tp ? ` of ${tp}` : ""}`
          : `Exact duplicate review · ${sf.short} · page ${st.page + 1}${tp ? ` of ${tp}` : ""}`;
    }
  } finally {
    st.loading = false;
    updateGroupsPagerUi(kind);
    updateReviewSizeFilterUi(kind);
  }
}

async function setReviewSizeFilter(kind, filterId, opts = {}) {
  const key = reviewPagerKey(kind);
  const st = reviewPagerState[key];
  const next = REVIEW_SIZE_FILTERS.some((x) => x.id === filterId) ? filterId : "all";
  if (st.sizeFilter === next && !opts.force) return;
  st.sizeFilter = next;
  st.page = 0;
  deletePreviewPager[key].page = 0;
  await loadReviewPage(kind, 0);
}

async function openPagedReview(kind, opts = {}) {
  const wf = workflowCfg(kind);
  const dlg = document.getElementById(wf.dlgId);
  const key = reviewPagerKey(kind);
  const st = reviewPagerState[key];
  const page = typeof opts.page === "number" ? opts.page : 0;
  if (opts.sizeFilter) st.sizeFilter = opts.sizeFilter;
  const slider = document.getElementById(workflowCfg(kind).sizeSliderId);
  if (slider) slider.value = String(sliderIndexFromSizeFilter(st.sizeFilter));
  await loadReviewPage(kind, page);
  if (dlg && (opts.showDialog !== false)) {
    if (!dlg.open) dlg.showModal();
    st.dialogOpen = true;
  }
}

async function gotoReviewPage(kind, delta) {
  const key = reviewPagerKey(kind);
  const st = reviewPagerState[key];
  const next = clampReviewPage(st.page + delta, st.total);
  if (next === st.page) return;
  await loadReviewPage(kind, next);
}


function wireStepJumps() {
  document.querySelectorAll(".step-jump").forEach((el) => {
    const target = el.getAttribute("data-jump-target");
    if (!target) return;
    const go = () => {
      const dest = document.getElementById(target);
      if (dest) dest.scrollIntoView({ behavior: "smooth", block: "start" });
    };
    el.addEventListener("click", go);
    el.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        go();
      }
    });
  });
}

async function fetchStatusSnapshot() {
  const ctrl = new AbortController();
  const timer = window.setTimeout(() => ctrl.abort(), STATUS_FETCH_TIMEOUT_MS);
  try {
    const st = await fetch("/api/status", { signal: ctrl.signal, cache: "no-store" });
    if (!st.ok) return null;
    return st.json();
  } finally {
    window.clearTimeout(timer);
  }
}

async function refreshStatusFromServer() {
  if (statusPollInFlight) return;
  statusPollInFlight = true;
  try {
    const snap = await fetchStatusSnapshot();
    if (snap) {
      statusPollFailStreak = 0;
      if (typeof snap.status_poll_interval_ms === "number" && snap.status_poll_interval_ms > 0) {
        window.__APP_POLL_MS__ = snap.status_poll_interval_ms;
      }
      applySnapshot(snap);
    } else {
      statusPollFailStreak += 1;
      if (statusPollFailStreak >= 2) {
        setBanner("Could not reach the app server — check that scripts/run.sh is still running.", true);
      }
    }
  } catch {
    statusPollFailStreak += 1;
    if (statusPollFailStreak >= 2) {
      setBanner("Status refresh failed — retrying automatically.", true);
    }
  } finally {
    statusPollInFlight = false;
    scheduleStatusPoll();
  }
}

function scheduleStatusPoll() {
  if (statusPollTimer) clearTimeout(statusPollTimer);
  const delay = statusPollIntervalMs(lastSnapshot);
  statusPollTimer = setTimeout(() => {
    refreshStatusFromServer();
  }, delay);
}

function startStatusPolling() {
  if (statusPollTimer) clearTimeout(statusPollTimer);
  refreshStatusFromServer();
}

function wireWorkflowReview(kind) {
  const wf = workflowCfg(kind);
  const dlg = document.getElementById(wf.dlgId);
  const openPanel = async () => {
    await openPagedReview(kind, { page: 0 });
  };
  const closePanel = () => {
    if (dlg && dlg.open) dlg.close();
    reviewPagerState[reviewPagerKey(kind)].dialogOpen = false;
  };
  document.getElementById(wf.btnOpenId)?.addEventListener("click", () => openPanel());
  document.getElementById(wf.btnCloseId)?.addEventListener("click", () => closePanel());
  document.getElementById(wf.btnCloseFooterId)?.addEventListener("click", () => closePanel());
  document.getElementById(wf.pagerPrevId)?.addEventListener("click", () => gotoReviewPage(kind, -1));
  document.getElementById(wf.pagerNextId)?.addEventListener("click", () => gotoReviewPage(kind, 1));
  const sizeSlider = document.getElementById(wf.sizeSliderId);
  sizeSlider?.addEventListener("input", () => {
    const filterId = sizeFilterFromSliderIndex(sizeSlider.value);
    const meta = sizeFilterMeta(filterId);
    const valueEl = document.getElementById(wf.sizeValueId);
    if (valueEl) valueEl.textContent = meta.label;
    sizeSlider.setAttribute("aria-valuenow", String(sliderIndexFromSizeFilter(filterId)));
  });
  sizeSlider?.addEventListener("change", () => {
    setReviewSizeFilter(kind, sizeFilterFromSliderIndex(sizeSlider.value));
  });

  const dlgDel = document.getElementById(wf.dlgDeleteId);
  const delPagerPrefix = kind === "fuzzy" ? "Fuzzy" : "Exact";
  document.getElementById(wf.btnDeleteId)?.addEventListener("click", async () => {
    deletePreviewPager[kind === "fuzzy" ? "fuzzy" : "exact"].page = 0;
    await loadDeletePreviewDialog(kind, 0);
    if (dlgDel) dlgDel.showModal();
  });
  document.getElementById(`btnDelete${delPagerPrefix}PreviewPrev`)?.addEventListener("click", async () => {
    const st = deletePreviewPager[kind === "fuzzy" ? "fuzzy" : "exact"];
    if (st.page > 0) await loadDeletePreviewDialog(kind, st.page - 1);
  });
  document.getElementById(`btnDelete${delPagerPrefix}PreviewNext`)?.addEventListener("click", async () => {
    const st = deletePreviewPager[kind === "fuzzy" ? "fuzzy" : "exact"];
    const tp = st.total > 0 ? Math.ceil(st.total / previewPageSize) : 0;
    if (st.page < tp - 1) await loadDeletePreviewDialog(kind, st.page + 1);
  });
  document.getElementById(wf.btnCancelDeleteId)?.addEventListener("click", () => dlgDel?.close());
  document.getElementById(wf.btnConfirmDeleteId)?.addEventListener("click", async (e) => {
    e.preventDefault();
    scrollToLiveProgress();
    const phrase = document.getElementById(wf.confirmInputId)?.value.trim() || "";
    const sizeFilter = reviewPagerState[reviewPagerKey(kind)].sizeFilter || "all";
    optimisticBusyPhase("deleting");
    const res = await fetch(
      `/api/delete?kind=${encodeURIComponent(wf.kind)}&${sizeFilterQueryParam(sizeFilter)}`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ paths: [], confirm: phrase }),
      },
    );
    const j = await res.json().catch(() => ({}));
    if (!res.ok) toast(`Delete blocked: ${j.detail || res.status}`);
    else toast("Deleting — watch live progress at the top.");
    dlgDel?.close();
    const inp = document.getElementById(wf.confirmInputId);
    if (inp) inp.value = "";
    await refreshStatusFromServer();
  });
}

function wireButtons() {
  wireStepJumps();
  wireWorkflowReview("exact");
  wireWorkflowReview("fuzzy");
  document.getElementById("btnDocPreviewPrev")?.addEventListener("click", async () => {
    if (docPreviewPager.page > 0) {
      docPreviewPager.page -= 1;
      const summaryEl = document.getElementById("docPreviewSummary");
      const stripEl = document.getElementById("docPreviewStrip");
      await fillDocPreviewInto(summaryEl, stripEl, {
        scope: docPreviewPager.scope,
        vf: docPreviewPager.vf,
        page: docPreviewPager.page,
        pagerUi: "panel",
      });
    }
  });
  document.getElementById("btnDocPreviewNext")?.addEventListener("click", async () => {
    const tp = docPreviewPager.total > 0 ? Math.ceil(docPreviewPager.total / previewPageSize) : 0;
    if (docPreviewPager.page < tp - 1) {
      docPreviewPager.page += 1;
      const summaryEl = document.getElementById("docPreviewSummary");
      const stripEl = document.getElementById("docPreviewStrip");
      await fillDocPreviewInto(summaryEl, stripEl, {
        scope: docPreviewPager.scope,
        vf: docPreviewPager.vf,
        page: docPreviewPager.page,
        pagerUi: "panel",
      });
    }
  });
  const docDlgPage = async (delta) => {
    const tp = docPreviewPager.total > 0 ? Math.ceil(docPreviewPager.total / previewPageSize) : 0;
    const next = docPreviewPager.page + delta;
    if (next < 0 || next >= tp) return;
    await loadDocRemoveDialogPreview(next);
  };
  document.getElementById("btnDocDlgPreviewPrev")?.addEventListener("click", () => docDlgPage(-1));
  document.getElementById("btnDocDlgPreviewNext")?.addEventListener("click", () => docDlgPage(1));

  document.getElementById("btnActivityLogClear")?.addEventListener("click", async () => {
    scrollToLiveProgress();
    const res = await fetch("/api/activity-log/clear", { method: "POST" });
    const j = await res.json().catch(() => ({}));
    if (!res.ok) {
      toast(`Clear activity log failed: ${typeof j.detail === "string" ? j.detail : res.status}`);
      return;
    }
    toast(j.message || "Activity log cleared.");
    await refreshStatusFromServer();
  });

  document.getElementById("btnDevice").addEventListener("click", async () => {
    scrollToLiveProgress();
    toast("Checking USB device…");
    const res = await fetch("/api/device");
    const j = await res.json();
    if (!res.ok) toast(`Device check failed: ${j.detail || res.status}`);
    else toast(j.trusted ? "Device trusted." : "Device not ready yet.");
    await refreshStatusFromServer();
  });
  document.getElementById("btnMount").addEventListener("click", async () => {
    scrollToLiveProgress();
    optimisticBusyPhase("mounting");
    toast("Mount started — live updates appear at the top of the page.");
    const res = await fetch("/api/mount", { method: "POST" });
    const j = await res.json().catch(() => ({}));
    if (res.status === 409) {
      toast(typeof j.detail === "string" ? j.detail : "Mount already in progress.");
    } else if (!res.ok) {
      toast(`Mount failed: ${j.detail || res.status}`);
    } else if (!j.started) {
      toast(j.message || "Mounted.");
    }
    await refreshStatusFromServer();
  });
  document.getElementById("btnUnmount").addEventListener("click", async () => {
    scrollToLiveProgress();
    optimisticBusyPhase("unmounting");
    toast("Unmounting — close Finder windows pointing at the mount first.");
    const res = await fetch("/api/unmount", { method: "POST" });
    const j = await res.json().catch(() => ({}));
    if (!res.ok) toast(`Unmount failed: ${j.detail || res.status}`);
    else toast(j.ok ? "Unmount complete. Safe to unplug after this succeeds." : String(j.message || "Unmount issue"));
    await refreshStatusFromServer();
  });
  async function postScanStart(kind) {
    scrollToLiveProgress();
    optimisticBusyPhase("scanning", kind);
    const q = kind === "fuzzy" ? "?kind=fuzzy" : "?kind=exact";
    const res = await fetch(`/api/scan/start${q}`, { method: "POST" });
    const j = await res.json().catch(() => ({}));
    const detail = typeof j.detail === "string" ? j.detail : "";
    if (res.status === 409) {
      toast(detail || "Request conflict — try again.");
    } else if (!res.ok) {
      toast(`Scan rejected: ${detail || res.status}`);
    } else {
      toast(j.message || "Scan started — watch live progress at the top.");
    }
    await refreshStatusFromServer();
  }
  document.getElementById("btnScan").addEventListener("click", () => postScanStart("exact"));
  document.getElementById("btnScanFuzzy")?.addEventListener("click", () => postScanStart("fuzzy"));
  async function postScanCancel() {
    scrollToLiveProgress();
    const res = await fetch("/api/scan/cancel", { method: "POST" });
    const j = await res.json().catch(() => ({}));
    if (!res.ok) {
      toast(`Cancel: ${typeof j.detail === "string" ? j.detail : res.status}`);
      return;
    }
    if (j.noop) toast(j.message || "No scan was running.");
    else toast(j.message || "Stop requested.");
    await refreshStatusFromServer();
  }
  document.getElementById("btnScanCancelExact")?.addEventListener("click", () => postScanCancel());
  document.getElementById("btnScanCancelFuzzy")?.addEventListener("click", () => postScanCancel());

  const dlgDoc = document.getElementById("dlgDocuments");
  const dlgFin = document.getElementById("dlgDocFinalize");

  document.querySelectorAll('input[name="docScope"]').forEach((el) => {
    el.addEventListener("change", () => {
      lastDocPreviewKey = "";
      scheduleDocPanelPreview();
    });
  });
  const vfEl = document.getElementById("docVisualFallback");
  if (vfEl) {
    vfEl.addEventListener("change", () => {
      lastDocPreviewKey = "";
      scheduleDocPanelPreview();
    });
  }

  document.getElementById("btnDocRemove").addEventListener("click", async () => {
    await loadDocRemoveDialogPreview();
    dlgDoc.showModal();
  });
  document.getElementById("btnDocCancel").addEventListener("click", () => dlgDoc.close());
  document.getElementById("btnDocConfirm").addEventListener("click", async (e) => {
    e.preventDefault();
    scrollToLiveProgress();
    mergeSnapshotPatch({
      jobs: [
        {
          running: true,
          kind: "document_remove",
          label: "Removing document images…",
          message: "Starting…",
        },
      ],
    });
    const scopeEl = document.querySelector('input[name="docScope"]:checked');
    const scope = scopeEl ? scopeEl.value : "older_than_90d";
    const include_visual_fallback = document.getElementById("docVisualFallback").checked;
    const confirm = document.getElementById("docConfirmInput").value.trim();
    const res = await fetch("/api/documents/remove", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ scope, confirm, include_visual_fallback }),
    });
    const j = await res.json().catch(() => ({}));
    if (!res.ok) toast(`Document removal blocked: ${j.detail || res.status}`);
    else {
      lastDocPreviewKey = "";
      scheduleDocPanelPreview();
      toast("Copying to Mac and removing from phone — watch live progress at the top.");
    }
    dlgDoc.close();
    document.getElementById("docConfirmInput").value = "";
    await refreshStatusFromServer();
  });
  document.getElementById("btnDocUndo").addEventListener("click", async () => {
    scrollToLiveProgress();
    const res = await fetch("/api/documents/undo", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    const j = await res.json().catch(() => ({}));
    if (!res.ok) toast(`Undo failed: ${j.detail || res.status}`);
    else toast(`Restored ${(j.restored || []).length} file(s) to the phone.`);
    await refreshStatusFromServer();
  });

  const finAll = document.getElementById("docFinalizeAll");
  const finWrap = document.getElementById("finalizeBatchPickWrap");
  const finSel = document.getElementById("docFinalizeBatchSelect");
  finAll.addEventListener("change", () => {
    if (finWrap) finWrap.classList.toggle("hidden", finAll.checked);
    if (!finAll.checked && lastDocumentBatches.length === 1 && finSel) {
      finSel.value = lastDocumentBatches[0].batch_id;
    }
    loadFinalizeDialogPreview();
  });
  if (finSel) finSel.addEventListener("change", () => loadFinalizeDialogPreview());

  document.getElementById("btnDocFinalize").addEventListener("click", async () => {
    populateFinalizeBatchSelect();
    if (finWrap) finWrap.classList.toggle("hidden", finAll.checked);
    await loadFinalizeDialogPreview();
    dlgFin.showModal();
  });
  document.getElementById("btnDocFinalizeCancel").addEventListener("click", () => dlgFin.close());
  document.getElementById("btnDocFinalizeConfirm").addEventListener("click", async (e) => {
    e.preventDefault();
    const all = document.getElementById("docFinalizeAll").checked;
    const bid = finSel && !all ? String(finSel.value || "").trim() : "";
    if (!all && !bid) {
      toast("Pick a batch from the list, or choose all batches.");
      return;
    }
    const body = all ? { batch_id: null } : { batch_id: bid };
    const res = await fetch("/api/documents/finalize", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const j = await res.json().catch(() => ({}));
    if (!res.ok) toast(`Finalize failed: ${j.detail || res.status}`);
    else toast("Mac holding copies dropped.");
    dlgFin.close();
    await refreshStatusFromServer();
  });
}

function startSse() {
  const es = new EventSource("/api/events");
  es.onmessage = async (ev) => {
    try {
      const s = JSON.parse(ev.data);
      if (typeof s.status_poll_interval_ms === "number" && s.status_poll_interval_ms > 0) {
        window.__APP_POLL_MS__ = s.status_poll_interval_ms;
      }
      applySnapshot(s);
      markRefreshed(s);
      const ex = typeof s.exact_group_count === "number" ? s.exact_group_count : 0;
      const fz = typeof s.fuzzy_group_count === "number" ? s.fuzzy_group_count : 0;
      if (ex !== lastLoadedGroupCounts.exact) {
        lastLoadedGroupCounts.exact = ex;
        if (ex > 0 && reviewPagerState.exact.dialogOpen) {
          await loadReviewPage("exact", reviewPagerState.exact.page);
        }
      }
      if (fz !== lastLoadedGroupCounts.fuzzy) {
        lastLoadedGroupCounts.fuzzy = fz;
        if (fz > 0 && reviewPagerState.fuzzy.dialogOpen) {
          await loadReviewPage("fuzzy", reviewPagerState.fuzzy.page);
        }
      }
    } catch {
      /* ignore */
    }
  };
  es.onerror = () => {
    toast("Live stream interrupted — reconnecting when possible.");
  };
}

window.addEventListener("DOMContentLoaded", async () => {
  uiBootstrapDone = true;
  wireButtons();
  startSse();
  startStatusPolling();
  try {
    await fetch("/api/device", { cache: "no-store" });
    const snap = await fetchStatusSnapshot();
    if (snap) {
      if (typeof snap.status_poll_interval_ms === "number" && snap.status_poll_interval_ms > 0) {
        window.__APP_POLL_MS__ = snap.status_poll_interval_ms;
      }
      applySnapshot(snap);
    } else {
      const msg =
        "Could not load session status — is scripts/run.sh still running? Start it from the repo root.";
      setBanner(msg, true);
      renderActivityHubMinimal(msg);
    }
  } catch {
    const msg = "Could not connect — start the app with scripts/run.sh, then reload this page.";
    setBanner(msg, true);
    renderActivityHubMinimal(msg);
  }
  try {
    await refreshAllScanSessions();
  } catch {
    /* scan session list is optional for the activity hub */
  }
});
