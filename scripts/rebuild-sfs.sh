#!/bin/sh

lbu_scripts="$(readlink -f "$(dirname "$0")")"
. "$lbu_scripts/common.func"

: ${lbu:=$(readlink -f "$lbu_scripts"/..)}
export lbu

set -e

on_exit() {
  lsof +d "$wd" "$wd"/* || true
  case "$lxc_name" in
    rebuild-$sname-$$.*)
        case "$(lxc-info -n "$lxc_name")" in *RUNNING*) lxc-stop -k -n "$lxc_name";;esac
        lxc-destroy -n "$lxc_name"
    ;;
  esac
  test ! -d "$lxc_conf" || {
    if mountpoint -q "$lxc_conf";then umount "$lxc_conf";fi
    rmdir "$lxc_conf"
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
  --lxc='<sfs_names..>'
                build in clean env with specific parts, ex. 'jessie-gnome settings scripts'
  --lxc-bind <fullpath>=<relpath>:  bind mount <fullpath> as <relpath>
                example: /usr/src/wine=usr/src/wine
  --auto:       rebuild automatically. in lxc mode, use 'rebuild-auto' at prompt.
EOF
}

cat_rebuild_sh() {
  cat <<EOF
. "${_cf:-$lbu_scripts/common.func}"
export DESTDIR="$DESTDIR" TERM="$TERM" lbu="$lbu" dl_cache_dir="/$lxc_dl_cache"
alias rebuild-finalize="exit 0"
alias rebuild-cancel="exit 1"
alias rebuild-reenter="exit 100"
alias rebuild-auto="$lbu_scripts/rebuild-destdir.sh && exit 0"
cd "\$DESTDIR"
test -z "$auto_rebuild" -o -e /root/.auto-rebuilt || { touch /root/.auto-rebuilt; rebuild-auto; }
EOF
}

build_lxc_container() {
  : ${root_sfs:=$(basename $(file2dev /bin/ls) | sed -e 's@\.sfs[.OLD0-9]*@@')}
  lxc_name="$(basename "$(mktemp -d -u /var/lib/lxc/rebuild-$sname-$$.XXXXXX)")"
  lxc_conf="$(mktemp -d /tmp/rebuild-$sname-conf.$$.XXXXXX)"

  mount -t tmpfs -o mode=0755 lxc-conf "$lxc_conf"
  rebuild_sh="/etc/profile.d/rebuild-$$.sh"
  apt_conf="/etc/apt/apt.conf.d/99rebuild-conf"
  mkdir -p "$lxc_conf${rebuild_sh%/*}" "$lxc_conf${apt_conf%/*}" "$lxc_conf$DESTDIR" "$lxc_conf/$lxc_dl_cache" \
    $(IFS="$_nl"; for mnt in $lxc_bind; do echo "$lxc_conf/${mnt#*=}";done)
  echo "APT::Get::List-Cleanup off;" >"$lxc_conf$apt_conf"
  cat_rebuild_sh >"$lxc_conf$rebuild_sh"

  lxc-create -t sfs -n "$lxc_name" -- \
    --default-parts="${lxc_parts:-$root_sfs settings scripts home}" --host-network \
    "$lxc_conf" "$mnt_src" "$wd/RW" \
    --bind "$(find_apt_fullpath "Dir::Cache::archives")=var/cache/apt/archives" \
    --bind "$(find_apt_fullpath "Dir::State::lists")=var/lib/apt/lists" \
    --bind "$dl_cache_dir=$lxc_dl_cache" \
    --bind "$DESTDIR=${DESTDIR#/}" \
    $(IFS="$_nl"; for mnt in $lxc_bind; do echo "--bind-ro=$mnt";done)
}

_nl='
'
auto_commands=' . "$_rsh"; PS1="$_bp";'
: ${lxc_bind=$lbu=${lbu#/}}
: ${lxc_dl_cache:=root/.cache/lbu/dl}

while test -n "$1" -a -z "${1##--*}";do
  case "$1" in
    --relink) relink="yes"; shift;;
    --auto) auto_commands="${auto_commands}${_nl} rebuild-auto;"; auto_rebuild="yes"; shift;;
    --lxc) use_lxc="yes"; shift;;
    --lxc=*)
      lxc_parts="${1#--lxc=}"
      use_lxc="yes";
      shift;;
    --lxc-bind) lxc_bind="${lxc_bind:+$lxc_bind$_nl}$2"; shift 2;;
    --help) usage; exit 0;;
    *) echo "Unknown option: '$1'" >&2; exit 1;;
  esac
done

auto_commands="${auto_commands}exec <&1;"

src="$1"
out="$2"

: ${sfs_gitloc:=usr/src/sfs.d/.git-source}
: ${sfs_gitcid:=usr/src/sfs.d/.git-commit}
: ${sfs_lxc_parts:=usr/src/sfs.d/.lxc-build-parts}

sfs_git_source() {
  local tmp="$(mktemp -u -d -t unsquash-$$.XXXXXX)" src="$1" git_src
  unsquashfs -n -d "$tmp" "$src" $sfs_gitloc >&2
  git_src="$(cat "$tmp/$sfs_gitloc")"
  rm -r "$tmp"
  test -n "$git_src" || return 1
  echo "$git_src"
}

test -z "$auto_rebuild" -o -n "$out" -o ! -s "$src" || {
  out="$src"
  src="$(sfs_git_source "$src")" || { src="$out"; out=""; }
}

case "$src" in
    git://*|http://*.git|https://*.git|file://*.git|git+*)
      git_src="$src"
      src="$(dl_file "$src")"
      echo "Source directory: $src"
      test ! -e "$src/.git-facls" || ( cd "$src"; setfacl --restore=.git-facls )
    ;;
esac

test -r "$src" || { usage >&2; exit 1; }

if test -d "$src";then
  test -n "$out" || { echo "With src as directory output file is mandatory" >&2; exit 1; }
  test ! -e "$src/$sfs_lxc_parts" || {
    test -n "${use_lxc+set}" || use_lxc="yes"
    test ! -s "$src/$sfs_lxc_parts" -o "${lxc_parts+set}" || read lxc_parts < "$src/$sfs_lxc_parts"
  }
  case "$(mnt2dev $(file2mnt "$src") 3)" in
    aufs)
      src_orig="$(aufs_orig "$src")"
      test "$(echo "$src_orig" | wc -l)" -eq 1 || {
        echo "Source in AUFS with multiple backends, please set dl_cache_dir env var to non-aufs location" >&2
        echo "Detected backends of '$src':" >&2
        echo "$src_orig" | sed -e 's/^/  /g' >&2
        echo "Possibly good locations: $(grep -w -e tmpfs -e "ext[2-4]" /proc/mounts  | cut -f2 -d" " | tr \\n " ")" >&2
        exit 1
      }
      src="$src_orig"
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
mnt_src="$src"
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
elif test -z "$lxc_name"; then
  build_lxc_container
fi

run_shell() {
  local ret
  if test -n "$use_lxc";then
    case "$(lxc-info -n "$lxc_name")" in
      *STOPPED*)
        lxc-start -n "$lxc_name" -d -- sleep 7200 || return $?
      ;;
    esac
    echo "After adding files to \$DESTDIR, run: mount -o remount /"
    lxc-attach -n "$lxc_name" -- su - root
    ret=$?
    mount -o remount "$DESTDIR"
  else
    echo "$auto_commands" | env _rsh="$rebuild_sh" _bp="$build_prompt" bash -i
    ret=$?
  fi
  return $ret
}

cat <<EOF
DESTDIR=$DESTDIR

Apply your modifications and type 'rebuild-finalize' to build or 'rebuild-cancel' to cancel.
 Use 'cp2sfs </full/path/to/file>..' to include files from system to sfs.

EOF
keep_rebuilding=true
while $keep_rebuilding;do
  test -z "$auto_rebuild" -o -z "$git_src" || {
    mkdir -p "$DESTDIR/${sfs_gitloc%/*}"
    echo "$git_src" >"$DESTDIR/$sfs_gitloc"
    (cd "$DESTDIR"; git log -1 --format=%H) >"$DESTDIR/$sfs_gitcid"
  }
  run_shell || {
    case "$?" in 100) continue;; esac
    echo "Cancelled.." >&2
    exit 1
  }
  echo "Rebuilding to ${out:-$src}.."
  rebuild_sfs "$DESTDIR" "$src" "$out" ${sfs_exclude_file:+-wildcards -ef "$sfs_exclude_file"} &&
    keep_rebuilding=false || echo "Rebuild failed, try again." >&2
done
