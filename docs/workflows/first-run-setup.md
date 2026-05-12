# Workflow: first-run setup (Mac + M1)

Goal: prepare the Mac so the web app can mount the iPhone and run duplicate detection.

## 1. Install system dependencies

- **macFUSE** — Required for FUSE on macOS; follow vendor docs for Apple Silicon and security settings ([../components/macfuse-prerequisites.md](../components/macfuse-prerequisites.md)).
- **libimobiledevice** — Homebrew or build from source; ensure `usbmuxd` is available/running.
- **ifuse** — Build or install alongside libimobiledevice stack.
- **dupeGuru** — Install GUI/CLI as you plan to integrate ([../components/duplicate-engine.md](../components/duplicate-engine.md)).

## 2. Apple Silicon kernel extension policy

If ifuse/macFUSE fails with permission or kext errors, complete **Recovery Mode** steps to allow the extension. Capture exact error strings in the app for diagnostics.

## 3. Smoke test (manual)

1. Connect iPhone via USB; unlock; tap **Trust**.
2. Run `ideviceinfo` — expect device properties.
3. Mount with ifuse to a test directory — expect to see **Media**-like folders.
4. Unmount cleanly — see [safe-unmount.md](./safe-unmount.md).

## 4. App configuration

- Create config directory and optional `secrets.local.json` per [../components/config-and-secrets.md](../components/config-and-secrets.md).

## Related

- [wired-cleanup-session.md](./wired-cleanup-session.md)
- [../reference/links-and-sources.md](../reference/links-and-sources.md)
