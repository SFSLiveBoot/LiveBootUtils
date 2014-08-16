#!/bin/sh

. "$(dirname "$0")/scripts/common.func"

trap_fail

wd="$HOME/netboot"
boot_src="${1:-/live/cdilive}"

dnsmasq_conf="$wd/dnsmasq.conf"
test -e "$wd/boot/grub/i386-pc/core.0" || grub-mknetdir --net-directory "$wd"

test -e "$dnsmasq_conf" || {
  iface=$(select_one "Network interface:" $(ip -o a | awk '/inet /{print $2 "@" $4}' | grep -v '^lo@'))
  my_ip="${iface#*@}"
  my_mask="${my_ip#*/}"
  my_ip="${my_ip%/*}"
  my_net="${my_ip%.*}"
  iface="${iface%%@*}"
  cat >"$dnsmasq_conf" <<EOF
#
# dnsmasq settings file. execution example:
#  sudo dnsmasq -d -8 /dev/null -q -C $dnsmasq_conf
#

# change this if you want to run on some other NICs
interface=$iface

# replace with actual client MAC and IP addresses
#dhcp-host=11:22:33:44:55:66,192.168.0.60

# ignore hosts not in dhcp-host or /etc/ethers
#dhcp-ignore=tag:!known

# serve IP only PXE clients, others must be handled by real DHCP server
#dhcp-ignore=tag:!pxe

# allocate IP from range, with optional netmask
dhcp-range=$my_net.10,$my_net.100,255.255.255.0

# OR serve only known hosts using dhcp-host
#dhcp-range=$my_ip,static

# OR provide only PXE menu, let the real DHCP server to handle IP's
# (grub will probably fail, because doesn't reuse server IP address)
#dhcp-range=$my_ip,proxy

# set server ip
dhcp-option=option:server-ip-address,$my_ip

# set router address
#dhcp-option=option:router,$my_net.1

# disable built-in dns server and set another
#port=0
#dhcp-option=option:dns-server,$my_net.1

# domain name for clients
#domain=domain1.com

# additional DNS search domains
#dhcp-option=option:domain-search,domain1.jp,domain2.co.jp

# wins server
#dhcp-option=option:netbios-ns,0.0.0.0

dhcp-boot=tag:pxe,/boot/grub/i386-pc/core.0,tftp-server,$my_ip
dhcp-vendorclass=set:pxe,PXEClient
dhcp-vendorclass=set:pxe,Etherboot

pxe-prompt="$my_ip: Press F8 for boot menu", 5
pxe-service=x86PC, "Netboot", boot/grub/i386-pc/core
pxe-service=x86PC, "Netboot from $my_ip", boot/grub/i386-pc/core, $my_ip
pxe-service=x86PC, "Boot from local disk"

bind-interfaces
except-interface=lo
enable-tftp
tftp-root=$wd
EOF
}

test -e "$wd/boot/grub/grub.cfg" || {
  rd_file="$(select_one "Choose net-bootable ramdisk" "$boot_src"/*/ramdisk_net-*)"
  test -e "$rd_file" || {
    show_info "No valid ramdisk found"
    exit 1
  }
  test -n "$my_ip" || my_ip=$(grep -o "server-ip-address,.*" "$dnsmasq_conf" | cut -f2 -d,)
  kver="${rd_file##*ramdisk_net-}"
  arch="${rd_file%/*}"
  arch="${arch##*/}"
  for f in "$boot_src"/*;do
    case "$f" in *.pf2|*.png|*/$arch) ;; *) continue;; esac
    test -e "$wd/boot/${f##*/}" || ln -s "$f" "$wd/boot"
  done
  cat >"$wd/boot/grub/grub.cfg" <<EOF
insmod font
if loadfont /boot/font.pf2;then
  insmod vbe
  insmod gfxterm
  set gfxmode=640x480x16
  if terminal_output gfxterm;then
    insmod png
    background_image -m stretch /boot/logo.png
  fi
fi

set timeout=10

menuentry "Netboot [$arch/$kver]" {
  echo -n "Loading kernel.."
  linux /boot/$arch/vmlinuz-$kver ip=dhcp root=nfs://$my_ip$boot_src/*.sfs+:$arch/*.sfs+mem max_loop=64
  echo -n "initrd.."
  initrd /boot/$arch/ramdisk_net-$kver
  echo "booting."
}
EOF
}

if test -z "$DISPLAY" ; then editor "$dnsmasq_conf"
else gedit "$dnsmasq_conf";fi

rpcinfo -p >/dev/null || sudo service portmap start
showmount -e >/dev/null || sudo service nfs-kernel-server start
showmount -e | grep -q "$boot_src" || sudo exportfs -iv -o ro,insecure,all_squash "*":"$boot_src"

if ask_yesno "Run dnsmasq server";then
  sudo dnsmasq -d -8 /dev/null -q -C $dnsmasq_conf
fi

exit_succ
