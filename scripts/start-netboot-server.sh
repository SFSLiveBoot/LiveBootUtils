#!/bin/sh

. "$(dirname "$0")"/common.func

run_as_root "$@"

cd $(mktemp -d $(/var/run/netboot-XXXXXX.d))

service connman stop
killall dhclient3

myip="$(ask_text "My ip/mask [10.1.1.254/24]:")"
test -n "$myip" || myip="10.1.1.254/24"
netrange="$(ask_text "DHCP client range [10.1.1.31,10.1.1.200]:")"
test -n "$netrange" || netrange="10.1.1.31,10.1.1.200"
kver=`uname -r`
arch=`uname -m`

ifconfig eth0 $myip up

mkdir -p boot/grub

cp /usr/lib/grub/i386-pc/* boot/grub
grub-mkimage --format=i386-pc --output core.img --prefix="(pxe)/boot/grub" pxe pxecmd
cat /usr/lib/grub/i386-pc/pxeboot.img core.img > grldr

cat > boot/grub/grub.cfg <<EOF
menuentry "test" {
 linux /cdilive/$arch/vmlinuz-$kver ip=dhcp root=none post_init=smb://%@${myip%/*}/share/netboot.sh
 initrd /cdilive/$arch/ramdisk_net-$kver
}
EOF

cat >smb.conf <<EOF
[share]
path=$PWD
guest ok=yes
EOF

cat >netboot.sh <<"EOF"
#!/bin/sh

mkdir -p /live
mount -o move /mnt/smb* /live

i=0

for sfs in /live/cdilive/*.sfs /live/cdilive/`uname -m`/*.sfs;do
  mkdir -p /parts/$i
  chained_mount "$sfs" /parts/$i
  i=$(($i+1))
done

storage=mem

echo "Storage will be '$storage'"
mkdir -p /parts/$i
chained_mount $storage /parts/$i

union_parts "$chained_parts" $rr

mkdir -p /dev/.initramfs/varrun/network
echo eth0=eth0 > /dev/.initramfs/varrun/network/ifstate

finalize_boot
EOF

mkdir -p cdilive
mount --bind /live/cdilive cdilive

dnsmasq --dhcp-range=$netrange --dhcp-boot=grldr --enable-tftp --tftp-root=$PWD --no-daemon &
smbd -D -F -s smb.conf &

