#!/bin/sh

. "$(dirname "$0")/common.func"
set -e

if test "x$1" = "x-s" ;then show_only=1 ; shift ; fi
if test "x$1" = "x-x" ;then exclude_fnpat="$2" ; shift 2; fi

out_sfs="$1"
test ! -d "$out_sfs" || DESTDIR="$out_sfs"
test -n "$2" || {
  echo "Usage: ${0##*/} [-s] [-x <exclude_fnpat>] <out.sfs|DESTDIR> <pkg_names..>" >&2
  echo "  -s   show only what would be installed" >&2
  exit 1
}
shift

if test -n "$DESTDIR";then
  sfs_src="$DESTDIR"
  keep_dirs=yes
elif test -z "$show_only";then
  sfs_src="$(mktemp -d /tmp/apt-sfs-src.XXXXXX)"
  chmod 755 "$sfs_src"
fi

cache_dir="$(find_apt_fullpath "Dir::Cache::archives")"

if ! required_debs="$(required_debpkg "$@")";then
  exit 1
fi

test -z "$required_debs" || apt-get ${target_dist:+-t $target_dist} download --print-uris $required_debs | while read url fname fsize fhash;do
  test -z "$exclude_fnpat" || if echo "$fname" | grep -Eq "$exclude_fnpat";then continue; fi
  url="$(echo "$url" | tr -d "'")"
  echo -n "${fname%%_*} = $url .. "
  test -z "$show_only" || { echo; continue; }
  case "$url" in
    file:///*) deb_file="${url#file://}" ;;
    https://*|http://*|ftp://*)
      test -s "$cache_dir/$fname" || {
        touch "$cache_dir/$fname"
        curl -L -o "$cache_dir/partial/$fname" "$url"
        mv "$cache_dir/partial/$fname"  "$cache_dir/$fname"
      }
      deb_file="$cache_dir/$fname"
    ;;
    *) echo "Unknown url: $url" >&2 ; false ;;
  esac
  unpack_deb "$sfs_src" "$deb_file"
  echo "ok."
done

test -z "$show_only" || exit 0

if test -z "$DESTDIR" && (echo "Type 'exit 0' to build, 'exit 1' to cancel"; cd "$sfs_src"; env DESTDIR="$sfs_src" debian_chroot="build-apt-sfs" bash );then
  out_sfs_tmp="$out_sfs.NEW.$(date +%s)"
  mksquashfs "$sfs_src" "$out_sfs_tmp"
  unsquashfs -s "$out_sfs_tmp" >/dev/null
  set +x
  replace_sfs "$out_sfs_tmp" "$out_sfs"
fi

test -n "$keep_dirs" || rm -r "$sfs_src"
