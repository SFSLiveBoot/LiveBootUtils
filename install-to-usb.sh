#!/bin/sh -e

. "$(dirname "$0")/scripts/common.func"

run_as_root "$@"
trap_fail

drive="$1"

test -n "$drive" -a -e "$drive" || {
  show_info "Usage: $(basename "$0") /dev/sd[a-z] . Use 'blkid -o list' to get mounted info, and 'cat /proc/partitions' to see all drives"
  drive=/dev/$(select_one "Blkid: $(blkid -o list | grep -v ^/dev/loop)." "$(grep -o '\<sd[a-z]\>' /proc/partitions)")
  test -e "$drive" || exit 1
}

confirm_text "This will destroy all data on $drive, are you sure?" "YES"

if grep -q "$drive" /proc/mounts;then
  show_info "Drive $drive is mounted, please run umount $drive first.$(echo; grep $drive /proc/mounts | cut -f1,2 -d" ")"
  exit 1
fi

set -x
dd if=/dev/zero of="$drive" bs=512 count=63

parted "$drive" mklabel msdos
parted "$drive" mkpart primary fat32 2048s 100%
parted "$drive" toggle 1 boot
part="${drive}1"

udevadm settle

mkdosfs $part

mnt="$(mktemp -d /tmp/liveboot.XXXXXX)"
mount $part $mnt
trap "show_info \"Failed. Cleaning up $mnt\";umount $mnt;rmdir $mnt" EXIT

show_info "Starting to copy .sfs files, that will take a while."
SILENT_EXIT=1 "$(dirname "$0")"/install-to-disk.sh "$mnt"

umount $mnt
rmdir $mnt

exit_succ