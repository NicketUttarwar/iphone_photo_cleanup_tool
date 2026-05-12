# Pipeline: batch delete

**Goal:** Apply operator-approved deletions **only** under the mount root, with logging and safeguards.

## Stages

1. **Validate selection** — Every path is under `mountPath` prefix; reject `..` and symlinks escaping root if unsafe.
2. **Confirm** — UI already confirmed; server re-checks idempotency token or version of scan artifact.
3. **Execute** — Delete files (and empty dirs if desired); capture per-path result.
4. **Summarize** — Return counts: deleted, failed, skipped.
5. **Remind** — Surface post-delete restart hint ([../workflows/post-delete-restart.md](../workflows/post-delete-restart.md)).

## Inputs

- List of absolute paths (subset of scanned paths)
- `mountPath` for prefix validation

## Outputs

- Result ledger for UI and optional export

## Safety

- Rate-limit or chunk very large batches to keep FUSE stable.
- Never delete outside mount root ([../best-for-brett.md](../best-for-brett.md)).

## Related

- [scan-and-review-pipeline.md](./scan-and-review-pipeline.md)
- [../workflows/safe-unmount.md](../workflows/safe-unmount.md)
