#!/bin/sh

dir1="$1"
dir2="$2"

test -d "$dir1" -a -d "$dir2" || {
  echo "Usage: ${0##*/} <dir1> <dir2>" >&2
  exit 1
}

list1="$(mktemp)"
list2="$(mktemp)"

find_files() {
  find "$@" -path "*/old/*" -prune -o \( -name "*.sfs"  -exec sh -c '. /opt/LiveBootUtils/scripts/common.func; for f;do n="${f##*/}"; n="${n#[0-9][0-9]-}"; echo "$n $(sfs_stamp "$f" || echo FAIL)";done' a.sh {} + \) | sort -u
}

find_files "$dir1" >"$list1"
find_files "$dir2" >"$list2"

meld "$list1" "$list2"

rm -f "$list1" "$list2"
