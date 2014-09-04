#!/bin/sh

. "$(dirname "$0")"/common.func

sfs1="$1"
sfs2="$2"

: ${DIFF:=diff -I ^d}
: ${UNSQLS:=unsquashfs -ll}
: ${UNSQS:=unsquashfs -s}

test -e "$sfs1" -a -e "$sfs2" || {
  echo "Usage: ${0##*/} <file1.sfs> <file2.sfs> [<diff_options>..]" >&2
  exit 1
}

shift 2

if test "x$(sfs_stamp "$sfs1")" = "x$(sfs_stamp "$sfs2")";then exit 0;fi

sfs1_list="$(mktemp /tmp/filelist-${sfs1##*/}.XXXXXX)"
sfs2_list="$(mktemp /tmp/filelist-${sfs2##*/}.XXXXXX)"

$UNSQS "$sfs1" >"$sfs1_list"
$UNSQS "$sfs2" >"$sfs2_list"

$UNSQLS "$sfs1" | tail -n+4 | sort -k6 >>"$sfs1_list"
$UNSQLS "$sfs2" | tail -n+4 | sort -k6 >>"$sfs2_list"

$DIFF "$@" "$sfs1_list" "$sfs2_list" ; ret="$?"
rm "$sfs1_list" "$sfs2_list"
exit $ret
