#!/bin/sh

set -e

test -e "$1" || {
  echo "Usage: ${0##*/} <sfs_or_dirs..>" >&2
  exit 1
}

: ${lbu:=$(readlink -f "$(dirname "$0")/..")}
. "$lbu/scripts/common.func"

: ${rebuild_sh:=$lbu/scripts/rebuild-sfs.sh}

_nl="
"
IFS_save="$IFS"

: ${sfs_gitloc:=usr/src/sfs.d/.git-source}
: ${sfs_gitcid:=usr/src/sfs.d/.git-commit}
: ${sfs_update_check:=usr/src/sfs.d/.check-up-to-date}

find_loop() {
  local file="$1" offset="${2:-0}" file_aufs bf bf_offset bf_file
  file_aufs="$(aufs_orig "$file")"
  test -z "$file_aufs" || file="$file_aufs"
  for bf in /sys/block/loop*/loop/backing_file;do
    read bf_file < "$bf"
    test "$bf_file" = "$file" || continue
    read bf_offset < "${bf%/backing_file}/offset"
    test "$bf_offset" -eq "$offset" || continue
    bf="${bf#/sys/block/}"
    echo "/dev/${bf%/loop/backing_file}"
    return 0
  done
  return 1
}

find_mount() {
  local dev="$1" dev_majmin ret
  test -b "$dev" || dev="$(find_loop "$1")" || return 1
  dev_majmin=$(mountpoint -x "$dev")
  ret="$(grep -E "^[0-9]+ [0-9]+ $dev_majmin " /proc/self/mountinfo | tail -1 | cut -f5 -d" " | sed -e 's@\\040@ @g')"
  test -n "$ret" || return 1
  echo "$ret"
}

unmount_tmp() {
  test -z "$do_unmount" || {
    echo -n "Unmounting "
    for mnt in $do_unmount;do
      echo -n "$mnt .. "
      umount "$mnt" && rmdir "$mnt" || true
    done
    echo "done."
    do_unmount=""
  }
}

trap unmount_tmp EXIT

for sfs;do
  if test -d "$sfs";then
    aufs_parts="$(aufs_parts "$sfs" | tac)"
    IFS="$_nl"
    if test -n "$aufs_parts";then
      for aufs_part in $aufs_parts;do
        IFS="$IFS_save"
        if ! back_sfs="$(file2dev "$aufs_part")";then
          echo "Cannot determine backing file for $aufs_part, skipping.." >&2
          continue
        fi
        case "$back_sfs" in
          *.sfs) ;;
          *.sfs.*) back_sfs="${back_sfs%.sfs.*}.sfs" ;;
          *)
            echo "$back_sfs does not seem to be like a .sfs file, skipping.." >&2
            continue
          ;;
        esac
        test -e "$back_sfs" || { echo "Skipping non-existant $back_sfs" >&2; continue; }
        "$0" "$back_sfs"
        aufs_update_sfs_branch "$aufs_part"
      done
    else
      "$0" $(find "$sfs" -mindepth 1 -name "*.sfs" -not -lname "*/*")
    fi
    IFS="$IFS_save"
    continue
  fi
  echo -n "Checking $sfs.. "
  sfs_mount="$(find_mount "$sfs")" || {
    sfs_mount="$(sfs2mnt "$sfs")"
    do_unmount="$sfs_mount${do_unmount:+ $do_unmount}"
  }

  if test -e "$sfs_mount/$sfs_gitloc";then
    git_loc="$(cat "$sfs_mount/$sfs_gitloc")"
    git_commit="$(cat "$sfs_mount/$sfs_gitcid")"
    echo "checking out latest version from $git_loc"
    sfs_git_dir="$(dl_file "$git_loc")"
    test "$(cd "$sfs_git_dir";git log -1 --format=%H)" = "$git_commit" || {
      echo "git commit changed, rebuilding.."
      "$rebuild_sh" --auto "$sfs"
      continue
    }
  else
    echo -n "no git.. "
  fi

  if test -x "$sfs_mount/$sfs_update_check";then
    env DESTDIR="$sfs_mount" "$sfs_mount/$sfs_update_check" || {
      echo "confirmed not to be latest version, rebuilding.."
      "$rebuild_sh" --auto "$sfs"
      continue
    }
    echo "confirmed to be latest version."
  else
    echo "no version check script."
  fi
  test -z "$do_unmount" || unmount_tmp
done
