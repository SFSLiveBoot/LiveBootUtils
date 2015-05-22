#!/bin/sh

. "$(dirname "$0")/common.func"

trap_fail

nl="
"
dq='"'

: ${kver:=$(uname -r)}
: ${arch:=$(uname -m)}
: ${mem:=512}
: ${kimg_d:=/boot}
: ${kernel:=$kimg_d/vmlinuz-$kver}
: ${initrd:=$kimg_d/ramdisk_net-$kver}
: ${storage:=mem}

test -n "$1" || {
  echo "Usage: ${0##*/} <parts.sfs..> [-- <kvm_opts..>]" >&2
  exit 1
}

test -r "$kernel" || kernel="/boot/${kernel##*/}"
test -r "$kernel" || { echo "Cannot find kernel $kernel" >&2; exit 1; }
test -r "$initrd" || {
  cat >&2 <<EOF
Initrd '$initrd' not found, you might want to create it using following:

git clone https://github.com/korc/make-ramdisk.git && make -C make-ramdisk KVERS='$kver' NET=1 RAMDISK='$initrd'

EOF
  exit 1
}

if test -n "$tap_iface";then
test -n "$no_run" -o -d "/sys/class/net/$tap_iface" || {
  cat >&2 <<EOF
No interface '$tap_iface' present. Please add with following as root:
 ip tuntap add dev $tap_iface mode tap user $USER && ip link set $tap_iface up && brctl addif br0 $tap_iface
EOF
  exit 1
}
: ${mac_vendor:=02:03:04}
: ${macaddr=$mac_vendor$(echo -n "$@" | md5sum | cut -c-6 | sed -e 's/\(..\)/:\1/g')}
: ${net_opts:=-net nic,model=virtio,macaddr=$macaddr -net tap,ifname=$tap_iface,script=no,downscript=no}
else
: ${net_opts:=-net nic,model=virtio${macaddr:+,macaddr=$macaddr} -net user}
fi

: ${my_ip:=10.0.2.2}
: ${kvm_opts:=-m $mem $net_opts${vga:+ -usbdevice tablet -vga $vga}}
test -n "$vga" || kvm_opts="$kvm_opts -nographic"

test -z "$spice_port" || {
  echo "Enabling spice on port $spice_port, but you need to set password separately from console with set_password" >&2
  kvm_opts="$kvm_opts -spice port=$spice_port,seamless-migration=on -device virtio-serial-pci -device virtserialport,chardev=spicechannel0,name=com.redhat.spice.0 -chardev spicevmc,id=spicechannel0,name=vdagent"
}

: ${nbd_port:=$((8520+$$%100))}
: ${nbd_wrapper:=$(PATH=$PATH:/usr/sbin:/sbin which xnbd-wrapper)}
: ${grub_cfg_src:=$(dirname "$0")/grub.cfg}
: ${mode:=nbd}


case "$mode" in
  nbd)
    nbd_tmpdir=$(mktemp -d /tmp/nbd-$$.XXXXXX)
    nbd_sock=$nbd_tmpdir/ctrl
    autoremove="$nbd_tmpdir${autoremove:+$nl$autoremove}"

    ( cd $nbd_tmpdir; exec $nbd_wrapper --readonly --cow --socket ctrl --port $nbd_port --logpath log ) & nbd_pid=$!
    autoremove="$nbd_tmpdir/ctrl$nl$nbd_tmpdir/log$nl$autoremove"
    extra_append="${extra_append:+$extra_append }ip=dhcp"

    while test -n "$1";do
      sfs="$1"; shift
      test "x$sfs" != "x--" || break
      sfs_bn="${sfs##*/}"
      ln -s "$sfs" "$nbd_tmpdir"
      autoremove="$nbd_tmpdir/$sfs_bn$nl$autoremove"
      ${nbd_wrapper}-ctl -s $nbd_sock -a "$sfs_bn"
      root_parts="${root_parts:-nbd://$my_ip:$nbd_port/}${root_parts:++:}$sfs_bn"
    done
  ;;
  virt9p)
    kvm_opts="$kvm_opts -virtfs local,path=$1,security_model=none,readonly,mount_tag=rootfs"
    root_parts="virt9p://rootfs/./*.sfs"
  ;;
  cdrom)
    if test -z  "$cdrom_iso";then
       cdrom_iso="$(mktemp /tmp/bootable-XXXXXX.iso)"
       autoremove="${autoremove:+$autoremove$nl}$cdrom_iso"
    elif test -e "$cdrom_iso";then
      echo "ERROR: $cdrom_iso already exists, refusing to overwrite" 2>&1
      exit 1
    fi
    cdrom_tmp_d="$(mktemp -d /tmp/iso-XXXXXX.d)"
    : ${dist_base:=$(dirname "$1")}
    : ${dist:=$(basename "$dist_base")}
    : ${cdrom_uuid:=$(date +%Y-%m-%d-%H-%M-%S-00)}
    mkdir -p "$cdrom_tmp_d/boot/grub/x86_64-efi" "$cdrom_tmp_d/boot/grub/i386-pc" "$cdrom_tmp_d/$dist"
    root_parts="/dev/sr0:$dist/*.sfs"
    kernel_dir="$cdrom_tmp_d/boot"
    while test -n "$1";do
      sfs="$1"; shift; test "x$sfs" != "x--" || break
      if test -d "$sfs";then
        while test ! "x$sfs" = "x${sfs%/}"; do sfs="${sfs%/}";done
        ln -s "$(readlink -f "$sfs")" "$cdrom_tmp_d/$dist/${sfs##*/}"
        root_parts="$root_parts+:$dist/${sfs##*/}"
        _extra_sfs="$_extra_sfs+:\$dist/${sfs##*/}"
      else case "$sfs" in
        $arch/*|*/$arch/*)
          kernel_dir="$cdrom_tmp_d/$dist/$arch"
          mkdir -p "$kernel_dir"; ln -s "$sfs" "$kernel_dir"
          case "$root_parts" in
            *'/$arch/*$kver.sfs'*) ;;
            *) root_parts="$root_parts+:$dist/\$arch/*\$kver.sfs";;
          esac
          ;;
        "$dist_base"/*/*)
          sfs_subdir="${sfs#$dist_base/}"
          sfs_subdir="${sfs_subdir%/*}"
          test -d "$cdrom_tmp_d/$dist/$sfs_subdir" || {
            mkdir -p "$cdrom_tmp_d/$dist/$sfs_subdir"
            case " $not_rootpart " in *" $sfs_subdir "*) ;; *)
            _extra_sfs="$_extra_sfs+:\$dist/$sfs_subdir/*.sfs"
            root_parts="$root_parts+:$dist/$sfs_subdir/*.sfs"
            ;; esac
          }
          ln -s "$sfs" "$cdrom_tmp_d/$dist/$sfs_subdir"
          ;;
        *) ln -s "$sfs" "$cdrom_tmp_d/$dist";;
      esac
      fi
    done
    cp "/usr/share/grub/ascii.pf2" "$cdrom_tmp_d/boot/grub"
    echo "source /grub.cfg" >"$cdrom_tmp_d/boot/grub/i386-pc/grub.cfg"
    echo "source /grub.cfg" >"$cdrom_tmp_d/boot/grub/x86_64-efi/grub.cfg"
    echo "source /grub.cfg" >"$cdrom_tmp_d/boot/grub/grub.cfg"
    cat >"$cdrom_tmp_d/grubvars.cfg" <<EOF
set dist="$dist"
set arch="$arch"
set root_uuid="$cdrom_uuid"
${_extra_sfs:+set extra_sfs=$dq$_extra_sfs$dq}
${no_storage_scan:+set no_storage_scan=$no_storage_scan}
EOF
    if test "x$kernel_dir" = "x$cdrom_tmp_d/$dist/$arch" -a -z "${kernel##*/vmlinuz-$kver}" -a -z "${initrd##*/ramdisk*-$kver}";then
      echo "set kver=\"$kver\"" >> "$cdrom_tmp_d/grubvars.cfg"
    else cat >>"$cdrom_tmp_d/grubvars.cfg" <<EOF
set kernel="/${kernel_dir#$cdrom_tmp_d/}/${kernel##*/}"
set initrd="/${kernel_dir#$cdrom_tmp_d/}/${initrd##*/}"
EOF
    fi
    ln -s "$grub_cfg_src" "$cdrom_tmp_d"
    ln -s "$initrd" "$kernel" "$kernel_dir"
    case "$initrd" in */ramdisk_net-*)
      initrd_normal="${initrd%/*}/ramdisk-${initrd#*/ramdisk_net-}"
      test -e "$initrd_normal" || initrd_normal="$(dirname "$(readlink -f "$initrd")")/${initrd_normal##*/}"
      test -e "$initrd_normal" && ln -s "$initrd_normal" "$kernel_dir" || ln -s "$initrd" "$kernel_dir/${initrd_normal##*/}"
    ;; esac
    grub-mkrescue -o "$cdrom_iso" -v "$cdrom_tmp_d" -- -f --modification-date=$(echo $cdrom_uuid | tr -d -)
    test -n "$keep_isodir" && echo "Keeping $cdrom_tmp_d" || rm -r "$cdrom_tmp_d"
    kvm_opts="$kvm_opts -cdrom $cdrom_iso"
  ;;
esac

: ${root:=$root_parts+$storage}
: ${append:=root=$root max_loop=64 quiet${extra_append:+ $extra_append}}
test -n "$vga" || append="$append nox console=ttyS0"

set -x
test -n "$no_run" || if test -n "$kernel";then
  kvm -kernel "$kernel" -initrd "$initrd" -append "$append" $kvm_opts "$@"
else
  kvm $kvm_opts "$@"
fi

test -z "$nbd_pid" || {
  kill $nbd_pid
  wait
}

set +x
IFS="$nl"
test -z "$autoremove" || for f in $autoremove;do
  if test -d "$f";then rmdir -v "$f";
  elif test -e "$f"; then rm -v "$f";
  else echo "$f already removed?";fi
done

exit_succ