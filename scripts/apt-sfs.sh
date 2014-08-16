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
  mkdir -p "$sfs_src/var/lib/dpkg/info"
  keep_dirs=yes
else
  test -n "$show_only" ||  {
    sfs_src="$(mktemp -d /tmp/apt-sfs-src.XXXXXX)"
    chmod 755 "$sfs_src"
    mkdir -p "$sfs_src/var/lib/dpkg/info"
  }
fi

cache_dir="$(find_apt_fullpath "Dir::Cache::archives")"

apt-get download --print-uris $(apt-get install -s "$@" | grep ^Inst | cut -f2 -d" ") | while read url fname fsize fhash;do
  test -z "$exclude_fnpat" || if echo "$fname" | grep -Eq "$exclude_fnpat";then continue; fi
  url="${url#'}"
  url="${url%'}"
  echo -n "File $fname ($url) .."
  test -z "$show_only" || { echo; continue; }
  case "$url" in
    file:///*) dpkg-deb -x "${url#file://}" "$sfs_src";;
    http://*|ftp://*)
      test -e "$cache_dir/$fname" || curl -o "$cache_dir/$fname" "$url"
      dpkg-deb -x "$cache_dir/$fname" "$sfs_src"
      pkg_name="$(dpkg-deb -f "$cache_dir/$fname" Package)"
      case "$(dpkg-deb -f "$cache_dir/$fname" Multi-Arch)" in
        same) pkg_arch="$(dpkg-deb -f "$cache_dir/$fname" Architecture)" ;;
        *) pkg_arch="";;
      esac
      
      dpkg-deb --fsys-tarfile "$cache_dir/$fname" | tar t | sed -e 's@^./@/@' -e 's@^/$@/.@' -e 's@/$@@' >"$sfs_src/var/lib/dpkg/info/${pkg_name}${pkg_arch:+:$pkg_arch}.list"
    ;;
    *) echo "Unknown url: $url" >&2 ; false ;;
  esac
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
