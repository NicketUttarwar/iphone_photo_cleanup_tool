/* global fetch, EventSource, document, window */
let lastPhase = "";
let mountPath = "";
let lastDocumentBatches = [];
let docPreviewDebounceTimer = null;
let lastDocPreviewKey = "";

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

/** Snapshot of session fields so the operator always sees what the server thinks is true. */
function renderActivityDiagnostics(s) {
  const el = document.getElementById("activityDiagnostics");
  if (!el) return;
  const fz =
    typeof s.fuzzy_roll_total === "number"
      ? `${s.fuzzy_roll_next_start ?? 0}/${s.fuzzy_roll_total}`
      : "n/a";
  const lines = [
    `BROWSER_NOW_UTC ${new Date().toISOString()}`,
    `PHASE ${s.phase || "—"} | scan_cancel_pending=${Boolean(s.scan_cancel_pending)} | duplicate_groups=${s.group_count ?? "—"}`,
    `MOUNT mount_path=${s.mount_path || "—"}`,
    `MOUNT mount_udid=${s.mount_udid || "—"}`,
    `FUZZY_ROLL next/total=${fz} | exhausted=${Boolean(s.fuzzy_roll_exhausted)} | batch_size=${s.fuzzy_roll_batch_size ?? "—"}`,
    `SCAN_ARTIFACT ${s.scan_artifact_path || "—"}`,
  ];
  if (s.last_error) lines.push(`LAST_ERROR ${s.last_error}`);
  const jobs = s.jobs || [];
  if (jobs.length) {
    lines.push(`JOBS (${jobs.length})`);
    for (const j of jobs) {
      const run = j.running ? "RUNNING" : "idle";
      lines.push(
        `  - ${run} id=${j.job_id} kind=${j.kind} | ${j.label || ""} | msg=${j.message || ""} | ` +
          `prog=${j.progress_current ?? "—"}/${j.progress_total ?? "—"} | started=${fmtEpochSeconds(j.started_at)} | finished=${fmtEpochSeconds(j.finished_at)}`,
      );
    }
  } else {
    lines.push("JOBS none in snapshot");
  }
  el.textContent = lines.join("\n");
}

/** Renders per-job progress bars and the rolling activity log from `/api/status` / SSE. */
function renderActivityHub(s) {
  const jobsEl = document.getElementById("activityJobs");
  const logEl = document.getElementById("activityLog");
  if (!jobsEl || !logEl) return;
  const jobs = (s.jobs || []).filter((j) => j.running);
  jobsEl.innerHTML = "";
  if (jobs.length === 0) {
    const idle = document.createElement("p");
    idle.className = "muted activity-idle-msg";
    idle.style.margin = "0";
    idle.style.fontSize = "0.88rem";
    idle.textContent =
      "No background job is running right now. When you mount, scan, delete, or move documents, a labeled job " +
      "appears here with a progress bar and a detailed status line. The log below records every step with timestamps.";
    jobsEl.appendChild(idle);
  } else {
    for (const j of jobs) {
      const wrap = document.createElement("div");
      wrap.className = "activity-job";
      const title = document.createElement("div");
      title.className = "activity-job-label";
      title.textContent = `${j.label || j.kind || "Working…"} (${j.kind || "job"})`;
      const barHost = document.createElement("div");
      barHost.className = "progress-host";
      const inner = document.createElement("div");
      inner.className = "progress-bar-inner";
      const tc = j.progress_total;
      const cur = j.progress_current;
      if (typeof tc === "number" && tc > 0 && typeof cur === "number") {
        const pct = Math.min(100, Math.max(0, (100 * cur) / tc));
        inner.style.width = `${pct}%`;
      } else {
        inner.classList.add("indeterminate");
      }
      barHost.appendChild(inner);
      const row = document.createElement("div");
      row.className = "activity-job-msg";
      row.textContent = j.message || "…";
      const det = document.createElement("div");
      det.className = "activity-job-details";
      const pctStr =
        typeof tc === "number" && tc > 0 && typeof cur === "number"
          ? `${Math.min(100, Math.max(0, (100 * cur) / tc)).toFixed(1)}%`
          : "n/a (no numeric progress yet)";
      det.textContent = `job_id=${j.job_id || "—"} | kind=${j.kind || "—"} | started_utc=${fmtEpochSeconds(j.started_at)} | progress_count=${cur ?? "—"}/${tc ?? "—"} (${pctStr})`;
      wrap.appendChild(title);
      wrap.appendChild(barHost);
      wrap.appendChild(row);
      wrap.appendChild(det);
      jobsEl.appendChild(wrap);
    }
  }
  const lines = s.activity_log || [];
  logEl.textContent = lines.length
    ? lines.join("\n")
    : "No log lines yet — they appear here as the server reports each step (mount, scan, delete, documents).";
  logEl.scrollTop = logEl.scrollHeight;
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

/** @returns {{ ok: boolean, count: number }} */
async function fillDocPreviewInto(summaryEl, stripEl, opts) {
  const { scope, vf } = opts;
  clearThumbStrip(stripEl);
  const res = await fetch(
    `/api/documents/preview?scope=${encodeURIComponent(scope)}&include_visual_fallback=${vf ? "true" : "false"}`,
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
  return { ok: true, count: n };
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
  summaryEl.textContent = "Loading matches…";
  await fillDocPreviewInto(summaryEl, stripEl, { scope, vf });
}

async function loadDeletePreviewDialog() {
  const summary = document.getElementById("deletePreviewSummary");
  const strip = document.getElementById("deletePreviewStrip");
  const btnGo = document.getElementById("btnConfirmDelete");
  clearThumbStrip(strip);
  if (btnGo) btnGo.disabled = true;
  const res = await fetch("/api/delete/preview");
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
    summary.innerHTML = `About to remove <strong>${n}</strong> image file(s), freeing roughly <strong>${formatHumanBytes(bytes)}</strong>, across <strong>${gw}</strong> duplicate group(s) that still have extras (of <strong>${dg}</strong> group(s) total).`;
  }
  fillMountThumbStrip(
    strip,
    (j.thumbnail_samples || []).map((x) => x.relpath).filter(Boolean),
  );
  if (btnGo) btnGo.disabled = n === 0;
}

async function loadDocRemoveDialogPreview() {
  const scopeEl = document.querySelector('input[name="docScope"]:checked');
  const scope = scopeEl ? scopeEl.value : "older_than_90d";
  const vf = document.getElementById("docVisualFallback").checked;
  const summary = document.getElementById("docDlgPreviewSummary");
  const strip = document.getElementById("docDlgPreviewStrip");
  const btnGo = document.getElementById("btnDocConfirm");
  if (btnGo) btnGo.disabled = true;
  const r = await fillDocPreviewInto(summary, strip, { scope, vf });
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

function updateDupReviewTeaser(s) {
  const el = document.getElementById("dupReviewTeaser");
  if (!el) return;
  const gc = typeof s.group_count === "number" ? s.group_count : 0;
  const phase = s.phase || "";
  const hasMount = Boolean(s.mount_path);
  if (!hasMount) {
    el.textContent = "Mount the iPhone to run a duplicate scan and open the review panel.";
  } else if (phase === "scanning") {
    el.textContent =
      "Scan in progress — watch the live progress strip at the top for the current file; you can Cancel in step 3 or Unmount in step 6.";
  } else if (gc === 0) {
    el.textContent =
      "No duplicate groups loaded yet. Use step 3 to scan (optional), or go straight to document cleanup in step 5.";
  } else {
    el.textContent = `${gc} duplicate group(s). Open the panel — keep marks start from auto-ranked picks; tap tiles to adjust.`;
  }
}

/** Drive the numbered guide, button disabled states, and the “Next” line from `/api/status` snapshots. */
function updateGuidedUI(s) {
  const trusted = Boolean(s.device && s.device.trusted);
  const hasMount = Boolean(s.mount_path);
  const phase = s.phase || "idle";
  const gc = typeof s.group_count === "number" ? s.group_count : 0;
  const cancelPending = Boolean(s.scan_cancel_pending);

  let current = 1;
  if (!trusted) {
    current = 1;
  } else if (!hasMount) {
    current = 2;
  } else if (phase === "scanning") {
    current = 3;
  } else if (phase === "mounted") {
    current = 3;
  } else if (phase === "reviewing" || phase === "deleting") {
    current = 4;
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
  let next = "";
  if (!trusted) {
    next = "Connect USB, unlock the iPhone, tap Trust if asked, then check the device.";
  } else if (!hasMount) {
    next =
      phase === "mounting"
        ? "Mounting — watch the live progress strip and activity log at the top of the page."
        : "Mount iPhone media so this Mac can read your library.";
  } else if (phase === "scanning") {
    next = cancelPending
      ? "Stop requested — the scan exits quickly between photos; when the phase shows Mounted or Reviewing again, you can start another scan, use document cleanup, or unmount."
      : "Scan running — live file-by-file progress is at the top. Use Cancel scan in step 3 to stop, or Unmount in step 6 to disconnect (that also cancels the scan).";
  } else if (phase === "mounted") {
    next =
      "Optional: run an exact duplicate scan or a fuzzy roll scan, or scroll down for document cleanup. Unmount before unplugging.";
  } else if (phase === "reviewing") {
    if (gc > 0) {
      next =
        "Open duplicate review: keep marks start from auto-ranked picks; tap tiles to change them (multiple per group). Delete only removes unmarked files. If every file in a group is kept, that group is skipped.";
    } else {
      next = "No groups from the last scan — document cleanup below is optional, then unmount when done.";
    }
  } else if (phase === "deleting") {
    next =
      "Deletion running — watch the live progress strip at the top. Unmount in step 6 stays available if you need to disconnect (close Finder windows on the iPhone volume first; if the volume is busy, wait a moment and retry).";
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
  document.getElementById("btnMount").disabled = !trusted || mountBusy;
  document.getElementById("btnScan").disabled = !canStartScan;
  const fuzzyBtn = document.getElementById("btnScanFuzzy");
  if (fuzzyBtn) fuzzyBtn.disabled = !canStartScan;
  const scanCancelBtn = document.getElementById("btnScanCancel");
  if (scanCancelBtn) {
    scanCancelBtn.disabled = phase !== "scanning";
    scanCancelBtn.textContent = cancelPending ? "Stopping…" : "Cancel scan";
  }

  const scanRunHint = document.getElementById("scanRunHint");
  if (scanRunHint) {
    if (phase === "mounting") {
      scanRunHint.textContent =
        "Mount in progress — duplicate scans stay disabled until the phase shows Mounted.";
    } else if (phase === "scanning") {
      scanRunHint.textContent = cancelPending
        ? "Stop requested — the worker checks often between photos and filesystem steps, so this usually clears in a moment."
        : "Only one scan runs at a time (exact or fuzzy). Starting the other mode cancels the current run; Cancel stops without starting another.";
    } else if (phase === "mounted" || phase === "reviewing") {
      const bs = typeof s.fuzzy_roll_batch_size === "number" ? s.fuzzy_roll_batch_size : 1000;
      const fzTotal = typeof s.fuzzy_roll_total === "number" ? s.fuzzy_roll_total : null;
      const fzNext = typeof s.fuzzy_roll_next_start === "number" ? s.fuzzy_roll_next_start : 0;
      const fzEx = Boolean(s.fuzzy_roll_exhausted);
      let fuzzyExtra = "";
      if (fzTotal != null && fzTotal > 0) {
        fuzzyExtra = fzEx
          ? ` Fuzzy: finished all ${fzTotal} indexed photos in slices of ~${bs}; use API ?kind=fuzzy&fuzzy_restart=true to clear fuzzy groups and start over from the top (reuses cached hashes).`
          : ` Fuzzy: next batch starts at photo index ${fzNext} of ${fzTotal} (~${bs} per run).`;
      }
      scanRunHint.textContent =
        `Exact = same-size near-identical dupes. Fuzzy roll = similar adjacent shots in capture-time order, ~${bs} photos hashed/analyzed per click; hashes persist between runs.` +
        fuzzyExtra;
    } else {
      scanRunHint.textContent = "";
    }
  }

  const dupOpen = document.getElementById("btnDupReviewOpen");
  if (dupOpen) {
    dupOpen.disabled =
      !hasMount || phase === "deleting" || phase === "unmounting" || phase === "mounting";
  }

  const mountNeeded = !hasMount;
  const docFs = document.getElementById("docScopeFieldset");
  if (docFs) docFs.disabled = mountNeeded;

  ["btnDocRemove", "btnDocUndo", "btnDocFinalize"].forEach((id) => {
    document.getElementById(id).disabled = mountNeeded;
  });
  document.getElementById("btnDelete").disabled =
    mountNeeded || gc === 0 || phase === "scanning" || phase === "deleting" || phase === "mounting";
}

function relPath(fullPath, mount) {
  if (!mount || !fullPath) return "";
  const m = mount.endsWith("/") ? mount.slice(0, -1) : mount;
  if (!fullPath.startsWith(m)) return "";
  const rest = fullPath.slice(m.length);
  return rest.startsWith("/") ? rest.slice(1) : rest;
}

function updateLastDelete(ledger) {
  const panel = document.getElementById("lastDeletePanel");
  const body = document.getElementById("lastDeleteBody");
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

function applySnapshot(s) {
  const mp = s.mount_path || "";
  if (mp !== lastMountForDoc) {
    lastMountForDoc = mp;
    lastDocPreviewKey = "";
  }
  lastDocumentBatches = s.document_batches || [];

  document.getElementById("phase").textContent = s.phase || "unknown";
  mountPath = s.mount_path || "";
  document.getElementById("mount").textContent = mountPath || "—";
  const dev = s.device;
  if (dev && dev.trusted) {
    document.getElementById("device").textContent = `${dev.name || "iPhone"} (${dev.udid || ""})`;
  } else if (dev && dev.error) {
    document.getElementById("device").textContent = dev.error;
  } else {
    document.getElementById("device").textContent = "Not connected / not trusted";
  }
  if (s.last_error) {
    setBanner(s.last_error, true);
  } else {
    setBanner("", false);
  }
  updateLastDelete(s.last_delete_ledger);
  updateDocumentBatchInfo(s);
  updateDocumentLedger(s);
  renderActivityHub(s);
  renderActivityDiagnostics(s);
  updateDupReviewTeaser(s);
  if (s.phase && s.phase !== lastPhase) {
    if (s.phase === "mounted") toast("iPhone media mounted.");
    else if (s.phase === "reviewing" && lastPhase === "scanning") toast("Duplicate scan finished.");
    lastPhase = s.phase;
  }
  updateGuidedUI(s);

  const phase = s.phase || "";
  if (mp && phase !== "deleting" && phase !== "unmounting" && phase !== "mounting") {
    scheduleDocPanelPreview();
  }
}

async function loadGroups() {
  const res = await fetch("/api/scan/groups");
  if (!res.ok) return;
  const data = await res.json();
  const root = document.getElementById("groups");
  if (!root) return;
  root.innerHTML = "";
  const groups = data.groups || [];
  const keep = data.keep || {};
  for (const g of groups) {
    const wrap = document.createElement("section");
    wrap.className = "group";
    const paths = g.paths || [];
    const rawK = keep[g.id];
    const keepList = Array.isArray(rawK) ? rawK : rawK ? [rawK] : [];
    const keepSet = new Set(keepList);
    const nk = keepSet.size;
    const sk = g.scan_kind === "fuzzy" ? "Fuzzy burst · " : "";
    const suffix =
      nk === paths.length && paths.length > 0 ? " — all marked keep (no deletes here)" : "";

    const header = document.createElement("div");
    header.className = "group-header";
    const titleText = document.createElement("div");
    titleText.className = "group-title-text";
    titleText.textContent = `${sk}Group ${g.id} · ${nk}/${paths.length} marked keep · save ~${Math.round((g.bytesSavedIfOneKept || 0) / 1024)} KB if only largest kept${suffix}`;
    const btnKeepAll = document.createElement("button");
    btnKeepAll.type = "button";
    btnKeepAll.className = "ghost keep-all-in-group";
    btnKeepAll.textContent = "Keep all in group";
    btnKeepAll.title =
      "Mark every image in this group as keep. You can still click tiles below to remove keep from individual photos.";
    btnKeepAll.setAttribute("aria-label", `Keep all ${paths.length} images in group ${g.id}`);
    btnKeepAll.disabled = paths.length === 0;
    btnKeepAll.addEventListener("click", async (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      if (!paths.length) return;
      await fetch("/api/selection", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ group_id: g.id, keep_paths: [...paths] }),
      });
      await loadGroups();
    });
    header.appendChild(titleText);
    header.appendChild(btnKeepAll);

    const tiles = document.createElement("div");
    tiles.className = "tiles";
    for (const p of paths) {
      const tile = document.createElement("div");
      tile.className = "tile";
      const rel = relPath(p, mountPath);
      const sel = keepSet.has(p);
      if (sel) tile.classList.add("selected");
      const mark = document.createElement("span");
      mark.className = "tile-keep-mark";
      mark.setAttribute("aria-hidden", "true");
      mark.textContent = sel ? "✓" : "";
      const img = document.createElement("img");
      img.loading = "lazy";
      if (rel) {
        img.src = `/api/thumbnail?relpath=${encodeURIComponent(rel)}&max_edge=512`;
      }
      img.alt = p.split("/").pop() || "photo";
      const cap = document.createElement("div");
      cap.className = "cap";
      cap.textContent = p.split("/").pop() || p;
      tile.appendChild(mark);
      tile.appendChild(img);
      tile.appendChild(cap);
      tile.addEventListener("click", async () => {
        await fetch("/api/selection", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ group_id: g.id, toggle_path: p }),
        });
        await loadGroups();
      });
      tiles.appendChild(tile);
    }
    wrap.appendChild(header);
    wrap.appendChild(tiles);
    root.appendChild(wrap);
  }
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

async function refreshStatusFromServer() {
  try {
    const st = await fetch("/api/status");
    if (st.ok) applySnapshot(await st.json());
  } catch {
    /* ignore */
  }
}

function wireButtons() {
  wireStepJumps();

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
    toast("Unmounting — close Finder windows pointing at the mount first.");
    const res = await fetch("/api/unmount", { method: "POST" });
    const j = await res.json().catch(() => ({}));
    if (!res.ok) toast(`Unmount failed: ${j.detail || res.status}`);
    else toast(j.ok ? "Unmount complete. Safe to unplug after this succeeds." : String(j.message || "Unmount issue"));
    await refreshStatusFromServer();
  });
  async function postScanStart(kind) {
    scrollToLiveProgress();
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
  }
  document.getElementById("btnScan").addEventListener("click", () => postScanStart("exact"));
  document.getElementById("btnScanFuzzy")?.addEventListener("click", () => postScanStart("fuzzy"));
  document.getElementById("btnScanCancel").addEventListener("click", async () => {
    const res = await fetch("/api/scan/cancel", { method: "POST" });
    const j = await res.json().catch(() => ({}));
    if (!res.ok) {
      toast(`Cancel: ${typeof j.detail === "string" ? j.detail : res.status}`);
      return;
    }
    if (j.noop) toast(j.message || "No scan was running.");
    else toast(j.message || "Stop requested.");
  });
  const dlgDup = document.getElementById("dlgDupReview");
  const openDupPanel = () => {
    if (dlgDup) dlgDup.showModal();
  };
  const closeDupPanel = () => {
    if (dlgDup && dlgDup.open) dlgDup.close();
  };
  document.getElementById("btnDupReviewOpen")?.addEventListener("click", () => openDupPanel());
  document.getElementById("btnDupReviewClose")?.addEventListener("click", () => closeDupPanel());
  document.getElementById("btnDupReviewCloseFooter")?.addEventListener("click", () => closeDupPanel());

  const dlg = document.getElementById("dlgDelete");
  document.getElementById("btnDelete").addEventListener("click", async () => {
    await loadDeletePreviewDialog();
    dlg.showModal();
  });
  document.getElementById("btnCancelDelete").addEventListener("click", () => dlg.close());
  document.getElementById("btnConfirmDelete").addEventListener("click", async (e) => {
    e.preventDefault();
    scrollToLiveProgress();
    const phrase = document.getElementById("confirmInput").value.trim();
    const res = await fetch("/api/delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ paths: [], confirm: phrase }),
    });
    const j = await res.json().catch(() => ({}));
    if (!res.ok) toast(`Delete blocked: ${j.detail || res.status}`);
    else toast("Deleting — watch live progress at the top.");
    dlg.close();
    document.getElementById("confirmInput").value = "";
  });

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
  });
  document.getElementById("btnDocUndo").addEventListener("click", async () => {
    const res = await fetch("/api/documents/undo", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    const j = await res.json().catch(() => ({}));
    if (!res.ok) toast(`Undo failed: ${j.detail || res.status}`);
    else toast(`Restored ${(j.restored || []).length} file(s) to the phone.`);
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
  });
}

function startSse() {
  const es = new EventSource("/api/events");
  es.onmessage = async (ev) => {
    try {
      const s = JSON.parse(ev.data);
      applySnapshot(s);
      if (s.phase === "reviewing") {
        await loadGroups();
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
  wireButtons();
  startSse();
  try {
    await fetch("/api/device");
    const st = await fetch("/api/status");
    if (st.ok) {
      applySnapshot(await st.json());
    }
  } catch {
    /* ignore */
  }
  await loadGroups();
});
