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

die() {
  echo "ERROR: $*" >&2
  umount "$r"
  exit 1
}

for part; do
  test ! "x$part" = "x--" || break
  sfs_mnt="$("$lbu/scripts/sfs-mount.sh" "$part")" || die "Cannot find mount for $part"
  mount -o remount,ins:1:"$sfs_mnt" "$r" || die "Cannot mount $sfs_mnt"
done
