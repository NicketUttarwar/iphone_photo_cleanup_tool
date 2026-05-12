#!/usr/bin/env bash
# Verify host tools for USB + FUSE workflow. Prints hints only; exits 0 for soft check.
set -euo pipefail

REPO_ROOT=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo-root)
      REPO_ROOT="$2"
      shift 2
      ;;
    *)
      shift
      ;;
  esac
done

if [[ -z "$REPO_ROOT" ]]; then
  REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
fi

warn() { echo "[host-check] $*" >&2; }

missing_any=0
missing_idevice=0
missing_ifuse=0
missing_diskutil=0

for cmd in ideviceinfo idevice_id ifuse; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    warn "Missing command: $cmd (install libimobiledevice / ifuse stack, e.g. Homebrew)."
    missing_any=1
    case "$cmd" in
      ideviceinfo|idevice_id) missing_idevice=1 ;;
      ifuse) missing_ifuse=1 ;;
    esac
  fi
done

if [[ "$missing_any" -eq 1 ]]; then
  warn "See docs/workflows/first-run-setup.md and docs/components/macfuse-prerequisites.md."
  os="$(uname -s)"
  brew_parts=()
  if [[ "$missing_idevice" -eq 1 ]]; then
    brew_parts+=("brew install libimobiledevice")
  fi
  if [[ "$missing_ifuse" -eq 1 ]]; then
    case "$os" in
      Darwin)
        brew_parts+=("brew install --cask macfuse")
        warn "ifuse: on macOS, build from source after installing macFUSE; the Homebrew tap may not see macFUSE's fuse3 pkg-config file."
        warn "ifuse source build hint: brew install autoconf automake libtool pkgconf glib && git clone https://github.com/libimobiledevice/ifuse.git /tmp/ifuse && cd /tmp/ifuse && ./autogen.sh --prefix=/usr/local && make && sudo make install"
        ;;
      Linux)
        brew_parts+=("brew install ifuse")
        ;;
      *)
        warn "ifuse: Homebrew core formula targets Linux; on macOS use macFUSE + gromgit/fuse/ifuse-mac (see docs above)."
        ;;
    esac
  fi
  if [[ ${#brew_parts[@]} -gt 0 ]]; then
    joined="$(printf '%s && ' "${brew_parts[@]}")"
    joined="${joined% && }"
    warn "Suggested Homebrew install (run as one line):"
    warn "  $joined"
  fi
fi

if ! command -v diskutil >/dev/null 2>&1; then
  warn "diskutil not found (expected on macOS for unmount)."
  missing_diskutil=1
fi

if [[ "$missing_any" -eq 0 && "$missing_diskutil" -eq 0 ]]; then
  echo "[host-check] All required host commands found."
fi

exit 0
