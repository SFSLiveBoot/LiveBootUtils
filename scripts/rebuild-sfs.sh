#!/bin/sh

. "$(dirname "$0")/common.func"

set -e

on_exit() {
  lsof +d "$wd" "$wd"/* || true
  test -z "$lxc_root" || {
    umount "$lxc_root/$DESTDIR" "$lxc_root" "$lxc_rw"
    rmdir "$lxc_root" "$lxc_rw"
  }
  test ! -e "$rebuild_sh" || rm "$rebuild_sh"
  unmount_below "$wd" && rmdir "$wd"/* "$wd"
}

usage() {
  cat <<EOF
Usage: ${0##*/} [<options>] <old.sfs> [<new.sfs>=$out]
Options:
  --relink:     replace sfs link even if it points to other directory
  --lxc:        build in clean lxc environment
EOF
}

cat_rebuild_sh() {
  cat <<EOF
. "${_cf:-/opt/LiveBootUtils/scripts/common.func}"
export DESTDIR="$DESTDIR" TERM="$TERM"
alias rebuild-finalize="exit 0"
alias rebuild-cancel="exit 1"
alias rebuild-reenter="exit 100"
cd "\$DESTDIR"
EOF
}

build_lxc_root() {
  lxc_root="$(mktemp -d /tmp/rebuild-lxc-root.$$.XXXXXX)"
  lxc_rw="$(mktemp -d /tmp/rebuild-lxc-rw.$$.XXXXXX)"
  echo "lxc_rw=$lxc_rw"
  mount -t tmpfs -o mode=0755 lxc-rw "$lxc_rw"
  LXC_ROOTFS_PATH="$lxc_root" LXC_ROOT_RW="$lxc_rw" /etc/lxc/mount-sfs.sh \
    "$(basename $(file2dev /bin/ls) | sed -e 's@\.sfs[.OLD0-9]*@@')" 15-settings 20-scripts 40-home \
    "$wd/$(basename "$src" .sfs)" "$wd/RW"
  rebuild_sh="/etc/profile.d/rebuild-$$.sh"
  cat_rebuild_sh >"$lxc_root$rebuild_sh"
  mkdir -p "$lxc_root/$DESTDIR"
  mount --bind "$DESTDIR" "$lxc_root/$DESTDIR"
}

while test -n "$1" -a -z "${1##--*}";do
  case "$1" in
    --relink) relink="yes"; shift;;
    --lxc) use_lxc="yes"; shift;;
    --help) usage; exit 0;;
    *) echo "Unknown option: '$1'" >&2; exit 1;;
  esac
done

src="$1"
out="$2"

test -r "$src" || { usage >&2; exit 1; }

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
DESTDIR="$wd/ALL"
sname="$(basename "$src" .sfs)"
sname="${sname#[0-9][0-9]-}"
build_prompt="\\nRebuilding: \"${sname}.sfs\". Use 'exit 1' to cancel, 'exit 0' to save changes.\\n[\A][\W]\\\$ "

if test -z "$use_lxc";then
  rebuild_sh="$(mktemp /tmp/rebuild-$$.XXXXXX.sh)"
  DESTDIR="$DESTDIR" _cf="$(dirname "$0")/common.func" cat_rebuild_sh >"$rebuild_sh"
else
  build_lxc_root
fi

run_shell() {
  if test -n "$use_lxc";then
    find "$lxc_rw" -depth \
      $(d="$lxc_rw" IFS=/;for x in $DESTDIR; do d="$d${x:+/$x}"; echo -not -path "$d";done) \
      $(d="$lxc_rw" IFS=/;for x in $rebuild_sh; do d="$d${x:+/$x}"; echo -not -path "$d";done) \
      -not -path "$lxc_rw/.wh..wh.????" \
      -delete
    mount -o remount "$lxc_root"
    echo "After adding files to \$DESTDIR, run: mount -o remount /"
    lxc-execute -n "rebuild-$sname" \
      -s lxc.utsname="rebuild-$sname" \
      -s lxc.rootfs="$lxc_root" \
      -s lxc.network.type=none \
      -- su - root
  else
    echo ' . "$_rsh"; PS1="$_bp"; exec <&1' | env _rsh="$rebuild_sh" _bp="$build_prompt" bash -i
  fi
}

cat <<EOF
DESTDIR=$DESTDIR

Apply your modifications and type 'rebuild-finalize' to build or 'rebuild-cancel' to cancel.
 Use 'cp2sfs </full/path/to/file>..' to include files from system to sfs.

EOF
keep_rebuilding=true
while $keep_rebuilding;do
  run_shell || {
    case "$?" in 100) continue;; esac
    echo "Cancelled.." >&2
    exit 1
  }
  echo "Rebuilding to ${out:-$src}.."
  rebuild_sfs "$DESTDIR" "$src" "$out" && keep_rebuilding=false || echo "Rebuild failed, try again." >&2
done
