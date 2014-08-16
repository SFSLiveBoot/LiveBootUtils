#!/bin/sh

src="$1"
dst="$2"

test -d "$src" -a -e "$src/prefs.js" || {
  echo "Usage: ${0##*/} <src> <dst>" >&2
  echo "  src - directory containing profile files (like prefs.js)"
  echo "  dst - new profile destination"
  exit 1
}

set -ex

while ! test "x$src" = "x${src%/}";do src="${src%/}"; done

dst="$(readlink -f "$dst")"
test ! -d "$dst" || dst="$dst/${src##*/}"

cd "$src"
find addons.sqlite compatibility.ini localstore.rdf prefs.js extensions extensions.ini extensions.sqlite foxyproxy.xml useragentswitcher jetpack |
  cpio -dvp "$dst"
