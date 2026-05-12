# iPhone Photo Cleanup Tool — Documentation Map

This folder is the **authoritative documentation tree** for building a **web-based, interactive app** you run on a **MacBook Pro (Apple Silicon)**, using a **USB-connected iPhone** to review and clear photo assets safely.

Start here, then follow links by role:

| If you want to… | Open |
|-----------------|------|
| Set direction and tradeoffs (“what’s best here”) | [best-for-brett.md](./best-for-brett.md) |
| See how everything fits together | [architecture/system-context.md](./architecture/system-context.md) |
| Build or wire a specific capability | [components/README.md](./components/README.md) |
| Follow repeatable user or system sequences | [workflows/README.md](./workflows/README.md) |
| Chain steps into automated or semi-automated flows | [pipelines/README.md](./pipelines/README.md) |
| Coordinate processes, services, and the web UI | [orchestration/README.md](./orchestration/README.md) |
| Add optional cloud/API helpers and local secrets | [integration/optional-external-services.md](./integration/optional-external-services.md) |
| Quick links to upstream projects | [reference/links-and-sources.md](./reference/links-and-sources.md) |

## How these files work together

1. **Principles** ([best-for-brett.md](./best-for-brett.md)) constrain *what* you build.
2. **Architecture** ([architecture/](./architecture/)) describes *where* data and control flow.
3. **Components** ([components/](./components/)) are the implementable units (mount, scan, UI, secrets).
4. **Workflows** ([workflows/](./workflows/)) are human- and system-level procedures.
5. **Pipelines** ([pipelines/](./pipelines/)) are ordered stages built from components.
6. **Orchestration** ([orchestration/](./orchestration/)) is how the web app, subprocesses, and optional services run together over time.

## Document tree

```text
docs/
├── README.md                          ← you are here
├── best-for-brett.md
├── architecture/
│   ├── system-context.md
│   └── data-flow.md
├── components/
│   ├── README.md
│   ├── libimobiledevice-bridge.md
│   ├── ifuse-mount.md
│   ├── macfuse-prerequisites.md
│   ├── duplicate-engine.md
│   ├── web-viewer.md
│   └── config-and-secrets.md
├── workflows/
│   ├── README.md
│   ├── first-run-setup.md
│   ├── wired-cleanup-session.md
│   ├── safe-unmount.md
│   └── post-delete-restart.md
├── pipelines/
│   ├── README.md
│   ├── scan-and-review-pipeline.md
│   └── batch-delete-pipeline.md
├── orchestration/
│   ├── README.md
│   ├── local-runtime.md
│   └── optional-cloud-augmentation.md
├── integration/
│   └── optional-external-services.md
└── reference/
    └── links-and-sources.md
```

## Core stack (from project ReadMe)

Local tooling expected on the Mac:

- **libimobiledevice** — USB communication and iOS protocols without iTunes/Photos.
- **ifuse** — FUSE mount of the device **Media** tree so paths look like normal files.
- **macFUSE** — Required on macOS for ifuse; Apple Silicon may need **Reduced Security** for the kernel extension.
- **dupeGuru** — Duplicate detection/removal logic you can drive or integrate with.

The **web app** sits on top: interactive selection, previews, and **safe unmount** orchestration.

## Known iOS caveat

Deleting files via the mount can leave **ghost thumbnails** until the phone re-indexes; a **restart** is the documented fix — see [workflows/post-delete-restart.md](./workflows/post-delete-restart.md).
