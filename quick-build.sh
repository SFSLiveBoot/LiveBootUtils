#!/bin/sh

set -e
: "${repo_base:=https://github.com/SFSLiveBoot}"

: "${kver:=4.15.4}"
: "${dist:=buster}"

: "${bootstrap_d:=$(readlink -f "bootstrap.d")}"
: "${build_d:=$(readlink -f "SFSLiveBoot-build.d")}"
: "${build_lst:=$bootstrap_d/build.lst}"
: "${lbu:=$(dirname "$0")}"
: "${output_iso:=$(readlink -f "SFSLiveBoot.iso")}"
: "${bootstrap_base:=$repo_base/LiveBootUtils/releases/download/${dist}-1.0}"
: "${bootstrap_files:=$bootstrap_base/00-${dist}-min.sfs $bootstrap_base/15-settings.sfs $bootstrap_base/20-scripts.sfs}"

test -n "$SUDO" -o "x$(id -u)" = "x0" || SUDO="sudo"
run() {
  echo "Running:$(for a in "$@";do case "$a" in *[^A-Za-z0-9_./=:-]*|"") echo -n " '$a'";;*) echo -n " $a";;esac;done)" >&2
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
    Ubuntu)
      case "$(lsb_release -rs)" in
        14.*) lxc_pkg=lxc lxc_repo=trusty-backports;;
        *) lxc_pkg=lxc1;;
      esac;;
    *) lxc_pkg=lxc;;
  esac
  run $SUDO env DEBIAN_FRONTEND=noninteractive apt-get ${lxc_repo:+-t $lxc_repo} -y install $lxc_pkg
fi

echo -n "Testing mksquashfs: "
which mksquashfs || {
  run $SUDO env DEBIAN_FRONTEND=noninteractive apt-get -y install squashfs-tools
}

echo -n "Testing for curl: "
which curl || {
  no_curl=1
  echo -n "Testing for wget: "
  which wget || no_wget=1
}

test -z "$no_wget" -o -z "$no_curl" || {
  run $SUDO env DEBIAN_FRONTEND=noninteractive apt-get -y install curl
  no_curl=""
}

echo -n "Downloading bootstrap files.. "
run mkdir -p "$bootstrap_d"
for bs_url in $bootstrap_files;do
  bs_dest="$bootstrap_d/${bs_url##*/}"
  echo -n "Testing bootstrap file: $bs_dest .. "
  if test -e "$bs_dest";then echo "ok."; else
    echo "downloading from $bs_url"
    if test -n "$no_curl";then
      wget -O "$bs_dest.dl-temp" -c "$bs_url" && mv -v "$bs_dest.dl-temp" "$bs_dest"
    else
      curl -o "$bs_dest.dl-temp" -L -C - "$bs_url" && mv -v "$bs_dest.dl-temp" "$bs_dest"
    fi
  fi
done
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
IFS="
"
run $SUDO env $(env | grep -i -e '^[^=]*_proxy=' -e '^lxc_' -e '^lbu_' -e '^ssh_auth_sock=') SFS_FIND_PATH="$bootstrap_d" \
  $PYTHON "$lbu/lbu_cli.py" build-boot-dir "$build_d" "$build_lst" linux "$output_iso"
