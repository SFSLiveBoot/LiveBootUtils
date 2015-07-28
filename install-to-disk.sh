#!/bin/sh

: ${wd:=$(dirname "$0")}

. "$wd/scripts/common.func"
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
: ${dist:=live}

: ${kver:=`uname -r`}
: ${arch:=`uname -m`}

no_act() {
  if test -n "$no_act";then echo "No-act: $1" >&2
  else return 1;fi
}

copy_root_parts() {
  local root_aufs_si="$(grep " / aufs " /proc/mounts | grep -o si=[0-9a-f]* | cut -f2 -d=)"
  local dst="$1"
  : ${kernel_dst:=$dst/$arch/vmlinuz-$kver}
  : ${initrd_dst:=$dst/$arch/ramdisk-$kver}

  for root_part_br in /sys/fs/aufs/si_${root_aufs_si}/br[0-9]*; do
    read part_mnt <"$root_part_br"
    part_mnt="${part_mnt%=*}"
    part_dev="$(mnt2dev "$part_mnt")" || continue
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
    part_name="${part_sfs%.sfs*}"
    part_name="${part_name#[0-9][0-9]-}"
    test -z "$exclude_sfs" || {
      is_excluded=""
      for exclude_test in $exclude_sfs; do
        case "$part_name" in $exclude_test) is_excluded=1; break;; esac
        case "$part_file" in $exclude_test) is_excluded=1; break;; esac
      done
      test -z "$is_excluded" || { 
        echo "Skipping excluded sfs: '$part_name'" >&2
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
    test ! -e "$part_srcdir/logo.png" -o -e "$dst/logo.png" ||
      no_act "Copying logo from $part_srcdir" ||
      cp -v "$part_srcdir/logo.png" "$dst/logo.png"
    case "$part_sfs" in
      *"$kver"*)
        part_dstdir="$arch"
        test -n "$kernel" -o ! -r "$part_srcdir/vmlinuz-$kver" ||
          kernel="$part_srcdir/vmlinuz-$kver"
        ;;
      *)
        part_dstdir="${part_srcdir##*/}"
        test -z "$part_dst_sed" || part_dstdir="$(echo "$part_dstdir" | sed -e "$part_dst_sed")"
        if test "x$part_dstdir" = "x$dist" -o "x$part_dstdir" = "xlive";then part_dstdir=""; else
          case "$part_dirlist" in
            "$part_dstdir"|"$part_dstdir "*|*" $part_dstdir"|*" $part_dstdir "*) ;;
            *) part_dirlist="${part_dirlist:+$part_dirlist }$part_dstdir" ;;
          esac
        fi;;
    esac
    test -d "$dst${part_dstdir:+/$part_dstdir}" || no_act "Creating $dst/$part_dstdir" || mkdir -p "$dst${part_dstdir:+/$part_dstdir}"
    part_dst="$dst${part_dstdir:+/$part_dstdir}/$part_sfs"
    test -e "$part_dst" &&
      test "$(sfs_stamp "$part_dst")" -ge "$(sfs_stamp "$part_file")" &&
      echo "Newer or same version $part_dst ($(date --date=@$(sfs_stamp "$part_dst") +%y%m%d_%H%M%S)) already exists." || no_act "Copying $part_file" || {
        cat_file "$part_file" >"${part_dst}.NEW.$$"
        replace_sfs "${part_dst}.NEW.$$" "$part_dst" 
      }
  done
  if no_act "Installing kernel kver=$kver";then return 0; fi
  test -e "$kernel_dst" -o -n "$kernel" -o ! -r "/boot/vmlinuz-$kver" || kernel="/boot/vmlinuz-$kver"
  test -e "$kernel_dst" -o -z "$kernel" || {
    mkdir -p "$dst/$arch/"
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
        echo -n "initrd 'ramdisk-$kver' path: "
        read initrd
        test ! -d "$initrd" || initrd="$initrd/ramdisk-$kver"
      fi
    done
  }
}

install_grub() {
  local tgt="$1" dst_disk="$2"
  echo -n "installing grub to ${dst_disk}.. "
  grub-install --target=i386-pc --boot-directory="$tgt/boot" "$dst_disk"
  cp /usr/share/grub/ascii.pf2 "$tgt/boot/grub"
  echo "source /grub.cfg" > "$tgt/boot/grub/grub.cfg"
  install_grub_efi "$tgt"
}

create_grub_cfg() {
  local tgt="$1" dist="$2" dq='"'
  cat >"$tgt/grubvars.cfg" <<EOF
set dist="$dist"
set kver="$kver"
set arch="$arch"
set root_uuid="$tgt_uuid"
set storage_uuid="$storage_uuid"
$(case "$(cat /sys/class/dmi/id/product_name)" in "Latitude E6520") echo "set append=\"reboot=pci\"";;esac)
${part_dirlist:+set extras=$dq$part_dirlist$dq}
EOF
  cp "$wd/scripts/grub.cfg" "$tgt"
}

no_act "Creating $tgt/$dist and $tgt/boot/grub" || mkdir -p "$tgt/$dist" "$tgt/boot/grub"
no_act "Installing grub to $dst_disk" || install_grub "$tgt" "$dst_disk"
copy_root_parts "$tgt/$dist"
no_act "Creating grub.cfg" || create_grub_cfg "$tgt" "$dist"

test -z "$tmp_tgt" || { echo -n "Unmounting $tgt.. "; umount "$tgt"; echo "Done."; rmdir "$tgt"; }
exit_succ
