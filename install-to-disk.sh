#!/bin/sh

. "$(dirname "$0")/scripts/common.func"
run_as_root "$@"

trap_fail

tgt="${1%%/}"
test -n "$tgt" || {
  tgt_dev=$(for blk in /sys/class/block/*/partition;do
    blk_dir="${blk%/partition}"
    blk_dev="/dev/${blk_dir##*/}"
    blk_blkid="$(cat "$blk_dir"/dev)"
    echo "$blk_dev"
    blk_mnt="$(blkid2mnt "$blk_blkid" || true)"
    echo "${blk_mnt:-Not mounted}"
    echo "$(blkid -o value -s TYPE "$blk_dev")"
    echo "$(blkid -o value -s LABEL "$blk_dev")"
  done | zenity --list --text="Select install destination" --column Partition --column Mountpoint --column Type --column "Volume label")
}

test ! -b "$tgt" || { tgt_dev="$tgt"; tgt=""; }

test -z "$tgt_dev" || if ! tgt="$(blkid2mnt "$(mountpoint -x "$tgt_dev")")";then
  tgt="$(mktemp -d /tmp/install.XXXXXX)"
  mount "$tgt_dev" "$tgt"
  tmp_tgt="1"
fi

mountpoint "$tgt"

dst_dev="$(mnt2dev "$tgt")"
tgt_uuid="$(blkid -s UUID -o value "$dst_dev")"
dst_disk="/dev/$(part2disk "$dst_dev")"

test -n "$tgt_uuid" || {
  echo "Cannot figure out UUID for $dst_dev, aborting" >&2
  exit 1
}

if storage_dev="$(blkid -o device -t LABEL=Storage | grep "^$dst_disk")";then
  storage_uuid="$(blkid -s UUID -o value "$storage_dev")"
fi

: ${dist:=$2}

if test -z "$dist";then
  part_binsh="$(echo /.parts/*/bin/sh)"
  test ! -e "$part_binsh" || {
    root_loop="$(mnt2dev "${part_binsh%/bin/sh}")"
    root_sfs="$(cat /sys/block/${root_loop#/dev/}/loop/backing_file)"
    dist="${root_sfs%/*}"
    dist="${dist##*/}"
  }
fi
: ${dist:=cdilive}

: ${kver:=`uname -r`}
: ${arch:=`uname -m`}

mkdir -p "$tgt/$dist" "$tgt/boot/grub"
echo "source /$dist/grub.cfg" > "$tgt/boot/grub/grub.cfg"

copy_root_parts() {
  local root_aufs_si="$(grep " / aufs " /proc/mounts | grep -o si=[0-9a-f]* | cut -f2 -d=)"
  local dst="$1"
  : ${kernel_dst:=$dst/$arch/vmlinuz-$kver}
  : ${initrd_dst:=$dst/$arch/ramdisk-$kver}

  for root_part_br in /sys/fs/aufs/si_${root_aufs_si}/br[0-9]*; do
    read part_mnt <"$root_part_br"
    part_mnt="${part_mnt%=*}"
    part_dev="$(mnt2dev "$part_mnt")"
    case "$part_dev" in
      /dev/loop*) part_dev="${part_dev#/dev/}";;
      *) continue;;
    esac
    read part_file < /sys/block/$part_dev/loop/backing_file
    test -r "$part_file" || {
      echo -n "Cannot read '$part_file'.. " >&2
      case "$part_file" in
        *.sfs.*) part_file="${part_file%.sfs.*}.sfs";;
        *" (deleted)") part_file="${part_file% (deleted)}";;
      esac
      if test -r "$part_file";then
        echo "using $part_file instead." >&2
      else
        echo "skipping." >&2
        continue
      fi
    }
    part_sfs="${part_file##*/}"
    test -z "$exclude_sfs" || {
      is_excluded=""
      for exclude_test in $exclude_sfs; do case "$part_file" in $exclude_test) is_excluded=1; break;; esac;done
      test -z "$is_excluded" || { 
        echo "Skipping excluded file: '$part_file'" >&2
        continue
      }
    }
    part_srcdir="${part_file%/*}"
    case "$part_sfs" in
      *.sfs.*)
        part_file_t="${part_file%.sfs.*}.sfs"
        part_sfs="${part_file_t##*/}"
        test ! -e "$part_file_t" || part_file="${part_file_t}"
      ;;
    esac
    test -e "$dst/font.pf2" || cp -v /usr/share/grub/ascii.pf2 "$dst/font.pf2"
    test ! -e "$part_srcdir/logo.png" -o -e "$dst/logo.png" ||
      cp -v "$part_srcdir/logo.png" "$dst/logo.png"
    case "$part_sfs" in
      *"$kver"*)
        part_dstdir="$arch"
        test -n "$kernel" -o ! -r "$part_srcdir/vmlinuz-$kver" ||
          kernel="$part_srcdir/vmlinuz-$kver"
        ;;
      *)
        part_dstdir="${part_srcdir##*/}"
        if test "x$part_dstdir" = "x$dist" -o "x$part_dstdir" = "xlive";then part_dstdir=""; else
          case "$part_dirlist" in
            "$part_dstdir"|"$part_dstdir "*|*" $part_dstdir"|*" $part_dstdir "*) ;;
            *) part_dirlist="${part_dirlist:+$part_dirlist }$part_dstdir" ;;
          esac
        fi;;
    esac
    mkdir -p "$dst${part_dstdir:+/$part_dstdir}"
    part_dst="$dst${part_dstdir:+/$part_dstdir}/$part_sfs"
    test -e "$part_dst" &&
      test "$(sfs_stamp "$part_dst")" -ge "$(sfs_stamp "$part_file")" &&
      echo "Newer or same version $part_dst already exists." || {
        cat_file "$part_file" >"${part_dst}.NEW.$$"
        replace_sfs "${part_dst}.NEW.$$" "$part_dst" 
      }
  done
  test -e "$kernel_dst" -o -n "$kernel" -o ! -r "/boot/vmlinuz-$kver" || kernel="/boot/vmlinuz-$kver"
  test -e "$kernel_dst" -o -z "$kernel" || {
    cp -v "$kernel" "$dst/$arch/"
  }
  test -e "$initrd_dst" || {
    for initrd in "$initrd" "${kernel%/*}/ramdisk-$kver" /boot/ramdisk-$kver /live/$arch/ramdisk-$kver /live/ramdisk-$kver;do
      test ! -r "$initrd" || break
    done
    while true;do
      if test -r "$initrd";then
        cp -v "$initrd" "$initrd_dst"
        test ! -r "${initrd%/*}/ramdisk_net-$kver" || 
          cp -vi "${initrd%/*}/ramdisk_net-$kver" "${initrd_dst%/*}/ramdisk_net-$kver"
        break
      else
        echo -n "initrd 'ramdisk-$kver' full path: "
        read initrd
      fi
    done
  }
}

install_grub_efi() {
  local tgt="$1"
  efi_arch="x86_64-efi"
  efi_image="bootx64.efi"
  efi_mods="configfile ext2 fat part_gpt part_msdos normal linux ls boot echo reboot search search_fs_file search_fs_uuid search_label help ntfs ntfscomp hfsplus chain multiboot terminal lspci font efi_gop efi_uga gfxterm"

  test -d "/usr/lib/grub/$efi_arch" || return 0
  test -z "$(find "$tgt" -maxdepth 3 -ipath "*/efi/boot/$efi_image")" || return 0
  efi_dir="$(find "$tgt" -maxdepth 2 -ipath "*/efi/boot")"
  test -n "$efi_dir" || {
    efi_dir="$(find "$tgt" -maxdepth 1 -ipath "*/efi")"
    if test -n "$efi_dir";then efi_dir="$efi_dir/Boot"
    else efi_dir="$tgt/EFI/Boot"; fi
  }
  mkdir -p "$efi_dir"
  grub-mkimage -o "$efi_dir/$efi_image" -O "$efi_arch" -p "/boot/$efi_arch" $efi_mods
  boot_dir="$(find "$tgt" -mindepth 1 -maxdepth 1 -type d -iname "boot")"
  test -n "$boot_dir" || { boot_dir="$tgt/boot" ; mkdir -p "$boot_dir"; }
  cp -r "/usr/lib/grub/$efi_arch" "$boot_dir"
  echo "source /boot/grub/grub.cfg" >"$boot_dir/$efi_arch/grub.cfg"
}

install_grub() {
  local tgt="$1" dst_disk="$2"
  echo -n "installing grub to ${dst_disk}.. "
  grub-install --boot-directory="$tgt/boot" "$dst_disk"
  install_grub_efi "$tgt"
}

create_grub_cfg() {
  local tgt="$1" dist="$2"
  cat >"$tgt/$dist/grubvars.cfg" <<EOF
# change as needed

set dist="$dist"
set kver="$kver"
set arch="$arch"
set root_uuid="$tgt_uuid"
set storage_uuid="$storage_uuid"
EOF
cat >"$tgt/$dist/grub.cfg" <<EOF

# kernel version etc. variables
source /$dist/grubvars.cfg

set timeout=5

if test -z "\$root_uuid";then
  probe -s root_uuid -u (\$root)
fi

if test -e /\$dist/font.pf2;then
  insmod font
  loadfont /\$dist/font.pf2
  insmod vbe
  insmod gfxterm
  set gfxmode="800x600x16"
  if terminal_output gfxterm;then
    if test -e /\$dist/logo.png;then
      insmod png
      background_image -m stretch /\$dist/logo.png
    elif test -e /logo.png;then
      insmod png
      background_image -m stretch /logo.png
    fi
  fi
fi

function load_lnx {
  if test -z "\$kernel";then
    set kernel="/\$dist/\$arch/vmlinuz-\$kver"
  fi
  if test -z "\$initrd";then
    set initrd="/\$dist/\$arch/ramdisk-\$kver"
  fi
  echo -n "Loading \$kernel.."
  linux \$kernel root=\$root_dev:\$dist/*.sfs+:\$dist/\\\$arch/*kernel-\\\$kver.sfs\$extra_sfs+\$storage_dev max_loop=64 \$append "\$@"
  echo -n "\$initrd.."
  initrd \$initrd
  echo "ok."
}

if test -n "\$storage_uuid";then
menuentry "Boot \$kver/\$arch from UUID=\$root_uuid [Storage: UUID=\$storage_uuid]" {
  set storage_dev="UUID=\$storage_uuid"
  set root_dev="UUID=\$root_uuid"
  set extra_sfs="$(for d in $part_dirlist;do echo -n "+:\$dist/$d/*.sfs";done)"
  load_lnx quiet
}
fi

menuentry "Boot \$kver/\$arch from UUID=\$root_uuid [mem]" {
  set storage_dev="mem"
  set root_dev="UUID=\$root_uuid"
  set extra_sfs="$(for d in $part_dirlist;do echo -n "+:\$dist/$d/*.sfs";done)"
  load_lnx quiet
}

menuentry "Boot \$kver/\$arch from UUID=\$root_uuid [light,mem]" {
  set storage_dev="mem"
  set root_dev="UUID=\$root_uuid"
  load_lnx quiet
}

menuentry "Boot \$kver/\$arch from UUID=\$root_uuid [light,copy-root-to-mem]" {
  set storage_dev="mem"
  set root_dev="mem:UUID=\$root_uuid"
  load_lnx quiet
}
EOF
}

install_grub "$tgt" "$dst_disk"
copy_root_parts "$tgt/$dist"
create_grub_cfg "$tgt" "$dist"

test -z "$tmp_tgt" || { umount "$tgt"; rmdir "$tgt"; }
exit_succ
