#!/bin/sh

set -e
: "${repo_base:=https://github.com/SFSLiveBoot}"

: "${kver:=4.15.4}"
: "${dist:=bullseye}"
: "${root_name:=$dist-gnome}"
: "${main_sfs:=15-settings 20-scripts 40-user}"
: "${sfs_dir:=linux}"

: "${bootstrap_d:=$(readlink -f "bootstrap.d")}"
: "${build_d:=$(readlink -f "SFSLiveBoot-build.d")}"
: "${build_lst:=$bootstrap_d/build.lst}"
: "${lbu:=$(dirname "$0")}"
: "${output_iso:=$(readlink -f "SFSLiveBoot.iso")}"
: "${bootstrap_base:=$repo_base/LiveBootUtils/releases/download/${dist}-1.0}"
: "${bootstrap_files:=$bootstrap_base/00-${dist}-min.sfs $bootstrap_base/15-settings.sfs $bootstrap_base/20-scripts.sfs}"

test -n "$SUDO" -o "x$(id -u)" = "x0" || SUDO="sudo"
run() {
  (set -x; exec "$@")
}

dl_file() {
  local url="$1" dst="$2"
  if test -n "$no_curl";then
    wget -O "$dst" -c "$url"
  else
    curl -sSfL -o "$dst" -C - "$url"
  fi
}

inst_pkg() {
  run $SUDO env DEBIAN_FRONTEND=noninteractive apt-get -y install "$@"
}

echo -n "Testing for aufs/overlay: "
if grep -qw -e aufs -e overlay /proc/filesystems;then
  echo ok
else
  echo -n "(trying to load module..) "
  if run $SUDO modprobe aufs;then
    echo "ok (aufs)"
  elif run $SUDO modprobe overlay;then
    echo "ok (overlay)"
  else
    echo "ERROR: cannot continue: no aufs/overlay support"
    exit 1
  fi
fi

echo -n "Testing for git: "
if which git >/dev/null;then
  echo "ok."
else
  inst_pkg git
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

test -n "$PYTHON" || {
  echo -n "Testing for python: "
  if PYTHON=$(which python2);then
    $PYTHON -V
  else
    inst_pkg python2
    PYTHON="$(which python2)"
  fi
}

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
  inst_pkg ${lxc_repo:+-t $lxc_repo} $lxc_pkg
fi

echo -n "Testing mksquashfs: "
which mksquashfs || inst_pkg squashfs-tools

echo -n "Testing for curl: "
which curl || {
  no_curl=1
  echo -n "Testing for wget: "
  which wget || no_wget=1
}

test -z "$no_wget" -o -z "$no_curl" || {
  inst_pkg curl
  no_curl=""
}

echo -n "Downloading bootstrap files.. "
run mkdir -p "$bootstrap_d"
for bs_url in $bootstrap_files;do
  bs_dest="$bootstrap_d/${bs_url##*/}"
  echo -n "Testing bootstrap file: $bs_dest .. "
  if test -e "$bs_dest";then echo "ok."; else
    echo "downloading from $bs_url"
    if dl_file "$bs_url" "$bs_dest.dl-temp";then
      mv -v "$bs_dest.dl-temp" "$bs_dest"
    else
      case "$bs_dest" in
        */00-*)
          root_tmp="$($SUDO mktemp -d -t $dist-root.XXXX)"
          $SUDO which debootstrap >/dev/null || inst_pkg debootstrap
          run $SUDO debootstrap "$dist" "$root_tmp"
          run $SUDO mksquashfs "$root_tmp" "$bs_dest"
          run $SUDO rm -rf "$root_tmp"
        ;;
        *)
          run $SUDO $PYTHON "$lbu/lbu_cli.py" rebuild-sfs "$bs_dest" "$repo_base/$(basename "$bs_url" .sfs)-sfs.git"
        ;;
      esac
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
${root_pkgs:+pkgs=$root_pkgs
stage2_pkgs=}
00-$root_name.sfs              00-root-sfs.git#$dist
$(for sfs in $main_sfs;do echo $sfs.sfs $sfs-sfs.git;done)
${add_sfs:+$add_sfs}
x86_64/10-kernel-${kver}.sfs    10-kernel-srcbuild-sfs/releases/download/v${kver}-1/10-kernel-${kver}.sfs
EOF
  echo "ok."
fi

cd "$bootstrap_d"
IFS="
"
run $SUDO env $(env | grep -i -e '^[^=]*_proxy=' -e '^lxc_' -e '^lbu_' -e '^ssh_auth_sock=' -e '^boot_') SFS_FIND_PATH="$bootstrap_d" \
  $PYTHON "$lbu/lbu_cli.py" build-boot-dir "$build_d" "$build_lst" "$sfs_dir" "$output_iso"
