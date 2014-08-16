#!/bin/sh -e

set -e

RM="rm -v"
test "x$1" != "x-n" || { RM="echo rm"; shift; }

test -e "$1" || {
  echo "Usage: ${0##*/} [-n] <link_to_current.sfs..>" >&2
  echo "  -n -- no act, just show what would be removed" >&2
  exit 1
}

clean_old_sfs() {
  local cur_link="$1" link_tgt dont_remove f loop_back
  link_tgt="$(readlink "$cur_link")"
  case "$link_tgt" in
    /*|*/*) return;;
    *) link_tgt="$(dirname "$cur_link")/$link_tgt";;
  esac

  for f in "$cur_link".*[0-9];do
    case "$f" in
      *.OLD.*) $RM "$f"; continue;;
      *.[0-9]*[0-9]) ;;
      *) echo "Skipping: $f" >&2; continue ;;
    esac
    test -L "$cur_link" || continue
    test -e "$f" || { echo "Warning: does not exist: $f" >&2; continue ; }
    if test "$f" -ef "$link_tgt" ;then continue; fi
    dont_remove=""
    for loop_back in /sys/block/loop*/loop/backing_file;do
      if test "$(cat "$loop_back")" -ef "$f";then
        dont_remove=1
        break
      fi
    done
    test -z "$dont_remove" || continue
    fuser -s "$f" || $RM "$f"
  done
}

for sfs ; do clean_old_sfs "$sfs";done
