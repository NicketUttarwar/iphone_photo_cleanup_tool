# Workflow: safe unmount

Unmounting is a **safety-critical** step. The web UI should treat “session complete” as **unmount succeeded**, not merely “deletes finished.”

## Operator-facing steps

1. Ensure **no other apps** are browsing the iPhone mount (Finder windows, terminals `cd`’d into mount).
2. In the web app, click **Unmount** (or **End session**).
3. Wait for **success** confirmation in the UI.
4. Only then disconnect the USB cable.

## App responsibilities

- Call the correct unmount path for your stack (`umount`, `diskutil unmount`, or ifuse-specific teardown — document in implementation).
- If unmount fails, show **actionable** errors (busy file, open handle).
- Optionally run a quick **lsof**-style check against mount root in dev builds only.

## Failure handling

- **Retry** once after short delay.
- If still failing, instruct user to close apps and retry; last resort: orderly app shutdown documentation (avoid orphan FUSE mounts).

## Related

- [../components/ifuse-mount.md](../components/ifuse-mount.md)
- [../components/web-viewer.md](../components/web-viewer.md)
- [wired-cleanup-session.md](./wired-cleanup-session.md)
