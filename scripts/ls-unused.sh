#!/bin/sh

for fname;do
  if fuser -s "$fname" ;then continue;fi
  in_use=""
  for lf in /sys/block/loop*/loop/backing_file;do
    read lf <$lf
    if test "$lf" -ef "$fname";then in_use=1;break;fi
  done
  for sl in "$@";do
    if test -L "$sl" && test "$sl" -ef "$fname";then in_use=1;break;fi
  done
  test -z "$in_use" || continue
  echo "$fname"
done
