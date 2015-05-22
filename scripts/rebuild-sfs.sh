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

sname="$(basename "$src" .sfs)"
sname="${sname#[0-9][0-9]-}"
cat <<EOF
DESTDIR=$wd/ALL

Apply your modifications and type 'exit 0' to build or 'exit 1' to cancel.
 Use 'cp2sfs </path/file/name>..' to include files from system to sfs.

EOF
keep_rebuilding=true
while $keep_rebuilding;do
  (
    cd "$wd/ALL"
    echo " . '$(dirname "$0")/common.func'; PS1='(rebuild: ${sname}.sfs) [\W]\\\$ '; exec <&1" |
      env DESTDIR="$wd/ALL" bash -i
  ) || {
    echo "Cancelled.." >&2
    exit 1
  }
  echo "Rebuilding to ${out:-$src}.."
  rebuild_sfs "$wd/ALL" "$src" "$out" && keep_rebuilding=false || echo "Rebuild failed, try again." >&2
done
