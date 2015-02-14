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

: ${info_d:=$sfs_src/var/lib/dpkg/info}

test -n "$show_only" || mkdir -p "$info_d"

cache_dir="$(find_apt_fullpath "Dir::Cache::archives")"

apt-get download --print-uris $(apt-get install -s "$@" | grep ^Inst | cut -f2 -d" ") | while read url fname fsize fhash;do
  test -z "$exclude_fnpat" || if echo "$fname" | grep -Eq "$exclude_fnpat";then continue; fi
  url="${url#'}"
  url="${url%'}"
  echo -n "${fname%%_*} = $url .. "
  test -z "$show_only" || { echo; continue; }
  case "$url" in
    file:///*) deb_file="${url#file://}" ;;
    http://*|ftp://*)
      test -s "$cache_dir/$fname" || {
        touch "$cache_dir/$fname"
        curl -L -o "$cache_dir/partial/$fname" "$url"
        mv "$cache_dir/partial/$fname"  "$cache_dir/$fname"
      }
      deb_file="$cache_dir/$fname"
    ;;
    *) echo "Unknown url: $url" >&2 ; false ;;
  esac
  dpkg-deb -x "$deb_file" "$sfs_src"
  pkg_name="$(dpkg-deb -f "$deb_file" Package)"
  test ! "x$(dpkg-deb -f "$deb_file" Multi-Arch)" = "xsame" ||
    pkg_name="$pkg_name:$(dpkg-deb -f "$deb_file" Architecture)"

  dpkg-deb --fsys-tarfile "$deb_file" | tar t | sed -e 's@^./@/@' -e 's@^/$@/.@' -e 's@/$@@' >"$info_d/${pkg_name}.list"
  ctrl_tmp="$(mktemp -d)"
  dpkg-deb -e "$deb_file" "$ctrl_tmp"
  for f in "$ctrl_tmp"/*;do
    mv "$f" "$info_d/$pkg_name.${f##*/}"
  done
  rmdir "$ctrl_tmp"
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
