#!/bin/sh

: ${cache_dir:=$1}
: ${cache_dir:=$(dirname "$(readlink -f "$0")")}

while test -n "$cache_dir" -a -z "${cache_dir##*/}";do cache_dir="${cache_dir%/}"; done

: ${apt_conf:=/etc/apt/apt.conf.d/90cache}
: ${sources_list:=/etc/apt/sources.list.d/${cache_dir##*/}.list}
: ${cache_archives:=$cache_dir/archives}
: ${cache_lists:=$cache_dir/lists}

case "$1" in
  -h)
    echo "Usage: ${0##*/} [<cache_dir>=$cache_dir]" >&2
    exit 1
  ;;
esac

dq='"'
set -xe

test -d "$cache_archives" || cache_archives=""
test -d "$cache_lists" || cache_lists=""

test -e "$apt_conf" || test -z "$cache_archives" -a -z "$cache_lists" -a -z "$http_proxy" || cat >"$apt_conf" <<EOF
Dir {
  ${cache_archives:+Cache::Archives $dq$cache_dir/archives$dq;}
  ${cache_lists:+State::Lists $dq$cache_dir/lists$dq;}
};
Acquire::Languages "en";
${http_proxy:+Acquire::HTTP::Proxy $dq$http_proxy$dq;}
EOF

 
test ! -e "$cache_dir/deb/Packages" -o -e "$sources_list" ||
  echo "deb file://$cache_dir/deb ./" >"$sources_list"
