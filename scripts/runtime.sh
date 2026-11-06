#!/bin/sh
# First install uses OS tools only. Later starts use the managed stable launcher.
set -eu
root=${ANDROID_USE_RUNTIME_DIR:-"$HOME/.local/share/android-use/runtime"}
if [ -f "$root/launcher.sh" ] && [ -f "$root/current.path" ]; then
  exec /bin/sh "$root/launcher.sh" auto-serve
fi
case "$(uname -s):$(uname -m)" in
  Darwin:arm64) target=macos-arm64;;
  Darwin:x86_64) target=macos-x86_64;;
  Linux:x86_64) target=linux-x86_64;;
  *) echo 'Unsupported Android Use runtime platform' >&2; exit 1;;
esac
base=https://github.com/arcships/android-use/releases/latest/download
name=android-use-runtime-$target.zip
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT HUP INT TERM
curl -fL --retry 2 --connect-timeout 15 --max-time 180 "$base/SHA256SUMS" -o "$work/SHA256SUMS" >&2
curl -fL --retry 2 --connect-timeout 15 --max-time 180 "$base/$name" -o "$work/$name" >&2
expected=$(awk -v file="$name" '$2==file {print $1}' "$work/SHA256SUMS")
case "$expected" in ''|*[!a-fA-F0-9]*) echo 'Invalid runtime checksum' >&2; exit 1;; esac
[ "${#expected}" -eq 64 ] || exit 1
if command -v sha256sum >/dev/null 2>&1; then
  actual=$(sha256sum "$work/$name" | awk '{print $1}')
else
  actual=$(shasum -a 256 "$work/$name" | awk '{print $1}')
fi
[ "$actual" = "$expected" ] || { echo 'Runtime checksum mismatch' >&2; exit 1; }
unzip -q "$work/$name" -d "$work/bundle"
"$work/bundle/app/android-use-runtime" --root "$root" install --archive "$work/$name" --sha256 "$expected" >&2
rm -rf "$work"
trap - EXIT HUP INT TERM
exec /bin/sh "$root/launcher.sh" auto-serve
