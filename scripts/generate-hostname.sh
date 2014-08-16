#!/bin/sh

uuid_file="/sys/devices/virtual/dmi/id/product_uuid"
test -s "$uuid_file" || uuid_file=""

HOSTNAME="$(/usr/bin/pwgen ${uuid_file:+-H$uuid_file} -0 -A 6 1)"
test -n "$HOSTNAME" || exit 1

echo "$HOSTNAME">/etc/hostname
grep -qwF "$HOSTNAME" /etc/hosts || echo "127.0.1.1 $HOSTNAME" >>/etc/hosts
hostname "$HOSTNAME"
