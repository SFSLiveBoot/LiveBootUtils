#!/bin/sh

: ${lbu:=/opt/LiveBootUtils}
. "$lbu/scripts/common.func"

set -e

name2pat() {
  local name="$1"
  case "$name" in *.sfs) name="${name%.sfs}";; *.sfs.*) name="${name%.sfs.*}";;esac
  case "$name" in [0-9][0-9]-*) echo "$name";; *) echo "[0-9][0-9]-$name";;esac
}

is_fullpath() {
  case "$1" in */*) return 0;; *) return 1;;esac
}

is_dir() { test -d "$1"; }

find_part_in_aufs() {
  local name_pat="$(name2pat "$1")"
  local ret="$(aufs_parts / | grep -e "/$name_pat\$" -e "/$name_pat\\.[0-9]+\$" | head -1)"
  test -n "$ret" || return 1
  echo "$ret"
}

backing_file() {
  local mnt="$1" dev
  dev="$(mnt2dev "$mnt")" || return 1
  case "$dev" in
    /dev/loop*) ;;
    *) return 1;;
  esac
  cat "/sys/block/${dev#/dev/}/loop/backing_file"
}

sfs_latest_file() {
  local file="$1"
  case "$file" in
    *.sfs.*)
      if test -e "${file%.sfs.*}.sfs";then
        echo "${file%.sfs.*}.sfs"
      else
        echo "$file"
      fi
      ;;
    *) echo "$file";;
  esac
}

mounted_path() {
  local bf file="$1" loop mnt
  file="$(readlink -f "$file")"
  for loop in /sys/block/loop*/loop/backing_file;do
    read bf < $loop
    if test "x$file" = "x$bf";then
      loop="${loop#/sys/block/}"
      loop="${loop%/loop/backing_file}"
      mnt="$(grep "^/dev/$loop " /proc/mounts | head -1)"
      test -n "$mnt" || return 1
      echo "$mnt" | cut -f2 -d" " | sed -e 's/\040/ /g'
      return 0
    fi
  done
  return 1
}

is_sfs() {
  local file="$1"
  test "xhsqs" = "x$(fold -w4 "$file" | head -1)"
}

file_stamp() {
  local file="$1"
  if is_sfs "$file";then
    sfs_stamp "$file"
  else
    stat  -c %Y "$file"
  fi
}

mount_part_file() {
  local file="$1"
  local name="${file##*/}"
  name="${name%.sfs*}"
  local mnt="/.parts/$name.$(file_stamp "$file")"
  mkdir -p "$mnt"
  if mountpoint -q "$mnt";then
    echo "Error: $mnt is already in use" >&2
    return 1
  fi
  mount -o loop,ro "$file" "$mnt" >&2
  echo "$mnt"
}

find_part_distfile() {
  local name="$1"
  local IFS_save="$IFS"
  IFS="
"
 ret="$(find $(for part in $(aufs_parts /); do file2dev "$part";done | grep -v ^/dev/ | xargs dirname | sort -u) \( -path "*/$name" -o -name "${name}.sfs" -o -name "[0-9][0-9]-${name}.sfs" \) -not -name "*.sfs.*" | head -1)"
 IFS="$IFS_save"
 test -n "$ret" || return 1
 echo "$ret"
}

mount_part() {
  local name="$1"
  if is_fullpath "$name";then
    if is_dir "$name";then
      echo "$name"
    else
      mnt="$(mounted_path "$name")" ||
        mnt="$(mount_part_file "$name")"
      echo "$mnt"
    fi
  else
    if part_dir=$(find_part_in_aufs "$name");then
      if backing_file="$(backing_file "$part_dir")";then
        backing_file_latest="$(sfs_latest_file "$backing_file")"
        if test "$backing_file" -ef "$backing_file_latest";then
          echo "$part_dir"
        else
          mount_part "$backing_file_latest"
        fi
      else
        echo "$part_dir"
      fi
    elif dist_file="$(find_part_distfile "$name")";then
      mount_part "$dist_file"
    else
      echo "Could not locate part '$name'" >&2
      exit 1
    fi
  fi
}

mount_part "$1"
