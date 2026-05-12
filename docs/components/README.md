# Components — catalog

Components are **replaceable units** with clear inputs/outputs. Implement each as a module or service boundary in your web app (or as a thin adapter around a CLI).

| Component | Doc | Responsibility |
|-----------|-----|----------------|
| libimobiledevice bridge | [libimobiledevice-bridge.md](./libimobiledevice-bridge.md) | Device discovery, info, trust state |
| ifuse mount | [ifuse-mount.md](./ifuse-mount.md) | Mount/unmount Media tree as POSIX paths |
| macFUSE prerequisites | [macfuse-prerequisites.md](./macfuse-prerequisites.md) | Host readiness on Apple Silicon |
| Duplicate engine | [duplicate-engine.md](./duplicate-engine.md) | Duplicate / similarity grouping |
| Web viewer | [web-viewer.md](./web-viewer.md) | Interactive UI, selection, safety UX |
| Config and secrets | [config-and-secrets.md](./config-and-secrets.md) | Local API keys and paths |

## Composition

- **Workflows** chain components for humans: [../workflows/README.md](../workflows/README.md)
- **Pipelines** chain components for automation: [../pipelines/README.md](../pipelines/README.md)
- **Orchestration** schedules and supervises them: [../orchestration/README.md](../orchestration/README.md)

## Principles

See [../best-for-brett.md](../best-for-brett.md).
