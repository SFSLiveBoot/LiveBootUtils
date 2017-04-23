#!/bin/sh

set -e

: ${lbu:=/opt/LiveBootUtils}
. "$lbu/scripts/common.func"

dist_d="$(dirname "$(file2dev /bin/sh)" | tail -1)"

: ${r:=$LXC_ROOTFS_PATH}
: ${LXC_ROOT_RW:=${r}.rw}

if mountpoint -q "$r" ;then exit 0;fi

mkdir -p "$r" "$LXC_ROOT_RW"

test "x$(mnt2dev $(file2mnt "$LXC_ROOT_RW") 3)" != "xaufs" ||
  mount -t tmpfs -o mode=0755 mem "$LXC_ROOT_RW"
mount -t aufs -o dirs="$LXC_ROOT_RW"=rw root "$r"

for part; do
  test ! "x$part" = "x--" || break
  sfs_mnt=""
  for sfs in $(case "$part" in /*) echo "$part" ;; *) find "$dist_d" \( -path "*/$part"  -o -name "${part}.sfs" -o -name "[0-9][0-9]-${part}.sfs" \) -not -name "*.sfs.*";;esac);do
    if test -d "$sfs";then sfs_mnt="$sfs"
    else
      sfs_mnt="/.parts/$(basename "$sfs" .sfs)"
      mountpoint -q "$sfs_mnt" || {
        mkdir -p "$sfs_mnt"
        mount -o loop,ro "$sfs" "$sfs_mnt"
      }
    fi
    mount -o remount,ins:1:"$sfs_mnt" "$r"
  done
  test -n "$sfs_mnt" || {
    echo "Error: '$part' part not found" >&2
    umount "$r"
    exit 1
  }
done
