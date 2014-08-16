#!/bin/sh -e

test -r "$1" || {
  echo "Usage: $0 <file.sfs>" >&2
  exit 1
}

: ${rootfs:=/}

add_sfs() {
  local sfs="$1"
  local mnt="$rootfs/.parts/$(basename "$sfs" .sfs)"

  test ! -e "$mnt" || {
    echo "Skipping $sfs, $mnt already exists" >&2
    return 1
  }

  mkdir -p "$mnt"
  mount -o loop,ro "$sfs" "$mnt"
  mount -t aufs -o "remount,ins:1:$mnt=rr" none "$rootfs"
}

for sfs;do
  echo -n "Adding ${sfs##*/}.."
  if add_sfs "$sfs";then echo ok
  else echo fail;fi
done
