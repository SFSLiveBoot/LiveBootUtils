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
if ( cd "$wd/ALL" ; echo ". \"\$_cf\"; PS1='($sname)[\W]\\\$ '; exec <&1" | env _cf="$(dirname "$0")/common.func" DESTDIR="$wd/ALL" bash -i );then
  rebuild_sfs "$wd/ALL" "$src" "$ALL"
fi
