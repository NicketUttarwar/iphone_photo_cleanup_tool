# Component: config and secrets (local only)

## Purpose

Store **API keys and optional service configuration** on disk **only on the operator’s Mac**, loaded at runtime by the web app. Nothing secret belongs in git.

## Suggested layout

```text
<app-config-dir>/
  config.json           # non-secret defaults (theme, mount path template)
  secrets.local.json    # gitignored; API keys, tokens
```

## `secrets.local.json` shape (example)

```json
{
  "amazonPhotosApiKey": null,
  "someMetadataService": "sk-..."
}
```

The app should **merge** `config.json` with `secrets.local.json` and treat missing keys as “feature disabled.”

## Rules

- Add `secrets.local.json` to `.gitignore`.
- Document a `secrets.local.example.json` with **empty** values in the repo (optional, when you add code).
- Log **never** print full secrets.

## Related

- [../integration/optional-external-services.md](../integration/optional-external-services.md)
- [../best-for-brett.md](../best-for-brett.md)
