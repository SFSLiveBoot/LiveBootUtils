#!/bin/sh

set -e

: ${lbu:=/opt/LiveBootUtils}
: ${lbu:=$(dirname "$0")}
: ${arch:=$(uname -m)}

while true;do
  case "$1" in
    --pause|-p) pause=1;;
    --append) append="$2"; shift;;
    --serial-console) ser_cons="$2"; shift;;
    *) break;;
  esac
  shift
done

iso="$1"
test -n "$2" || {
  cat >&2 <<EOF
Usage: ${0##*/} [<options..>] <out.iso> <sources.sfs..>
Options:
   -p (or --pause)           Pause before build, allowing changes to be made
   --append <string>         Append to kernel commandline (in grub.cfg)
   --serial-console <digit>  Define serial console to use during boot
EOF
  exit 1
}
shift 1

test ! -e "$iso" || {
  echo "$iso already exists, aborting" >&2
  exit 1
}

iso_d=$(mktemp -d /tmp/build-iso.XXXXXX)

rm_tmp() { echo -n "Removing '$iso_d' .. "; rm -r "$iso_d"; echo "done."; }
trap rm_tmp EXIT

for fname;do
  : ${files_top:=$(dirname "$fname")}
  : ${dist:=$(basename "$files_top")}
  dest_name="${fname#$files_top/}"
  mkdir -p "$(dirname "$iso_d/$dist/$dest_name")"
  case "$dest_name" in
    $arch/[0-9][0-9]-kernel-*.sfs)
      kver="${dest_name#$arch/[0-9][0-9]-kernel-}"
      kver="${kver%.sfs}"
      dst="$iso_d/$dist/$arch" find "$(readlink -f "$(dirname "$fname")")" -maxdepth 1 \( -name "vmlinuz-$kver" -o -name "ramdisk*$kver" \) -exec sh -c 'for f;do ln -vs "$f" "$dst";done' _ {} +
    ;;
    */*)
      dname="${dest_name%/*}"
      case " $extras " in
        *" $dname "*) ;;
        *) extras="${extras+$extras }$dname";;
      esac
    ;;
  esac
  ln -vs "$(readlink -f "$fname")" "$iso_d/$dist/$dest_name"
done

for comp in vmlinuz ramdisk;do
  test -e "$iso_d/$dist/$arch/$comp-$kver" || {
    echo "Required file $comp-$kver not found, aborting. Did you specify correct XX-kernel-KVER.sfs in commandline?" >&2
    exit 1
  }
done

dq='"'
cat >"$iso_d/grubvars.cfg" <<EOF
set dist="$dist"
set arch="$arch"
set kver="$kver"
${extras+set extras=$dq$extras$dq}
${ser_cons+set ser_cons=$dq$ser_cons$dq}
${append+set append=$dq$append$dq}
EOF

mkdir -p "$iso_d/boot/grub"
ln -s "$lbu/scripts/grub.cfg" "$iso_d/boot/grub"

test -z "$pause" || { echo -n "Press ENTER to build '$iso' from '$iso_d' .. "; read x; }
grub-mkrescue -o "$iso" "$iso_d" -- -f -v
