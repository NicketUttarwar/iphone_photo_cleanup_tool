# Pipelines — index

**Pipelines** are **ordered stages** inside the application, built from [../components/README.md](../components/README.md). They are more rigid than workflows: each stage has defined inputs/outputs suitable for automation, retries, and logging.

| Pipeline | Doc |
|----------|-----|
| Scan and review | [scan-and-review-pipeline.md](./scan-and-review-pipeline.md) |
| Batch delete | [batch-delete-pipeline.md](./batch-delete-pipeline.md) |

## Orchestration

Multiple pipelines may run serially or be triggered by events; see [../orchestration/README.md](../orchestration/README.md).

## Principles

[../best-for-brett.md](../best-for-brett.md)
