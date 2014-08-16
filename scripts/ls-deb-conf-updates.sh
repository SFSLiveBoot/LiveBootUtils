#!/bin/sh -e

if test "x$1" = "x-q";then
  shift
  quiet="1"
  MD5SUM="$(which md5sum)"
  md5sum() {
    "$MD5SUM" "$@" 2>&1 | grep FAILED | cut -f1 -d:
  }
fi

test -n "$quiet" || echo "Checksum changed:"
awk 'BEGIN {pr=0};/^([A-z]+):/{pr=($1=="Conffiles:")?1:0};/^ /{if(pr)print $1 " "  $2}' /var/lib/dpkg/status | while read conffile dpkg_hash;do
  echo "$dpkg_hash  $conffile"
done | LANG=C md5sum --quiet -c - || true

deb_files=$(mktemp)

find /var/lib/dpkg/info/*.list | xargs grep -hE '^/(etc|bin|usr|lib|sbin)/' | sort -u >$deb_files

test -n "$quiet" || echo "Not in .debs:"
find /etc /bin /usr /lib /sbin -type f -size +1c | grep -xvFf $deb_files

rm $deb_files
