iPhone Photo Cleanup Tool — ReadMe
==================================

**The unified project readme is now README.md** (screenshots, setup, kernel permissions, scripts).
This file is kept for historical notes and assumptions below.

See image_of_kernel_permission_step.png for kernel extension permissions (also in README.md).
See screenshot_of_app.png for what the app looks like.

CURRENT STATE OF THIS REPOSITORY (as of last update)
----------------------------------------------------
- The repo now includes a **runnable local web app** (FastAPI + browser UI) under **`src/iphone_cleanup/`**, launched via **`scripts/run.sh`**.
- **Configuration** lives under **`config/`** (see **`config/app.defaults.yaml`** and **`config/app.example.yaml`**). Copy the example to **`config/app.local.yaml`** for overrides (gitignored).
- **Host tools** are still **system-installed** (not from pip): **macFUSE**, **libimobiledevice** (`ideviceinfo`, `idevice_id`), **ifuse**. **`scripts/check_host_prerequisites.sh`** prints hints if binaries are missing.
- **Duplicate detection** is built in (perceptual hash + size bucketing). **dupeGuru** integration remains optional per **docs/**.
- **Optional face/eye assist** for auto “best” picks: install **`requirements-mediapipe.txt`** into the same venv, then set **`duplicates.auto_best.face_eye: true`** in **`config/app.local.yaml`**.
- What **is** also present unchanged:
  - This **ReadMe.txt** — intent, stack notes, assumptions you can challenge.
  - **docs/** — architecture and workflows. Start at **docs/README.md**.
  - **multi-phone-one-acct-strategy.txt** — optional cloud/multi-device context.


RUN (LOCAL)
-----------
1. Install macFUSE, libimobiledevice, and ifuse on the Mac (see **docs/workflows/first-run-setup.md**). On **Apple Silicon**, macFUSE may require a **one-time Recovery-mode** kernel-extension enablement and a **reboot** — see **APPLE SILICON: KERNEL EXTENSIONS** below.
2. (Optional) Copy **`config/app.example.yaml`** to **`config/app.local.yaml`** and edit bind port, paths, or tool paths.
3. From the repository root run **`./scripts/run.sh`** (full options and copy-paste examples are under **SCRIPTS (`scripts/`)** below). The app does not use environment variables for configuration.
4. A browser tab opens to **`http://127.0.0.1:<port>/`** by default (disable with **`--no-open-browser`** on **`run.sh`** or **`ui.open_browser: false`** in YAML).
5. **Logs** — structured lines go to **`data/logs/app.log`** (or the directory set by **`paths.logs_dir`** in YAML).


APPLE SILICON: KERNEL EXTENSIONS (macFUSE / FUSE)
-------------------------------------------------
**macFUSE** uses a **kernel extension** (kext). On **Mac with Apple silicon** (M1/M2/M3/M4 or later), Apple does **not** let you fully enable that from normal macOS alone the first time: you must boot into **macOS Recovery**, open **Utilities → Startup Security Utility**, select your startup volume, choose **Security Policy**, enable **Reduced Security**, check **Allow user management of kernel extensions from identified developers**, confirm, then **restart** from the Apple menu so the Mac boots back into regular macOS. After that reboot you may still need to approve the extension or app in **System Settings** per your installer’s prompts.

Step-by-step guide (print or open on another device before entering Recovery):
https://www.sweetwater.com/sweetcare/articles/kernel-extensions-on-mac-with-apple-silicon

**FYI for new machines or new macOS installs:** expect at least one **full reboot** after the Recovery change before FUSE/macFUSE-backed tools behave; if **`ifuse`** or mounting failed before you did this, finish Recovery + reboot + any System Settings approvals, then run **`./scripts/run.sh`** or **`scripts/check_host_prerequisites.sh`** again.


SCRIPTS (`scripts/`)
--------------------
Run these from anywhere; each script resolves the repo root unless you override it.

**`scripts/check_host_prerequisites.sh`** — Soft check for USB/FUSE host binaries (`ideviceinfo`, `idevice_id`, `ifuse`, `diskutil`). Always exits **0**; prints **`[host-check]`** warnings and suggested **`brew install ...`** lines when something is missing.

  ./scripts/check_host_prerequisites.sh

  ./scripts/check_host_prerequisites.sh --repo-root /path/to/iphone_photo_cleanup_tool

**`scripts/run.sh`** — Creates or reuses **`.venv/`** under the repo, installs **`requirements.txt`** and the package in editable mode, runs **`check_host_prerequisites.sh`** unless **`--skip-host-check`**, then starts **`python -m iphone_cleanup`**. Uses **`.venv/bin/python`**; you do not need to activate a venv first.

  ./scripts/run.sh

  ./scripts/run.sh --no-open-browser

  ./scripts/run.sh --skip-host-check

  ./scripts/run.sh --python /usr/local/bin/python3 --recreate-venv

  ./scripts/run.sh --dev

  ./scripts/run.sh --config /path/to/my-app.yaml

  ./scripts/run.sh --dev --config ./config/app.local.yaml --no-open-browser

**`run.sh` flags** (combine in any order): **`--python /path/to/python3`**, **`--recreate-venv`**, **`--skip-host-check`**, **`--dev`** (also installs **`requirements-dev.txt`** if present), **`--config /path/to.yaml`** (passed as **`--local-config`** to the app), **`--no-open-browser`**, **`--help`** / **`-h`**.


CONFIGURATION RULE
------------------
All tunables are read from **`config/*.yaml`** and CLI flags passed by **`scripts/run.sh`** / **`python -m iphone_cleanup`**. The application does **not** read process environment variables for settings.
Duplicate groups always start from **auto-ranked keepers** (sharpness / resolution / recency for exact dupes; fuzzy bursts favor approximate eye counts when MediaPipe is enabled via **`duplicates.auto_best.face_eye`**). Tap thumbnails to override keep marks before deleting extras.


GOAL (TARGET PRODUCT)
---------------------
Build a **web-based, interactive** app you run on a **MacBook Pro (Apple Silicon)**. With an **iPhone connected by USB cable**, use it to **review and clear photo (and related) assets** — duplicates and selections — with emphasis on **safe unmount** and clear operator guidance. Core path should stay **local** (Mac + cable + phone), not dependent on iTunes or Apple Photos as the primary interface.


PLANNED / EXTERNAL STACK (PARTIALLY SUPERSEDED BY CODE)
-------------------------------------------------------
1. **libimobiledevice** — still required on the host for USB detection and pairing.

2. **ifuse** — still required to mount iPhone media into **`paths.mount_point`** from config.

3. **macFUSE** — still required on macOS for ifuse.

4. **dupeGuru** — optional future integration; in-repo scanner covers v1 duplicate groups.

5. **Local web viewer** — **implemented** in this repo (interactive UI, thumbnails, scan/delete flows, unmount guidance).


NOTES (PRODUCT / iOS BEHAVIOR)
------------------------------
Deleting photos **directly via the ifuse-mounted filesystem** can leave **“ghost thumbnails”** in the iPhone’s native Photos app, because the file may be gone while the on-device Photos database has not been updated the way Apple’s own pipeline would. A **restart of the iPhone** often triggers re-indexing and clears the mismatch. **Include this as a visible step** in the final shipped readme and in-app help after bulk deletes.

Structured markdown for components, workflows, pipelines, and orchestration: **docs/README.md**


ASSUMPTIONS YOU CAN DEBUNK OR REPLACE (TO IMPROVE THE PLAN)
-----------------------------------------------------------
Use this list to deliberately break the design if reality disagrees.

**Hardware and OS**
- A1. Primary machine is **Apple Silicon** MacBook Pro; Intel Macs are out of scope or untested.
- A2. **macFUSE + ifuse** remain a viable path on your target macOS version (vs Apple locking down kexts further or offering a better supported API).
- A3. **USB cable** is the main connectivity; Wi‑Fi sync or cloud-only workflows are explicitly not the v1 core.

**What the mount actually exposes**
- A4. The **ifuse “Media”** view is **sufficient** for finding duplicates and safe deletion for your library (vs partial trees, different iOS versions, or permissions that hide what you care about).
- A5. **dupeGuru** (or the same algorithms) can be **invoked or integrated** in a way that is maintainable on Apple Silicon (GUI automation vs CLI vs alternate duplicate engine).

**Security and deployment**
- A6. Binding the web UI to **localhost** is enough for v1; no remote access, TLS to LAN, or multi-user auth is required initially.
- A7. Running subprocesses with the **interactive user’s privileges** is acceptable for mount/delete operations (vs hardened helper tool / SMJobBless).

**Product scope**
- A8. **File-level delete** from the mount is an acceptable tradeoff vs perfect consistency with the Photos app database (ghost-thumbnail risk accepted with documented mitigation).
- A9. **One phone / one mount** per app session is enough for v1 (no concurrent multi-device orchestration).
- A10. **Optional cloud APIs** (see docs/integration) stay optional; keys live only in local files, never in git.

**Operator model**
- A11. A **single human operator** runs the app and confirms destructive actions; no unattended batch deletes in v1.

If you invalidate an assumption, update **docs/** in the same change so architecture and workflows stay aligned.


OTHER FILES
-----------
- **multi-phone-one-acct-strategy.txt** — ideas for multi-phone / same-account cloud photo setups; optional reading, not core to the USB cleanup tool.
