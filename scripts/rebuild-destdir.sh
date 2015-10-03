#!/bin/sh

test -n "$DESTDIR" || { echo "Error: DESTDIR not set" >&2; exit 1; }

: ${ctrl_d:=$DESTDIR/usr/src/sfs.d}

set -e

for fname in $(cat "$ctrl_d/.rebuild.lst");do
  echo -n "Removing: $fname .. " >&2
  if test -L "$fname" -o -e "$fname";then
    rm -r "$DESTDIR/$fname"
    echo "ok." >&2
  else
    echo "not existing, skipping." >&2
  fi
done

for f in $(find "$ctrl_d" -name "[0-9][0-9]-*" | sort -n);do
  echo "Running: ${f#$ctrl_d/} .. " >&2
  "$f"
  exit_st="$?"
  echo "Done: ${f#$ctrl_d/}" >&2
  test "x$exit_st" = "x0" || { echo "Failed with $exit_st, bailing out." >&2; exit $exit_st; }
done