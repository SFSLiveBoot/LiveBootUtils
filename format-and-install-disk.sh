#!/bin/sh -e

: "${lbu:=$(dirname "$0")}"
. "$lbu/scripts/common.func"

run_as_root "$@"
trap_fail

: "${drive:=$1}"

case "$drive" in -h|--help)
  echo "Usage: ${0#**/} [<install_disk>]"
  echo "   Example: $0 /dev/sda"
  echo "Use 'blkid -o list' to get mounted drives info, and 'cat /proc/partitions' to see all usable devices"
  exit_succ
  exit 0
;; esac

: "${linux_fs:=ext4}"
: "${MKFS_LINUX:=mkfs.$linux_fs}"
: "${MKFS_FAT:=mkfs.vfat}"

test -n "$drive" -a -e "$drive" || {
  drive=/dev/$(select_one "Blkid: $(blkid -o list | grep -v -e ^/dev/loop -e '\<squashfs\>')." "$(grep -wo -e '[vs]d[a-z]' -e 'nvme[0-9]n[0-9]' /proc/partitions)")
  test -e "$drive" || exit 1
}

confirm_text "This will destroy all data on $drive, are you sure?" "YES"

case "$drive" in /dev/nvme*) _p="p";;esac

: "${part1:=${drive}${_p}1}"
: "${part2:=${drive}${_p}2}"
: "${part3:=${drive}${_p}3}"

if grep -q "$drive" /proc/mounts;then
  show_info "Drive $drive is mounted, please run umount $drive first.$(echo; grep $drive /proc/mounts | cut -f1,2 -d" ")"
  exit 1
fi

set -x

wipefs -a "$drive"
parted "$drive" mklabel msdos
parted "$drive" mkpart primary fat32 2048s 200MB
parted "$drive" mkpart primary "$linux_fs" 200MB 8GB
parted "$drive" mkpart primary "$linux_fs" 8GB 100%
parted "$drive" toggle 1 boot

sync
udevadm settle
blockdev --rereadpt "$drive"

wipefs -a "$part1"
wipefs -a "$part2"
wipefs -a "$part3"

$MKFS_FAT "$part1"
$MKFS_LINUX -L Boot "$part2"
$MKFS_LINUX -L Storage "$part3"

SILENT_EXIT=1 "$(dirname "$0")"/install-to-disk.sh "$part2"

: "${tgt_d:=/target}"
: "${tgt_boot:=$tgt_d/boot}"
: "${tgt_efi:=$tgt_d/efi}"

mkdir -p "$tgt_efi" "$tgt_boot"
mount "$part1" "$tgt_efi"
mount "$part2" "$tgt_boot" -o ro

tar c -C "$tgt_boot" EFI/Boot boot/grub/x86_64-efi | tar x -C "$tgt_efi"

cat >"$tgt_efi/boot/grub/grub.cfg" <<EOF
set root_uuid="$(blkid -o value -s UUID "$part2")"
search -u -s root \$root_uuid
set prefix=(\$root)/boot/grub
source /boot/grub/grub.cfg
EOF

umount "$tgt_efi" "$tgt_boot"
rmdir "$tgt_efi" "$tgt_boot" "$tgt_d"

exit_succ
