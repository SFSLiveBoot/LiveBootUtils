#!/bin/sh

set -e
: "${repo_base:=https://github.com/SFSLiveBoot}"

: "${kver:=4.15.4}"
: "${dist:=stretch}"

: "${bootstrap_d:=$(readlink -f "bootstrap.d")}"
: "${build_d:=$(readlink -f "SFSLiveBoot-build.d")}"
: "${build_lst:=$bootstrap_d/build.lst}"
: "${lbu:=$(dirname "$0")}"
: "${bootstrap_files:=$repo_base/00-root-sfs/releases/download/20180122/00-$dist-min.sfs $repo_base/15-settings-sfs/releases/download/20180122/15-settings.sfs $repo_base/20-scripts-sfs/releases/download/20180122/20-scripts.sfs}"

: "${efi_dir:=/usr/lib/grub/x86_64-efi}"

test -n "$SUDO" -o "x$(id -u)" = "x0" || SUDO="sudo"
run() {
  echo "Running:$(for a;do case "$a" in *[^A-Za-z0-9_./=:-]*|"") echo -n " '$a'";;*) echo -n " $a";;esac;done)" >&2
  "$@"
}

echo -n "Testing for aufs: "
if grep -qw aufs /proc/filesystems;then
  echo ok
else
  echo -n "(trying to load module..) "
  if run $SUDO modprobe aufs;then
    echo "ok"
  else
    echo "ERROR: cannot continue: no aufs support"
    exit 1
  fi
fi

echo -n "Testing for git: "
if which git >/dev/null;then
  echo "ok."
else
  run $SUDO env DEBIAN_FRONTEND=noninteractive apt-get -y install git
fi

echo -n "Testing for LiveBootUtils: "
if test -r "$lbu/lbu_cli.py";then
  lbu="$(readlink -f "$lbu")"
else
  test -r "LiveBootUtils/lbu_cli.py" ||
    run git clone --depth=1 $repo_base/LiveBootUtils
  lbu="$(readlink -f "LiveBootUtils")"
fi
if test -d "$lbu/.git";then
  echo -n "Updating LiveBootUtils.."
  (cd "$lbu"; run git pull)
fi

echo -n "Testing for python: "
if PYTHON=$(which python2);then
  $PYTHON -V
else
  run $SUDO env DEBIAN_FRONTEND=noninteractive apt-get -y install python python-crypto
  PYTHON="$(which python2)"
fi

echo -n "Testing for LXC: "
if which lxc-start >/dev/null;then
  lxc-info --version
else
  case "$(lsb_release -is)" in
    Ubuntu) lxc_pkg=lxc1;;
    *) lxc_pkg=lxc;;
  esac
  run $SUDO env DEBIAN_FRONTEND=noninteractive apt-get -y install $lxc_pkg
fi

echo -n "Testing mksquashfs: "
which mksquashfs || {
  run $SUDO env DEBIAN_FRONTEND=noninteractive apt-get -y install squashfs-tools
}

echo -n "Testing for wget: "
if which wget >/dev/null;then
  echo "ok."
else
  run $SUDO env DEBIAN_FRONTEND=noninteractive apt-get -y install wget
fi

echo -n "Testing for grub: "
if test -d "$efi_dir" && which grub-mkrescue >/dev/null && which xorriso >/dev/null && which mcopy >/dev/null;then
  echo "ok."
  output_iso="$(readlink -f "SFSLiveBoot.iso")"
else
  if run $SUDO env DEBIAN_FRONTEND=noninteractive apt-get -y install grub-efi-amd64-bin grub-common xorriso mtools;then
    output_iso="$(readlink -f "SFSLiveBoot.iso")"
  else
    echo "failed, skipping ISO build"
  fi
fi

echo -n "Downloading bootstrap files.. "
run mkdir -p "$bootstrap_d"
(cd "$bootstrap_d"; run wget -c $bootstrap_files)
echo "ok."

if test -s "$build_lst";then
  echo "Using already existing $build_lst"
else
  echo -n "Creating ${build_lst}.. "
  cat >"$build_lst" <<EOF
* $repo_base
00-$dist-gnome.sfs              00-root-sfs.git#$dist
15-settings.sfs                 15-settings-sfs.git
20-scripts.sfs                  20-scripts-sfs.git
40-user.sfs                     40-user-sfs.git
x86_64/10-kernel-${kver}.sfs    10-kernel-srcbuild-sfs/releases/download/v${kver}-1/10-kernel-${kver}.sfs
EOF
  echo "ok."
fi

run cd "$bootstrap_d"
run $SUDO env $(env | grep -i '^[^=]*_proxy=') ${SSH_AUTH_SOCK:+SSH_AUTH_SOCK="$SSH_AUTH_SOCK" }SFS_FIND_PATH="$bootstrap_d" $PYTHON "$lbu/lbu_cli.py" build-boot-dir "$build_d" "$build_lst" linux "$output_iso"
