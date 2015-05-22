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

replace_sfs() {
  local src="$1" dst="$2" uid gid mode new_tgt
  test ! -d "$dst" || { dst="$dst/${src##*/}"; dst="${dst%.sfs*}.sfs" ; }
  if test -e "$dst";then
    local src_stamp="$(sfs_stamp "$src")" dst_stamp="$(sfs_stamp "$dst")"
    local src_dtime="$(date --date=@$src_stamp +%y%m%d_%H%M%S)"
    local dst_dtime="$(date --date=@$dst_stamp +%y%m%d_%H%M%S)"
    uid="$(stat -Lc %u "$dst")" gid="$(stat -Lc %g "$dst")" mode="$(stat -Lc %a "$dst")"
    chown "$uid" "$src" || true
    chgrp "$gid" "$src" || true
    chmod "$mode" "$src" || true
    if test -L "$dst";then
      new_tgt="$dst.$src_stamp"
      echo -n "Re-linking $dst ($dst_dtime) to ${new_tgt##*/} ($src_dtime) .. "
      mv "$src" "$new_tgt"
      mv "$dst" "$dst.OLD.$(date +%s)"
      ln -s "${new_tgt##*/}" "$dst"
      echo "Done."
    else
      echo -n "Replacing $dst ($dst_dtime) with new version ($src_dtime).. "
      mv "$dst" "$dst.OLD.$(date +%s)"
      mv "$src" "$dst"
      echo "Done."
    fi
  else
    mv -v "$src" "$dst"
  fi
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
      replace_sfs "$sfs_new" "$sfs"
    else
      echo "No update needed."
    fi
  else
    echo "WARN: Could not read: $src_sfs" >&2
  fi
done
