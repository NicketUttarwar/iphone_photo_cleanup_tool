# Best for Brett — Principles and decision lens

Use this file when choosing libraries, UX, and whether to add external services. It keeps the project aligned with: **local repository**, **M1 Mac**, **wired iPhone**, **interactive web UI**, and **safe asset cleanup**.

## Non-negotiables

1. **Local-first** — Primary data path is USB → libimobiledevice/ifuse → files on disk. No cloud is required for the core product.
2. **Explicit user consent** — Destructive actions (delete, bulk operations) require clear UI confirmation and an audit trail in the app (log or history panel).
3. **Unmount before disconnect** — Always surface “safe to unplug” only after a verified unmount path; see [workflows/safe-unmount.md](./workflows/safe-unmount.md).
4. **Honest limitations** — Document ghost thumbnails and the restart/re-index step; see [workflows/post-delete-restart.md](./workflows/post-delete-restart.md).

## When to use which tool

| Need | Prefer |
|------|--------|
| Device presence, identifiers, basic ops | **libimobiledevice** CLI or a thin wrapper ([components/libimobiledevice-bridge.md](./components/libimobiledevice-bridge.md)) |
| File-oriented browsing and deletion | **ifuse** mount ([components/ifuse-mount.md](./components/ifuse-mount.md)) |
| Duplicate groups / similarity | **dupeGuru** or equivalent logic ([components/duplicate-engine.md](./components/duplicate-engine.md)) |
| Rich selection and previews | **Web viewer** ([components/web-viewer.md](./components/web-viewer.md)) |
| Optional enrichment (metadata, cloud backup status) | **External APIs** only with keys from local config ([integration/optional-external-services.md](./integration/optional-external-services.md)) |

## “Best for Brett” vs scope creep

- Add an external service **only** if it removes real friction (e.g., multi-device search in a cloud you already use) and keys stay **local** — see [components/config-and-secrets.md](./components/config-and-secrets.md).
- Prefer **one orchestration story**: local runtime owns mount lifecycle and subprocess boundaries — [orchestration/local-runtime.md](./orchestration/local-runtime.md).

## Related docs

- [architecture/system-context.md](./architecture/system-context.md) — goals and constraints.
- [pipelines/README.md](./pipelines/README.md) — how stages compose.
- [orchestration/README.md](./orchestration/README.md) — how the web app coordinates work.
