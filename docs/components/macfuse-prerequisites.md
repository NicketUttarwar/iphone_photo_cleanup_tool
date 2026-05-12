# Component: macFUSE prerequisites

## Purpose

**macFUSE** is required on macOS for **ifuse** to work. This component is not code-only: it is **host configuration** the app must detect and explain.

Website: see [../reference/links-and-sources.md](../reference/links-and-sources.md).

## Apple Silicon note

Kernel extensions may require **Recovery Mode → Startup Security Utility → Reduced Security** to allow macFUSE. The web app should:

- Detect “mount failed” patterns that indicate kext policy.
- Link or inline short instructions; full steps belong in [../workflows/first-run-setup.md](../workflows/first-run-setup.md).

## Interface sketch

- `preflight() -> { macfuseInstalled, kextLikelyOk, hints[] }`

## Related

- [ifuse-mount.md](./ifuse-mount.md)
- [../workflows/first-run-setup.md](../workflows/first-run-setup.md)
