#!/bin/sh

. "$(dirname "$0")/common.func"

set -e

on_exit() {
  lsof +d "$wd" "$wd"/* || true
  unmount_below "$wd" && rmdir "$wd"/* "$wd"
}

case "$1" in
 --relink) relink="yes"; shift;
esac

src="$1"
out="$2"

test -r "$src" || {
  echo "Usage: $(basename "$0") [--relink] <old.sfs> [<new.sfs>=$out]" >&2
  exit 1
}

test -n "$relink" -o ! -L "$src" || {
  case "$(readlink "$src")" in
    */*)
      echo "ERROR: ${src##*/} is pointing to file in different directory: $(readlink $src)" >&2
      echo "Use --relink option to rebuild anyway." >&2
      exit 1
    ;;
  esac
}

wd="$(mktemp -d /tmp/rebuild.XXXXXX)"
trap on_exit EXIT

chmod 755 $wd

mount_combined "$wd" "$src"

sname="$(basename "$src" .sfs)"
sname="${sname#[0-9][0-9]-}"

build_prompt="\\nRebuilding: \"${sname}.sfs\". Use 'exit 1' to cancel, 'exit 0' to save changes.\\n[\A][\W]\\\$ "
cat <<EOF
DESTDIR=$wd/ALL

Apply your modifications and type 'exit 0' to build or 'exit 1' to cancel.
 Use 'cp2sfs </full/path/to/file>..' to include files from system to sfs.

EOF
keep_rebuilding=true
while $keep_rebuilding;do
  (
    cd "$wd/ALL"
    echo ' . "$_cf"; PS1="$_PS1"; exec <&1' |
      env _cf="$(dirname "$0")/common.func" _PS1="$build_prompt" DESTDIR="$wd/ALL" bash -i
  ) || {
    echo "Cancelled.." >&2
    exit 1
  }
  echo "Rebuilding to ${out:-$src}.."
  rebuild_sfs "$wd/ALL" "$src" "$out" && keep_rebuilding=false || echo "Rebuild failed, try again." >&2
done
