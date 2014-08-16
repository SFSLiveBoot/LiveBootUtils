#!/bin/sh -e


. "$(dirname "$0")/common.func"
run_as_root "$@"

img="$1"

test -n "$img" -a -e "$img" || {
  show_info "Usage: $(basename "$0") storagefile.img"
  exit 1
}

mnt=$(mktemp -d /.parts/rw-XXXXXX)
chmod 755 $mnt
mount -o loop "$img" $mnt
mount -t aufs -o remount,ins:0:$mnt=rw none /
