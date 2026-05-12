# Component: ifuse mount

## Purpose

Use **ifuse** so the iPhone **Media** directory appears as a normal directory tree on macOS. The duplicate engine and web viewer operate on **paths under this mountpoint**.

Upstream: `libimobiledevice/ifuse` (see [../reference/links-and-sources.md](../reference/links-and-sources.md)).

## Responsibilities

- Create a dedicated mount directory (e.g. `~/Library/Application Support/<app>/iphone-mount` or `~/iPhoneMedia`).
- Invoke ifuse with correct arguments for **Media** (document exact flags in implementation README).
- Track **mount state** in the app: mounted / not mounted / stale (process died).
- Implement **idempotent mount**: if already mounted for same UDID, reuse or remount safely.

## Interface sketch

- `mount(udid) -> { mountPath, pidOrToken }`
- `status() -> { mounted, mountPath, udid }`
- `unmount() -> { ok, message }`

## Failure modes

- macFUSE not loaded → link to [macfuse-prerequisites.md](./macfuse-prerequisites.md).
- Permission denied on mountpoint.
- Device locked mid-session → reads may fail; UI should pause destructive actions.

## Related

- [../workflows/safe-unmount.md](../workflows/safe-unmount.md)
- [../pipelines/scan-and-review-pipeline.md](../pipelines/scan-and-review-pipeline.md)
