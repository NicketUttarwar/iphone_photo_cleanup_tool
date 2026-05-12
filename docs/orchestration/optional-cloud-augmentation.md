# Orchestration: optional cloud augmentation

## Goal

Allow **optional** calls to external services (metadata, multi-device context, backup providers) **without** making them part of the critical path for local cleanup.

## Pattern

1. **Local scan completes** ([../pipelines/scan-and-review-pipeline.md](../pipelines/scan-and-review-pipeline.md)).
2. If `secrets.local.json` contains relevant keys ([../components/config-and-secrets.md](../components/config-and-secrets.md)), enqueue **augmentation tasks**.
3. Merge non-destructive labels into UI (e.g., “also appears in cloud album X”) — never auto-delete based on cloud alone unless explicitly designed and confirmed.

## Failure isolation

- Network failures **must not** block unmount or local deletes.
- Timeouts and circuit breakers per provider.

## Multi-phone family context

If you use cloud photo apps with one account across phones, **search/filter by device** in that vendor’s UI can complement local cleanup (see repo `multi-phone-one-acct-strategy.txt`). Treat as **operator documentation**, not a hard dependency.

## Related

- [../integration/optional-external-services.md](../integration/optional-external-services.md)
