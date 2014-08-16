#!/bin/sh -e

. "$(dirname "$0")/scripts/common.func"

run_as_root "$@"

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

dd if=/dev/zero of="$drive" bs=512 count=63

parted "$drive" mklabel msdos
parted "$drive" mkpart primary fat32 2048s 100%
part="${drive}1"

mkdosfs -n CDILiveBoot $part

mnt="$(mktemp -d /tmp/liveboot.XXXXXX)"
part_uuid=$(blkid -o value -s UUID $part)
mount $part $mnt
trap "show_info \"Failed. Cleaning up $mnt\";umount $mnt;rmdir $mnt" EXIT

show_info "Starting to copy .sfs files, that will take a while."
"$(dirname "$0")"/install-to-disk.sh "$mnt"

umount $mnt
rmdir $mnt

trap - EXIT
show_info "Succeeded"
