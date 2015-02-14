#!/bin/sh

. "$(dirname "$0")/common.func"

target="${1:-/}"
si="$(mnt2dev "$target" 4 | grep -o "si=[^,]*" | cut -f2 -d=)"

for br in $(find "/sys/fs/aufs/si_${si}" -name "br[0-9]*");do
  IFS="=" read mnt mode < "$br"
  if test "x$mode" = "xrw" ;then
    rw_mnt="$mnt"
  else
    env rw_mnt="$rw_mnt" mnt="$mnt" find "$rw_mnt$find_subdir" -type f $find_args -exec sh -c 'for f;do n="${f#$rw_mnt/}"; test ! -e "$mnt/$n" || { diff -u "$mnt/$n" "$f" && echo "# Same: $mnt: $f"; }; done' "$0.findexec" {} +
  fi
done

