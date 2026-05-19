# User scan workspace

Saved duplicate-scan results for this app live here. The folder is **gitignored** at runtime (your scans stay on this Mac only). The README and `examples/` files are tracked in git so you can see the layout.

## Layout

```
user_scans/
  active.json              # which session is selected in the UI (created at runtime)
  sessions/
    <session_id>/
      session.json         # metadata (label, timestamps, scan kind, counts)
      results.json         # duplicate groups payload (same shape as data/scans artifacts)
```

## Session id

`<session_id>` looks like `20260518_143022_exact_a1b2c3d4` (timestamp, scan kind, short random suffix). Newest sessions sort first by `created_at` in `session.json`.

## Files

| File | Purpose |
|------|---------|
| `active.json` | `{"session_id": "<session_id>"}` — UI default selection; falls back to newest if missing or invalid |
| `session.json` | Human label, `scan_kind` (`exact` or `fuzzy`), group count, mount hints |
| `results.json` | `{"scan_kind": "...", "groups": [...]}` — consumed by duplicate review |

See `examples/` for sample JSON.
