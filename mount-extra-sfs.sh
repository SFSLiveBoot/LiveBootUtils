#!/bin/sh -e

test -r "$1" || {
  echo "Usage: $0 <file.sfs>" >&2
  exit 1
}

: ${rootfs:=/}
: ${aufs_si=$(grep -F " / aufs " /proc/mounts | tail -1 | grep -o si=[^,[:space:]]* | cut -f2 -d=)}

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

add_dir() {
  local mnt="$1" br_name
  local name="$(basename "$mnt")"
  if br_name=$(grep -lwFr "$(readlink -f "$mnt")" "/sys/fs/aufs/si_${aufs_si}");then
    echo -n "$name already mounted as ${br_name##*/} .. " >&2
    return 1
  else
    mount -t aufs -o "remount,ins:1:$mnt=ro" "$name" "$rootfs"
  fi
}

for sfs;do
  echo -n "Adding ${sfs##*/}.."
  if test -d "$sfs";then
    add_dir "$sfs" && echo ok || echo fail
  elif add_sfs "$sfs";then echo ok
  else echo fail;fi
done
