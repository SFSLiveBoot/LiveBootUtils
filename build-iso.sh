#!/bin/sh

set -e

output="$1"

tag=$(hexdump -e '"%02x"' -n 2 </dev/urandom)

test -n "$output" || {
  echo "Usage: ${0##*/} <output.iso> [<template_dir>]" >&2
  echo -n "Enter output .iso file name: "
  read output
  test -n "$output" || exit 1
}

rm_wd() { rm -r "$wd"; }

wd="$2"
test -n "$wd" || {
  wd=$(mktemp -d /tmp/build-iso.XXXXXX)
  trap rm_wd EXIT
}

: ${arch:=`uname -m`}
: ${kver:=`uname -r`}

mkdir -p "$wd/boot/grub"

IFS_save="$IFS"
IFS="
"
for sfs in $(cat /sys/block/loop*/loop/backing_file);do
  test -n "$dist" || {
    dist_src="${sfs%/*}"
    dist="${dist_src##*/}"
  }
  mkdir -p "$wd/$dist/$arch" 
  case "$sfs" in
    *.sfs.[0-9]*[0-9]) test ! -e "${sfs%.sfs.[0-9]*[0-9]}.sfs" || sfs="${sfs%.sfs.[0-9]*[0-9]}.sfs" ;;
  esac
  case "$sfs" in
    *$kver*) ln -vs "$sfs" "$wd/$dist/$arch/" ;;
    *) ln -vs "$sfs" "$wd/$dist/" ;;
  esac
done
IFS="$IFS_save"

for kern_dir in /boot /live/$arch /live "$dist_src/$arch" "$dist_src";do
  for comp in vmlinuz ramdisk ramdisk_net;do
    test -e "$wd/$dist/$arch/$comp-$kver" || test ! -e "$kern_dir/$comp-$kver" || ln -vs "$kern_dir/$comp-$kver" "$wd/$dist/$arch/"
  done
done

for comp in vmlinuz ramdisk;do
  test -e "$wd/$dist/$arch/$comp-$kver" || {
    echo "$comp-$kver not found, aborting" >&2
    exit 1
  }
done

test -e "$wd/boot/grub/vars.cfg" || cat >"$wd/boot/grub/vars.cfg" <<EOF
set dist=$dist
set arch=$arch
set kver=$kver
EOF

test -e "$wd/boot/grub/grub.cfg" || cat >"$wd/boot/grub/grub.cfg" <<EOF
echo -n "Press Esc to skip serial port setup..."
if sleep --verbose --interruptible 5;then
  if serial --unit=0 --speed=9600 --word=8 --parity=no --stop=1;then
    echo "serial initialized, adding to terminal"
    set serial="console=tty0 console=ttyS0"
    terminal_output --append serial
    terminal_input --append serial
  else
    echo "failed to initialize serial line"
  fi
fi
set timeout=5
probe -s root_label -l (\$root)
probe -s root_uuid -u (\$root)
source /boot/grub/vars.cfg

function load_lnx {
  echo -n "Loading \$dist/\$arch vmlinuz-\$kver.."
  linux /\$dist/\$arch/vmlinuz-\$kver root=\$root_dev:\$dist/*.sfs+:\$dist/\\\$arch/*kernel-\\\$kver.sfs\$extra_sfs+mem max_loop=64 \$serial "\$@"
  echo -n "ramdisk-\$kver.."
  initrd /\$dist/\$arch/ramdisk-\$kver
  echo "ok."
}

function cfg_extra_sfs {
  set extra_sfs=""
  for extra in \$extras;do
    extra_sfs="\$extra_sfs+:\$dist/\$extra/*.sfs"
  done
}

if test -n "\$extras";then
menuentry "Boot \$dist/\$arch \$kver [+\$extras] (LABEL=\$root_label)" {
  cfg_extra_sfs
  set root_dev="LABEL=\$root_label"
  load_lnx quiet
}
fi

menuentry "Boot \$dist/\$arch \$kver (LABEL=\$root_label)" {
  set root_dev="LABEL=\$root_label"
  load_lnx quiet
}

menuentry "Boot \$dist/\$arch \$kver [live-mem] (LABEL=\$root_label)" {
  set root_dev="mem:LABEL=\$root_label"
  load_lnx quiet
}

menuentry "Boot \$dist/\$arch \$kver (UUID=\$root_uuid)" {
  set root_dev="UUID=\$root_uuid"
  load_lnx quiet 
}

menuentry "Boot \$dist/\$arch \$kver (/dev/sda)" {
  set root_dev=/dev/sda
  load_lnx quiet 
}
EOF

if ( echo "Modify filesystem structure and exit this shell with 'exit 0' to build, with 'exit 1' to abort"; cd "$wd" ; PS1="(build-iso)$PS1" bash );then
  set -x
  grub-mkrescue -o "$output" "$wd" -f -v -V "Liveboot-$tag"
else
  echo "Aborting build." >&2
fi

