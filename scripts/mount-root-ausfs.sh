#!/bin/sh

set -e

test -n "$1" || {
  echo "Usage: ${0##*/} <component..>" >&2
  echo "Components can be block devices, \"mem\" and .sfs filenames" >&2
  exit 1
}

mnt_for() {
  local tgt="$1" lback dev m_dev m_mnt x tgt_mnt
  if test -f "$tgt";then
    for lback in /sys/block/loop*/loop/backing_file;do
      if test "$(cat "$lback")" -ef "$tgt";then
        dev="${lback%/loop/backing_file}"
        mnt_for "/dev/${dev#/sys/block/}"
        return $?
      fi
    done
    return 1
  elif test -b "$tgt";then
    dev="$(mountpoint -x "$tgt")"
    while read -r m_dev m_mnt x;do
      case "$m_dev" in /*) test -e "$m_dev" || continue ;; *) continue;; esac
      if test "$dev" = "$(mountpoint -d "$m_mnt")";then
        tgt_mnt="$m_mnt"
      elif test -n "$tgt_mnt" -a "$tgt_mnt" = "$m_mnt";then
        echo "Warning: $tgt is mounted to $tgt_mnt, which is MOUNTED OVER by $m_dev" >&2
        tgt_mnt=""
      fi
    done < /proc/mounts
    if test -z "$tgt_mnt";then return 1;else
      echo "$tgt_mnt"
      return 0
    fi
  elif test -d "$tgt";then
    case "$tgt" in /*) ;; *) tgt="$(realpath "$tgt")" ;; esac
    while test -n "$tgt";do
      if mountpoint -q "$tgt";then echo "$tgt";return 0;fi
      tgt="$(dirname "$tgt")"
    done
    return 1
  else
    echo "WARNING: mnt_for: Unknown file type for '$tgt'" >&2
    return 1
  fi
}

new_mnt() {
  local opts fstype
  while true;do
    case "$1" in
     -o) opts="$2"; shift ;;
     -t) fstype="$2"; shift ;;
     *) break;
    esac
    shift
  done
  if test "x$1" = "x-o";then
    opts="$2"
    shift 2
  fi
  local src="$1" mnt="${1##*/}"
  mnt="${mnt%.*}"
  mnt="$(mktemp -d /.parts/$mnt.XXXXXX)"
  if mount ${fstype:+-t $fstype} ${opts:+-o "$opts"} "$1" "$mnt";then
    echo "$mnt"
  else
    rmdir "$mnt"
    return 1
  fi
}

dirs=""
umount_lst=""
for component;do
  case "$component" in
    *.sfs)
      mnt="$(mnt_for "$component")" || { mnt="$(new_mnt -o ro,loop "$component")"; umount_lst="${umount_lst:+$umount_lst }$mnt"; }
      dirs="${dirs:+$dirs:}$mnt=rr"
    ;;
    mem)
      mnt="$(new_mnt -t tmpfs -o mode=755 mem)"
      umount_lst="${umount_lst:+$umount_lst }$mnt";
      dirs="${dirs:+$dirs:}$mnt=rw"
    ;;
    *)
      if test -b "$component";then
        mnt="$(mnt_for "$component")" || { mnt="$(new_mnt "$component")"; umount_lst="${umount_lst:+$umount_lst }$mnt"; }
        dirs="${dirs:+$dirs:}$mnt=rw"
      else
        echo "Don't know what to do with $component" >&2
      fi
    ;;
  esac
done

target="$(mktemp -d /tmp/root-ausfs.XXXXXX)"

mount -t aufs -o "dirs=$dirs" none "$target"
test ! -d "$target/proc" || mount -t proc none "$target/proc"
test ! -d "$target/sys" || mount -t sysfs none "$target/sys"
test ! -d "$target/dev/pts" || mount -t devpts none "$target/dev/pts"
for d in var/lib/apt/lists var/cache/apt/archives;do
  test ! -d "$target/$d" || mount --bind /$d $target/$d
done

echo "Use exit to end session (and unmount $target and $umount_lst)"
test -e "$target/etc/resolv.conf" || cp -avt "$target" --parents /etc/resolv.conf
( cd "$target" ; bash; )

echo "Unmounting.."
for mnt in $(grep "^[^ ]* $target[/ ]" /proc/mounts  | cut -f2 -d" " | tac ) $umount_lst;do
  mnt="$(echo -n "$mnt" | sed -e 's/\\040/ /g')"
  echo "  $mnt"
  if umount "$mnt";then
    rmdir --ignore-fail-on-non-empty "$mnt" || true
  else
    echo "Failed to unmount $mnt" >&2
  fi
done
echo "Done."
