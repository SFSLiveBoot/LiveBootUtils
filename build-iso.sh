#!/bin/sh

set -e

outfile="$1"

my_dir="$(dirname "$0")"
: ${tag:=$(hexdump -e '"%02x"' -n 2 </dev/urandom)}
: ${iso_label:=Bootable-$tag}
: ${root_dev:=LABEL=$iso_label}

test -n "$no_serial" -o -n "$ser_cons" || ser_cons="0"

test -n "$outfile" || {
  echo "Usage: ${0##*/} <output.iso> [<template_dir>]" >&2
  echo -n "Enter outfile .iso file name: "
  read outfile
  test -n "$outfile" || exit 1
}

rm_target() { echo -n "Removing $target.. " >&2; rm -r "$target"; echo "Ok.">&2; }

target="$2"
test -n "$target" || {
  target=$(mktemp -d /tmp/build-iso.XXXXXX)
  trap rm_target EXIT
  trap "exit 1" INT
}

: ${arch:=`uname -m`}
: ${kver:=`uname -r`}

mkdir -p "$target/boot/grub"

IFS_save="$IFS"
IFS="
"
for sfs in $(cat /sys/block/loop*/loop/backing_file);do
  test -n "$dist" || {
    dist_src="${sfs%/*}"
    dist="${dist_src##*/}"
  }
  mkdir -p "$target/$dist/$arch"
  case "$sfs" in
    *.sfs.[0-9]*[0-9]) test ! -e "${sfs%.sfs.[0-9]*[0-9]}.sfs" || sfs="${sfs%.sfs.[0-9]*[0-9]}.sfs" ;;
  esac
  case "$sfs" in
    *$kver*) test -e "$target/$dist/$arch/${sfs##*/}" || ln -vs "$sfs" "$target/$dist/$arch/" ;;
    *) test -e "$target/$dist/${sfs##*/}" || ln -vs "$sfs" "$target/$dist/" ;;
  esac
done
IFS="$IFS_save"

test -e "$target/$dist/font.pf2" || ln -s /usr/share/grub/ascii.pf2 "$target/$dist/font.pf2"

for logo_dir in "$dist_src" "${dist_src%/*}";do
  test -e "$logo_dir/logo.png" || continue
  test -e "$target/logo.png" || ln -s "$logo_dir/logo.png" "$target"
  break
done

for kern_dir in /boot /live/$arch /live "$dist_src/$arch" "$dist_src";do
  for comp in vmlinuz ramdisk ramdisk_net;do
    test -e "$target/$dist/$arch/$comp-$kver" || test ! -e "$kern_dir/$comp-$kver" || ln -vs "$kern_dir/$comp-$kver" "$target/$dist/$arch/"
  done
done

for comp in vmlinuz ramdisk;do
  test -e "$target/$dist/$arch/$comp-$kver" || {
    echo "$comp-$kver not found, aborting" >&2
    exit 1
  }
done

test -e "$target/grubvars.cfg" || cat >"$target/grubvars.cfg" <<EOF
set dist=$dist
set arch=$arch
set kver=$kver
set root_dev="$root_dev"
${ser_cons:+set ser_cons="$ser_cons"}
${append:+set append="$append"}
EOF

test -e "$target/boot/grub/grub.cfg" || echo "source /grub.cfg" >"$target/boot/grub/grub.cfg"
test -e "$target/grub.cfg" || cp -v "$my_dir/scripts/grub.cfg" "$target"

if ( echo "Modify filesystem structure and exit this shell with 'exit 0' to build, with 'exit 1' to abort"; cd "$target" ; PS1="(build-iso)$PS1" bash );then
  set -x
  grub-mkrescue -o "$outfile" "$target" -f -v -V "$iso_label"
else
  echo "Aborting build." >&2
fi

