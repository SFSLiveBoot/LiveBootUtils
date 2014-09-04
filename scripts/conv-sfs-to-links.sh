#!/bin/sh

test -n "$1" || {
  echo "Usage: ${0##*/} <sfs_files..>" >&2
  exit 1
}

set -e

. "$(dirname "$0")"/common.func
trap_fail

for sfs;do
  test ! -d "$sfs" || {
    sfs_files="$(find "$sfs" -type f -name "*.sfs")"
    test -z "$sfs_files" || SILENT_EXIT=1 "$0" $sfs_files
    continue
  }
  test ! -L "$sfs" || {
    echo "$sfs is already a link, skipping."
    continue
  }
  stamp=$(sfs_stamp "$sfs")
  test -e "$sfs.$stamp" || {
    mv -v "$sfs" "$sfs.$stamp"
    ln -vs "${sfs##*/}.$stamp" "$sfs"
  }
done
exit_succ
