#!/bin/sh -e

. "$(dirname "$0")/scripts/common.func"

trap 'echo -n "Press Enter to exit and remove $wd.";read x;test ! -d "$wd" || rm -r "$wd"' EXIT

outfile="$1"
label="CDILive"
live_dir="cdilive"
bootfile="boot/grub/eltorito.img"

test -n "$outfile" || {
  echo "Usage: ${0##*/} <outfile>"
  echo -n "Enter output .iso file name: "
  read outfile
  test -n "$outfile" || exit 1
}

wd="$(mktemp -d /tmp/livecd.XXXXXX)"

test ! -L "$outfile" || {
  update_link="$outfile"
  outfile="$outfile.$(date +%s)"
}

mkdir -p "$wd/boot/grub" "$wd/$live_dir"
echo "configfile /$live_dir/grub.cfg" > $wd/boot/grub/grub.cfg
cp /usr/lib/grub/i386-pc/* $wd/boot/grub
grub-mkimage -o "$wd/boot/grub/core.img" -O i386-pc biosdisk iso9660
cat "$wd/boot/grub/cdboot.img" "$wd/boot/grub/core.img" > "$wd/$bootfile"
grub_cfg_gfx $live_dir $wd /live /live/$live_dir >$wd/$live_dir/grub.cfg
copy_current_sfs -s "$wd/$live_dir"
grub_cfg_allentries $live_dir "$wd" "@$label" >>$wd/$live_dir/grub.cfg

echo "Apply your modifications and type 'exit 0' to build or 'exit 1' to cancel."
echo "DESTDIR=$wd"
if (cd "$wd" ; env DESTDIR="$wd" bash; );then
  set -x
  genisoimage -f -no-emul-boot --boot-load-size 4 --boot-info-table -r -b "$bootfile" -jcharset UTF-8 -V "$label" -o "$outfile" "$wd"
  test -z "$update_link" || ln -sfv "${outfile##*/}" "$update_link"
fi

