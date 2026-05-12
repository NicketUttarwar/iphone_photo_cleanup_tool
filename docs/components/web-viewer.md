# Component: web viewer (interactive UI)

## Purpose

Provide a **browser-based** experience for:

- Browsing scan results and previews.
- Selecting **keep vs delete** per duplicate group or per asset.
- Showing **mount status** and blocking deletes when not mounted.
- Guiding **safe unmount** before disconnect.

This matches the ReadMe goal: *“nice interactive web viewer for selection and safe unmounting.”*

## UX requirements

- **Dry-run or confirmation** for bulk delete.
- **Progress** for long scans and deletes (streaming logs or SSE).
- **Unmount CTA** always visible while mounted; disable “Done” until unmount succeeds.

## Technical notes

- Bind server to **127.0.0.1** by default.
- Never expose raw filesystem paths from other users’ home dirs; scope to mount root.

## Related

- [../workflows/safe-unmount.md](../workflows/safe-unmount.md)
- [../orchestration/local-runtime.md](../orchestration/local-runtime.md)
