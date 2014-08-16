#!/bin/sh

. "$(dirname "$0")/common.func"

set -e

on_exit() {
  lsof +d "$wd" "$wd"/* || true
  unmount_below "$wd" && rmdir "$wd"/* "$wd"
}

src="$1"
out="$2"
exclude_file="$(dirname "$0")/rootfs.exclude"

wd="$(mktemp -d /tmp/update-root.XXXXXX)"
trap on_exit EXIT
build="$wd/ALL"
chmod 755 "$wd"

mount_combined "$wd" "$src"

echo "Apply your modifications and type 'exit 0' to build or 'exit 1' to cancel." >&2

enter_chroot "$build" || { echo "Cancelled." >&2; exit 1; }

rebuild_sfs "$build" "$src" "$out" -noappend -wildcards -ef "$exclude_file"
