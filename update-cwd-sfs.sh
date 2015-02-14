#!/bin/sh -e

src="${1%%/}"

test -n "$src" || {
  echo "Usage: $(basename "$0") <src>=$src [<dstdirs..>=$*]" >&2
  echo " dstdirs defaults to $PWD" >&2
  exit 1
}

shift

test -n "$1" || set -- "$PWD"

sfs_stamp_file() {
  if test -e "$1" && stamp="$(unsquashfs -s "$1" | grep time | cut -f7- -d" ")";then
    date -d "$stamp" +%s
  else
   ret=1
  fi
}

sfs_stamp() {
  local stamp dl_tmp ret
  case "$1" in
    http://*|https://*|ftp://*)
      dl_tmp="$(mktemp)"
      if curl -f -s -r 0-1024 -o "$dl_tmp" "$1";then
        sfs_stamp_file "$dl_tmp"
        ret="$?"
      else ret=1;fi
      rm "$dl_tmp"
    ;;
    *) sfs_stamp_file "$1" ;;
  esac
  return $ret
}

get() {
  case "$1" in
    http://*|https://*|ftp://*) curl -f "$1";;
    *)
      if test -x "$(which bar)";then
        bar -ns -ti "${1##*/}" -s $(stat -Lc %s "$1") -if "$1"
      else 
        echo -n "Copying.." >&2;cat "$1";echo >&2
      fi
    ;;
  esac
}

: ${find_maxdepth:=3}

IFS_save="$IFS"
IFS="
"
for sfs in $(find "$@" -maxdepth $find_maxdepth -name "*.sfs"); do
  if test -L "$sfs";then
    sfs_target="$(readlink "$sfs")"
    case "$sfs_target" in
      */*) echo "Skipping $sfs: symlink target in different directory ($sfs_target)" >&2; continue;;
    esac
  fi
  test -e "$sfs" || { echo "$sfs does not exist, skipping"; continue; }
  sfs_bn="$(basename "$sfs")"
  case "$sfs_bn" in
    [0-9][0-9]-*) sfs_num="${sfs_bn%%-*}" sfs_bn="${sfs_bn#[0-9][0-9]-}" ;;
    *) sfs_num="";;
  esac
  src_sfs="$src/$sfs_bn"
  src_stamp="$(sfs_stamp "$src_sfs")" || {
    if test -n "$sfs_num" && src_stamp="$(sfs_stamp "$src/$sfs_num-$sfs_bn")";then
      src_sfs="$src/$sfs_num-$sfs_bn"
    elif test -d "$src";then
      for test_sfs in $(find "$src" -maxdepth $find_maxdepth -name "[0-9][0-9]-$sfs_bn" -or -name "$sfs_bn");do
        if src_stamp="$(sfs_stamp "$test_sfs")";then
          src_sfs="$test_sfs"
          break
        fi
      done
    fi
  }
  if test -n "$src_stamp";then
    has_src=1
    echo -n "Checking $sfs_bn creation time... "
    if test $(sfs_stamp "$sfs") -lt "$src_stamp";then
      sfs_new="$sfs.NEW.$$"
      echo "Needs update (getting as $sfs_new)"
      test ! -L "$sfs" || test ! -e "$sfs.$src_stamp" || {
        echo "Already have $sfs.$src_stamp, skipping.." >&2
        continue
      }
      get "$src_sfs" > "$sfs_new"
      unsquashfs -ls "$sfs_new" / >/dev/null || {
        echo "Getting $src_sfs failed." >&2
        mv "$sfs_new" "$sfs.FAILED.$(date +%s)"
        continue
      }
      touch -m -d @$src_stamp "$sfs_new"
      sfs_mode="$(stat -Lc %a "$sfs")"
      sfs_owner="$(stat -Lc %u "$sfs")"
      sfs_group="$(stat -Lc %g "$sfs")"
      chgrp "$sfs_group" "$sfs_new" || true
      chown "$sfs_owner" "$sfs_new" || true
      chmod "$sfs_mode" "$sfs_new" || true

      old_sfs="$sfs.OLD.$(date +%s)"
      echo -n "Moving: "; mv -v "$sfs" "$old_sfs"
      if test -L "$old_sfs";then
        mv "$sfs_new" "$sfs.$src_stamp"
        echo -n "Linking: "; ln -v -s "${sfs##*/}.$src_stamp" "$sfs"
      else
        echo -n "Moving: "; mv -v "$sfs_new" "$sfs"
      fi
    else
      echo "No update needed."
    fi
  else
    echo "WARN: Could not read: $src_sfs" >&2
  fi
done
