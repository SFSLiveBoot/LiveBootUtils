#!/bin/sh

. "$(dirname "$0")/common.func"

set -e

on_exit() {
  lsof +d "$wd" "$wd"/* || true
  unmount_below "$wd" && rmdir "$wd"/* "$wd"
}

src="$1"
out="$2"

test -r "$src" || {
  echo "Usage: $(basename "$0") <old.sfs> [<new.sfs>=$out]" >&2
  exit 1
}

wd="$(mktemp -d /tmp/rebuild.XXXXXX)"
trap on_exit EXIT

chmod 755 $wd

mount_combined "$wd" "$src"

echo "Apply your modifications and type 'exit 0' to build or 'exit 1' to cancel."
echo "DESTDIR=$wd/ALL"
if ( cd "$wd/ALL" ; env DESTDIR="$wd/ALL" bash; );then
  rebuild_sfs "$wd/ALL" "$src" "$ALL"
fi
