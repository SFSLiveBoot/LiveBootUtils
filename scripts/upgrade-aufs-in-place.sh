#!/bin/sh

. "$(dirname "$0")/common.func"

trap_fail

target="${1:-/}"

si="$(mnt2dev "$target" 4 | grep -o "si=[^,]*" | cut -f2 -d=)"

test -n "$si" || {
  echo "Cannot parse aufs si for $target. Not an aufs?" >&2
  exit 1
}

set -e

done_sfs=":"

for br in $(find /sys/fs/aufs/si_${si} -name "br[0-9]*");do
  br_num="${br##*/br}"
  br_desc="$(cat "$br")"
  br_mnt="${br_desc%=*}"
  echo -n "Processing br$br_num = $br_mnt .. "
  br_src="$(file2dev "$br_mnt")" || { echo " skipping, not backed by device." ; continue ; }
  br_sfs="${br_src%.sfs*}.sfs"
  case "$done_sfs" in
    *:${br_sfs##*/}:*)
      echo -n "already processed, trying to unmount .. "
      mount -o remount,del:"$br_mnt" "$target" &&
        umount "$br_mnt" &&
        rmdir "$br_mnt" &&
        echo "done." || {
          echo "still in use."
          if test -d /proc/self/map_files;then
            find /proc/[0-9]*/map_files -lname "$br_mnt/*" | cut -f3 -d/ | sort -u | xargs -r ps
          else
            grep -lwF "$br_mnt" /proc/[0-9]*/maps | cut -f3 -d/ | sort -u | xargs -r ps
          fi
        } || true
      continue
    ;;
  esac
  done_sfs="$done_sfs${br_sfs##*/}:"
  case "$br_src" in
    *.sfs.*)
      if test "$br_src" -ef "$br_sfs";then echo "not modified."
      else
        echo -n "replacing.. "
        new_br_mnt=$(echo -n "$br_mnt" | sed -e 's/\(\.[0-9]\+\)*$//').$(sfs_stamp "$br_sfs") || {
          echo "could not read time stamp of '$br_sfs' ?"
          continue
        }
        mkdir -p "$new_br_mnt"
        mountpoint -q "$new_br_mnt" || mount "$br_sfs" "$new_br_mnt" -o ro,loop
        mount -o "remount,ins:$br_num:$new_br_mnt" "$target"
        mount -o remount,del:"$br_mnt" "$target" || {
          echo "  leaving $br_mnt mounted, probably still in use.."
          continue
        }
        umount "$br_mnt" || { echo "could not unmount $br_mnt" ; continue; }
        rmdir "$br_mnt"
        echo "ok."
      fi
    ;;
    *) echo "not an old .sfs.XXX file ($br_src)";;
  esac
done

exit_succ
