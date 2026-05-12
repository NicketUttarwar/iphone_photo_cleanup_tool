# Workflow: post-delete restart (ghost thumbnails)

## Why this exists

Deleting photos **directly through the ifuse-mounted filesystem** can remove files **without updating** the iPhone Photos internal database. The native Photos app may then show **ghost thumbnails** or stale entries.

Per project ReadMe: **restarting the iPhone** usually triggers a **re-index** that clears the inconsistency.

## When to show this in the product

- After any **bulk delete** session, show a dismissible banner: *“If thumbnails look wrong in Photos, restart your iPhone once.”*
- Link from help / troubleshooting.

## Operator steps

1. Disconnect after [safe-unmount.md](./safe-unmount.md).
2. **Restart** the iPhone (power off/on or explicit restart).
3. Open Photos and confirm thumbnails match reality.

## Engineering note

This is a **limitation of file-level deletion**, not necessarily a bug in your app. Document honestly in UI and docs ([../best-for-brett.md](../best-for-brett.md)).
