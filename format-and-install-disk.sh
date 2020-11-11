#!/bin/sh -e

. "$(dirname "$0")/scripts/common.func"

run_as_root "$@"
trap_fail

: ${drive:=$1}
: ${fs_type:=fat32}
: ${part_max:=100%}

test -n "$drive" -a -e "$drive" || {
  show_info "Usage: $(basename "$0") /dev/sd[a-z] . Use 'blkid -o list' to get mounted info, and 'cat /proc/partitions' to see all drives"
  drive=/dev/$(select_one "Blkid: $(blkid -o list | grep -v ^/dev/loop)." "$(grep -o '\<sd[a-z]\>' /proc/partitions)")
  test -e "$drive" || exit 1
}

confirm_text "This will destroy all data on $drive, are you sure?" "YES"

: ${part1:=${drive}1}
: ${part2:=${drive}2}

if grep -q "$drive" /proc/mounts;then
  show_info "Drive $drive is mounted, please run umount $drive first.$(echo; grep $drive /proc/mounts | cut -f1,2 -d" ")"
  exit 1
fi

test -n "$MKFS_PROG" ||
  case "$fs_type" in
    fat32) MKFS_PROG=mkdosfs ;;
    *) MKFS_PROG="mkfs.$fs_type" ;;
  esac

if test -z "$part2_start"; then
  part1_end="$part_max"
else
  part1_end="$part2_start"
fi

set -x

wipefs -a "$drive"
parted "$drive" mklabel msdos
parted "$drive" mkpart primary $fs_type 2048s $part1_end
parted "$drive" toggle 1 boot
test -z "$part2_start" || parted "$drive" mkpart primary $fs_type $part2_start $part_max

udevadm settle
blockdev --rereadpt "$drive"

wipefs -a "$part1"
test -z "$part2_start" || wipefs -a "$part2"

$MKFS_PROG $part1
test -z "$part2_start" || $MKFS_PROG ${part2_label:+-L "$part2_label"} $part2

SILENT_EXIT=1 "$(dirname "$0")"/install-to-disk.sh "$part1"

exit_succ
