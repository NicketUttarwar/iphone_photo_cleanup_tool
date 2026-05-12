# Component: duplicate engine

## Purpose

Identify duplicate or near-duplicate images/videos under the **mounted Media tree** so the operator can delete redundant copies safely.

Primary reference implementation: **dupeGuru** — `arsenetar/dupeguru` (see [../reference/links-and-sources.md](../reference/links-and-sources.md)).

## Integration options

1. **Subprocess** — Invoke dupeGuru in CLI/batch mode if available, or export its results format.
2. **Library port** — If you later embed duplicate logic in-process, keep the same **output schema** (groups of paths + confidence) so the UI does not change.

## Output contract (suggested)

```json
{
  "groups": [
    {
      "id": "g1",
      "paths": ["/mount/.../IMG_001.JPG", "/mount/.../IMG_001 (1).JPG"],
      "bytesSavedIfOneKept": 1234567,
      "recommendedKeep": "/mount/.../IMG_001.JPG"
    }
  ]
}
```

## Related pipelines

- [../pipelines/scan-and-review-pipeline.md](../pipelines/scan-and-review-pipeline.md)
- [../pipelines/batch-delete-pipeline.md](../pipelines/batch-delete-pipeline.md)
