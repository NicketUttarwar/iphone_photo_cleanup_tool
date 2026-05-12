# Component: libimobiledevice bridge

## Purpose

Wrap **libimobiledevice** capabilities so the web app can answer: *Is a trusted iPhone connected? Which UDID? What name?* without iTunes or Photos.

Upstream: `libimobiledevice/libimobiledevice` (see [../reference/links-and-sources.md](../reference/links-and-sources.md)).

## Capabilities to expose (suggested)

- List paired devices / detect single active USB device.
- Run `ideviceinfo` (or library equivalent) for model, iOS version, serial/UDID as appropriate.
- Surface **trust** failures with actionable UI copy (“Unlock iPhone and tap Trust”).

## Interface sketch

- **Inputs:** none or optional UDID filter.
- **Outputs:** structured JSON: `{ udid, name, iosVersion, trusted: true/false }`.
- **Errors:** not connected, usbmuxd not running, trust pending.

## Dependencies

- `usbmuxd` running on macOS (often via Homebrew services).
- User has accepted trust dialog on the phone.

## Related workflows

- [../workflows/wired-cleanup-session.md](../workflows/wired-cleanup-session.md)
- [../workflows/first-run-setup.md](../workflows/first-run-setup.md)
