# Workflow: wired cleanup session

End-to-end usage: **Mac web app + USB iPhone + local tools**.

## Preconditions

- First-run setup complete ([first-run-setup.md](./first-run-setup.md)).
- iPhone unlocked; cable firm; Trust established ([../components/libimobiledevice-bridge.md](../components/libimobiledevice-bridge.md)).

## Steps

1. **Start web app** on localhost ([../orchestration/local-runtime.md](../orchestration/local-runtime.md)).
2. **Detect device** — UI shows name/UDID or prompts for trust.
3. **Mount** — App invokes ifuse; UI shows mount path and “live” indicator ([../components/ifuse-mount.md](../components/ifuse-mount.md)).
4. **Scan** — Run duplicate pipeline ([../pipelines/scan-and-review-pipeline.md](../pipelines/scan-and-review-pipeline.md)).
5. **Review** — Operator uses web viewer to pick deletes ([../components/web-viewer.md](../components/web-viewer.md)).
6. **Execute deletes** — Only within mount root; confirm bulk actions ([../pipelines/batch-delete-pipeline.md](../pipelines/batch-delete-pipeline.md)).
7. **Unmount** — Mandatory before unplug ([safe-unmount.md](./safe-unmount.md)).
8. **Post-delete** — If Photos shows ghosts, follow [post-delete-restart.md](./post-delete-restart.md).

## Optional cloud hints

If configured, fetch cross-device metadata only after local scan — [../integration/optional-external-services.md](../integration/optional-external-services.md).

## Related

- [../best-for-brett.md](../best-for-brett.md)
