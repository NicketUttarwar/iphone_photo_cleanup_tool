/* global fetch, EventSource, document, window, FormData */
let lastPhase = "";
let mountPath = "";

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

/** Drive the numbered guide, button disabled states, and the “Next” line from `/api/status` snapshots. */
function updateGuidedUI(s) {
  const trusted = Boolean(s.device && s.device.trusted);
  const hasMount = Boolean(s.mount_path);
  const phase = s.phase || "idle";
  const gc = typeof s.group_count === "number" ? s.group_count : 0;

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
    next =
      "Next: Step 1 — plug in USB, unlock the iPhone, tap Trust if asked, then press Check device now.";
  } else if (!hasMount) {
    next = "Next: Step 2 — press Mount iPhone media so this Mac can read your photo library.";
  } else if (phase === "scanning") {
    next = "Duplicate scan running — watch Background work below. Use Cancel scan if you need to stop.";
  } else if (phase === "mounted") {
    next =
      "Next: Step 3 — start a duplicate scan (optional), or skip to Steps 4–5. When finished, Step 6 — Unmount before unplugging USB.";
  } else if (phase === "reviewing") {
    if (gc > 0) {
      next =
        "Next: Step 4 — scroll to Duplicate review, tap one keeper per group, then Delete non-kept duplicates when you are sure.";
    } else {
      next =
        "This scan found no duplicate groups. You can use Step 5 (documents) or go straight to Step 6 when you are done.";
    }
  } else if (phase === "deleting") {
    next = "Deletion in progress — wait for Background work to finish before unmounting.";
  } else if (phase === "unmounting") {
    next = "Unmount in progress…";
  } else if (hasMount) {
    next = "When your edits are finished: Step 6 — Unmount (safe), then unplug.";
  } else {
    next = "";
  }
  na.textContent = next;

  const canStartScan =
    hasMount && (phase === "mounted" || phase === "reviewing") && phase !== "scanning";
  const mountBusy = phase === "scanning" || phase === "deleting" || phase === "unmounting";
  document.getElementById("btnMount").disabled = !trusted || mountBusy;
  document.getElementById("btnScan").disabled = !canStartScan;
  document.getElementById("btnScanCancel").disabled = phase !== "scanning";

  const mountNeeded = !hasMount;
  ["btnDocPreview", "btnDocRemove", "btnDocUndo", "btnDocFinalize"].forEach((id) => {
    document.getElementById(id).disabled = mountNeeded;
  });
  document.getElementById("btnDelete").disabled = mountNeeded || gc === 0;
  document.getElementById("btnApplyAuto").disabled = mountNeeded || gc === 0;
  document.querySelectorAll('input[name="keepMode"]').forEach((r) => {
    r.disabled = mountNeeded;
  });

  const jumpOk = hasMount;
  document.getElementById("btnJumpDup").disabled = !jumpOk;
  document.getElementById("btnJumpDoc").disabled = !jumpOk;
}

function relPath(fullPath, mount) {
  if (!mount || !fullPath) return "";
  const m = mount.endsWith("/") ? mount.slice(0, -1) : mount;
  if (!fullPath.startsWith(m)) return "";
  const rest = fullPath.slice(m.length);
  return rest.startsWith("/") ? rest.slice(1) : rest;
}

function updateWorking(snapshot) {
  const box = document.getElementById("working");
  const txt = document.getElementById("workingText");
  const jobs = snapshot.jobs || [];
  const active = jobs.filter((j) => j.running);
  if (active.length === 0) {
    box.classList.add("hidden");
    txt.textContent = "Idle";
    return;
  }
  box.classList.remove("hidden");
  const parts = active.map((j) => `${j.label}: ${j.message || "…"}`);
  txt.textContent = parts.join(" · ");
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
  document.getElementById("phase").textContent = s.phase || "unknown";
  document.getElementById("keepMode").textContent = s.keep_mode || "—";
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
  if (s.keep_mode) {
    const r = document.querySelector(`input[name="keepMode"][value="${s.keep_mode}"]`);
    if (r) r.checked = true;
  }
  updateLastDelete(s.last_delete_ledger);
  updateDocumentBatchInfo(s);
  updateDocumentLedger(s);
  updateWorking(s);
  if (s.phase && s.phase !== lastPhase) {
    toast(`State: ${s.phase}`);
    lastPhase = s.phase;
  }
  updateGuidedUI(s);
}

async function loadGroups() {
  const res = await fetch("/api/scan/groups");
  if (!res.ok) return;
  const data = await res.json();
  const root = document.getElementById("groups");
  root.innerHTML = "";
  const groups = data.groups || [];
  const keep = data.keep || {};
  for (const g of groups) {
    const wrap = document.createElement("section");
    wrap.className = "group";
    const title = document.createElement("div");
    title.className = "group-title";
    title.textContent = `Group ${g.id} · save ~${Math.round((g.bytesSavedIfOneKept || 0) / 1024)} KB if one kept`;
    const tiles = document.createElement("div");
    tiles.className = "tiles";
    for (const p of g.paths || []) {
      const tile = document.createElement("div");
      tile.className = "tile";
      const rel = relPath(p, mountPath);
      const sel = keep[g.id] === p || (!keep[g.id] && g.recommendedKeep === p);
      if (sel) tile.classList.add("selected");
      const img = document.createElement("img");
      img.loading = "lazy";
      if (rel) {
        img.src = `/api/thumbnail?relpath=${encodeURIComponent(rel)}`;
      }
      img.alt = p.split("/").pop() || "photo";
      const cap = document.createElement("div");
      cap.className = "cap";
      cap.textContent = p.split("/").pop() || p;
      tile.appendChild(img);
      tile.appendChild(cap);
      tile.addEventListener("click", async () => {
        await fetch("/api/selection", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ group_id: g.id, keep_path: p }),
        });
        toast(`Keep selected for group ${g.id}`);
        await loadGroups();
      });
      tiles.appendChild(tile);
    }
    wrap.appendChild(title);
    wrap.appendChild(tiles);
    root.appendChild(wrap);
  }
}

function wireButtons() {
  document.getElementById("btnJumpDup").addEventListener("click", () => {
    document.getElementById("dupReview").scrollIntoView({ behavior: "smooth", block: "start" });
  });
  document.getElementById("btnJumpDoc").addEventListener("click", () => {
    document.getElementById("docPanel").scrollIntoView({ behavior: "smooth", block: "start" });
  });

  document.getElementById("btnDevice").addEventListener("click", async () => {
    toast("Checking USB device…");
    const res = await fetch("/api/device");
    const j = await res.json();
    if (!res.ok) toast(`Device check failed: ${j.detail || res.status}`);
    else toast(j.trusted ? "Device trusted." : "Device not ready yet.");
  });
  document.getElementById("btnMount").addEventListener("click", async () => {
    toast("Mounting (may take a few seconds)…");
    const res = await fetch("/api/mount", { method: "POST" });
    const j = await res.json().catch(() => ({}));
    if (!res.ok) toast(`Mount failed: ${j.detail || res.status}`);
    else toast("Mounted.");
  });
  document.getElementById("btnUnmount").addEventListener("click", async () => {
    toast("Unmounting — close Finder windows pointing at the mount first.");
    const res = await fetch("/api/unmount", { method: "POST" });
    const j = await res.json().catch(() => ({}));
    if (!res.ok) toast(`Unmount failed: ${j.detail || res.status}`);
    else toast(j.ok ? "Unmount complete. Safe to unplug after this succeeds." : String(j.message || "Unmount issue"));
  });
  document.getElementById("btnScan").addEventListener("click", async () => {
    toast("Scan started in background — watch the status line.");
    const res = await fetch("/api/scan/start", { method: "POST" });
    const j = await res.json().catch(() => ({}));
    if (!res.ok) toast(`Scan rejected: ${j.detail || res.status}`);
  });
  document.getElementById("btnScanCancel").addEventListener("click", async () => {
    const res = await fetch("/api/scan/cancel", { method: "POST" });
    const j = await res.json().catch(() => ({}));
    if (!res.ok) toast(`Cancel: ${j.detail || res.status}`);
    else toast("Cancel requested — wait until the scan stops.");
  });
  document.querySelectorAll('input[name="keepMode"]').forEach((el) => {
    el.addEventListener("change", async (ev) => {
      const mode = ev.target.value;
      await fetch("/api/keep-mode", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode, apply_auto: false }),
      });
      toast(`Keep mode: ${mode}`);
      await loadGroups();
    });
  });
  document.getElementById("btnApplyAuto").addEventListener("click", async () => {
    const mode = document.querySelector('input[name="keepMode"]:checked').value;
    toast("Applying automatic keeper selection…");
    await fetch("/api/keep-mode", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode, apply_auto: true }),
    });
    await loadGroups();
    toast("Auto pick applied (see config for face/eye assist).");
  });
  const dlg = document.getElementById("dlgDelete");
  document.getElementById("btnDelete").addEventListener("click", () => dlg.showModal());
  document.getElementById("btnCancelDelete").addEventListener("click", () => dlg.close());
  document.getElementById("btnConfirmDelete").addEventListener("click", async (e) => {
    e.preventDefault();
    const phrase = document.getElementById("confirmInput").value.trim();
    const res = await fetch("/api/delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ paths: [], confirm: phrase }),
    });
    const j = await res.json().catch(() => ({}));
    if (!res.ok) toast(`Delete blocked: ${j.detail || res.status}`);
    else toast("Delete job started — watch background status.");
    dlg.close();
    document.getElementById("confirmInput").value = "";
  });

  const dlgDoc = document.getElementById("dlgDocuments");
  const dlgFin = document.getElementById("dlgDocFinalize");
  const preDoc = document.getElementById("docPreview");

  document.getElementById("btnDocPreview").addEventListener("click", async () => {
    const scopeEl = document.querySelector('input[name="docScope"]:checked');
    const scope = scopeEl ? scopeEl.value : "older_than_90d";
    const vf = document.getElementById("docVisualFallback").checked;
    const res = await fetch(
      `/api/documents/preview?scope=${encodeURIComponent(scope)}&include_visual_fallback=${vf ? "true" : "false"}`,
    );
    const j = await res.json().catch(() => ({}));
    if (!res.ok) {
      toast(`Preview failed: ${j.detail || res.status}`);
      preDoc.classList.add("hidden");
      return;
    }
    const lines = [`count=${j.count} scope=${j.scope} visual=${j.include_visual_fallback}`, ...(j.sample || [])];
    preDoc.textContent = lines.join("\n");
    preDoc.classList.remove("hidden");
    toast(`Preview: ${j.count} match(es).`);
  });
  document.getElementById("btnDocRemove").addEventListener("click", () => dlgDoc.showModal());
  document.getElementById("btnDocCancel").addEventListener("click", () => dlgDoc.close());
  document.getElementById("btnDocConfirm").addEventListener("click", async (e) => {
    e.preventDefault();
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
    else toast("Document removal started — watch background status.");
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
    else toast(`Undo: restored ${(j.restored || []).length} file(s).`);
  });
  document.getElementById("btnDocFinalize").addEventListener("click", () => dlgFin.showModal());
  document.getElementById("btnDocFinalizeCancel").addEventListener("click", () => dlgFin.close());
  document.getElementById("btnDocFinalizeConfirm").addEventListener("click", async (e) => {
    e.preventDefault();
    const all = document.getElementById("docFinalizeAll").checked;
    const bid = document.getElementById("docFinalizeBatchId").value.trim();
    if (!all && !bid) {
      toast("Enter a batch id or tick all batches.");
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
    else toast("Holding copies dropped.");
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
  toast("Use the numbered steps on this page from top to bottom.");
  await loadGroups();
});
