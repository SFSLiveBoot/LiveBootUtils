#!/bin/sh

set -eu

: "${lbu:=/opt/LiveBootUtils}"
: "${lbu_cli:=$lbu/lbu_cli.py}"
: "${slb_repo:=https://github.com/SFSLiveBoot}"
: "${dist:=$(lsb_release -cs)}"
: "${root_repo:=$slb_repo/00-root-sfs.git#$dist}"
: "${wd:=$PWD}"

r() { (set -x; "$@") }

test $# -ge 1 || {
  echo "Usage: ${0##*/} <output.sfs>" >&2
  exit 1
}

output_sfs="$1"
shift

tmp_d="$(mktemp -d "$wd/tmp-XXX")"
trap 'st="$?";echo Clean up with: sudo rm -r "${tmp_d#$wd/}" >&2;exit "$st"' EXIT INT
r sudo install -d -o root -g root -m 0755 "$tmp_d" "$tmp_d/rw" "$tmp_d/dst" "$wd/cache"

# shellcheck disable=SC2086
r sudo LBU_CACHE_DIR="$wd/cache" LXC_DESTDIR="$tmp_d/rw" LXC_RW_D="$tmp_d/dst" $lbu_cli rebuild-sfs "$output_sfs" "$root_repo" "$@"
r sudo rm -rf "$tmp_d"
trap - EXIT INT
