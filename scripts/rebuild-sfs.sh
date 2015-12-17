#!/bin/sh

lbu_scripts="$(readlink -f "$(dirname "$0")")"
. "$lbu_scripts/common.func"

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
Usage: ${0##*/} [<options>] {<old.sfs>|source_dir|git_url} [<new.sfs>=$out]
Options:
  --relink:     replace sfs link even if it points to other directory
  --lxc:        build in clean lxc environment
  --auto:       rebuild automatically
  --lxc-bind <fullpath>=<relpath>:  bind mount <fullpath> as <relpath>
                example: /usr/src/wine=usr/src/wine
EOF
}

cat_rebuild_sh() {
  cat <<EOF
. "${_cf:-$lbu_scripts/common.func}"
export DESTDIR="$DESTDIR" TERM="$TERM"
alias rebuild-finalize="exit 0"
alias rebuild-cancel="exit 1"
alias rebuild-reenter="exit 100"
alias rebuild-auto="$lbu_scripts/rebuild-destdir.sh && exit 0"
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
  apt_conf="/etc/apt/apt.conf.d/99rebuild-conf"
  echo "APT::Get::List-Cleanup off;" >"$lxc_root$apt_conf"
  cat_rebuild_sh >"$lxc_root$rebuild_sh"
  mkdir -p "$lxc_root/$DESTDIR"
  mount --bind "$DESTDIR" "$lxc_root/$DESTDIR"
}

_nl='
'
auto_commands=' . "$_rsh"; PS1="$_bp";'

while test -n "$1" -a -z "${1##--*}";do
  case "$1" in
    --relink) relink="yes"; shift;;
    --auto) auto_commands="${auto_commands}${_nl}rebuild-auto;"; shift;;
    --lxc) use_lxc="yes"; shift;;
    --lxc-bind) lxc_bind="${lxc_bind:+$lxc_bind$_nl}$2"; shift 2;;
    --help) usage; exit 0;;
    *) echo "Unknown option: '$1'" >&2; exit 1;;
  esac
done

auto_commands="${auto_commands}exec <&1;"

src="$1"
out="$2"

case "$src" in
    git://*|http://*.git|https://*.git|file://*.git)
      src="$(dl_file "$src")"
      echo "Source directory: $src"
      test ! -e "$src/.git-facls" || ( cd "$src"; setfacl --restore=.git-facls )
    ;;
esac

test -r "$src" || { usage >&2; exit 1; }

if test -d "$src";then
  test -n "$out" || { echo "With src as directory output file is mandatory" >&2; exit 1; }
  case "$(mnt2dev $(file2mnt "$src") 3)" in
    aufs)
      echo "Source directory cannot be located on aufs. Perhaps you should set dl_cache_dir env var?" >&2
      echo "Possibly good locations: $(grep -w -e tmpfs -e ext[2-4] /proc/mounts  | cut -f2 -d" " | tr \\n " ")" >&2
      exit 1
    ;;
  esac
fi

test -n "$relink" -o ! -L "${out:-$src}" || {
  case "$(readlink "${out:-$src}")" in
    */*)
      echo "ERROR: $(basename "${out:-$src}") is pointing to file in different directory: $(readlink ${out:-$src})" >&2
      echo "Use --relink option to rebuild anyway." >&2
      exit 1
    ;;
  esac
}

wd="$(mktemp -d /tmp/rebuild.XXXXXX)"
trap on_exit EXIT

chmod 755 $wd

mount_combined "$wd" "$src"
test ! -d "$src" || { src="$out"; out=""; }
DESTDIR="$wd/ALL"
sname="$(basename "${out:-$src}" .sfs)"
sname="${sname#[0-9][0-9]-}"
build_prompt="\\nRebuilding: \"${sname}.sfs\". Use 'rebuild-cancel' to cancel, 'rebuild-finalize' to save changes."
build_prompt="$build_prompt\\n[\A][\W]\\\$ "

test -n "${sfs_exclude_file+yes}" -o ! -e "$DESTDIR/usr/src/sfs.d/.sqfs-exclude" ||
  sfs_exclude_file="$DESTDIR/usr/src/sfs.d/.sqfs-exclude"

if test -z "$use_lxc";then
  rebuild_sh="$(mktemp /tmp/rebuild-$$.XXXXXX.sh)"
  DESTDIR="$DESTDIR" _cf="$lbu_scripts/common.func" cat_rebuild_sh >"$rebuild_sh"
else
  build_lxc_root
fi

run_shell() {
  if test -n "$use_lxc";then
    find "$lxc_rw" -depth \
      $(d="$lxc_rw" IFS=/;for x in $DESTDIR; do d="$d${x:+/$x}"; echo -not -path "$d";done) \
      $(d="$lxc_rw" IFS=/;for x in $rebuild_sh; do d="$d${x:+/$x}"; echo -not -path "$d";done) \
      $(d="$lxc_rw" IFS=/;for x in $apt_conf; do d="$d${x:+/$x}"; echo -not -path "$d";done) \
      -not -path "$lxc_rw/.wh..wh.????" \
      -delete
    mount -o remount "$lxc_root"
    echo "After adding files to \$DESTDIR, run: mount -o remount /"
    test -z "$lxc_bind" || for mnt in $lxc_bind;do mkdir -p "$lxc_root/${mnt##*=}" ;done
    (IFS="$_nl"; lxc-execute -n "rebuild-$sname" -l debug \
      -s lxc.utsname="rebuild-$sname" \
      -s lxc.rootfs="$lxc_root" \
      -s lxc.network.type=none \
      -s lxc.mount.entry="$(find_apt_fullpath "Dir::Cache::archives") var/cache/apt/archives none bind 0 0" \
      -s lxc.mount.entry="$(find_apt_fullpath "Dir::State::lists") var/lib/apt/lists none bind 0 0" \
      ${lxc_bind:+$(for mnt in $lxc_bind;do echo -s;echo "lxc.mount.entry=${mnt%=*} ${mnt##*=} none bind,ro 0 0";done)} \
      -- su - root
    )
  else
    echo "$auto_commands" | env _rsh="$rebuild_sh" _bp="$build_prompt" bash -i
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
  rebuild_sfs "$DESTDIR" "$src" "$out" ${sfs_exclude_file:+-wildcards -ef "$sfs_exclude_file"} &&
    keep_rebuilding=false || echo "Rebuild failed, try again." >&2
done
