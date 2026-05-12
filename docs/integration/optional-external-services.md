# Integration: optional external services

This project’s **core** is **local**: USB, libimobiledevice, ifuse, dupeGuru, web UI on the Mac ([../architecture/system-context.md](../architecture/system-context.md)).

Optional integrations can still help if you want **extra context** (for example, which assets already exist in a cloud library, or device-specific search in a vendor app). Those require **API keys or tokens** that you obtain from each provider.

## Local secret storage

- Store keys in **`secrets.local.json`** (or equivalent) on disk — see [../components/config-and-secrets.md](../components/config-and-secrets.md).
- The web app **reads at startup** or on demand; **never** commit secrets to git.

## Suggested integration boundaries

| Concern | Approach |
|---------|----------|
| Authentication | OAuth or API keys per vendor docs; refresh tokens stored locally only |
| Rate limits | Queue + backoff in orchestration ([../orchestration/optional-cloud-augmentation.md](../orchestration/optional-cloud-augmentation.md)) |
| Privacy | Fetch only identifiers you need; log redacted |

## Examples of services you might add later

- Cloud photo APIs (if available for your account type) for cross-checks.
- Generic HTTP metadata services (reverse geocode, not usually needed for dedupe).

Concrete providers and endpoints should be documented **when you implement** them; this file defines the **pattern** only.

## Related

- [../best-for-brett.md](../best-for-brett.md)
