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
: ${sfs_update_check:=usr/src/sfs.d/.is-up-to-date}

cat_sfs_file() {
  local sfs="$1" fname="$2" unsq_tmp="$(mktemp -u -d /tmp/unsquash.$$.XXXXXX)" ret
  unsquashfs -n -d "$unsq_tmp" "$sfs" "$fname" >/dev/null
  test -e "$unsq_tmp/$fname" || ret=1 && cat "$unsq_tmp/$fname"
  rm -r "$unsq_tmp"
  return $ret
}

find_loop() {
  local file="$1" file_aufs bf bf_offset bf_file
  file_aufs="$(aufs_orig "$file")"
  test -z "$file_aufs" || file="$file_aufs"
  for bf in /sys/block/loop*/loop/backing_file;do
    read bf_file < "$bf"
    test "$bf_file" = "$file" || continue
    read bf_offset < "${bf%/backing_file}/offset"
    test "$bf_offset" -eq 0 || continue
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

for sfs;do
  if test -d "$sfs";then
    IFS="$_nl"
    "$0" $(find "$sfs" -mindepth 1 -name "*.sfs" -not -lname "*/*")
    IFS="$IFS_save"
    continue
  fi
  echo -n "Checking $sfs.. "
  if git_loc="$(cat_sfs_file "$sfs" "$sfs_gitloc")";then
    echo "checking out latest version from $git_loc"
    git_commit="$(cat_sfs_file "$sfs" "$sfs_gitcid")"
    sfs_git_dir="$(dl_file "$git_loc")"
    test "$(cd "$sfs_git_dir";git log -1 --format=%H)" = "$git_commit" || {
      echo "git commit changed, rebuilding.."
      "$rebuild_sh" --auto "$sfs"
      continue
    }
  else
    echo -n "no git.. "
  fi
  sfs_mount="$(find_mount "$sfs")" || {
    do_unmount="$sfs_mount${do_unmount:+ $do_unmount}"
    sfs_mount="$(sfs2mnt "$sfs")"
  }
  if test -x "$sfs_mount/$sfs_update_check";then
    env DESTDIR="$sfs_mount" "$sfs_mount/$sfs_update_check" || {
      echo "confirmed not latest version, rebuilding"
      "$rebuild_sh" --auto "$sfs"
      continue
    }
    echo "confirmed to be latest version"
  else
    echo "no version check script '$sfs_update_check'"
  fi
done
