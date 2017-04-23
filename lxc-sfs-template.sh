#!/bin/sh

set -e

veth_up_script='iface="$4"; link="$(grep -lFx "$(ethtool -S "$iface" | grep peer_ifindex | tr -dc 0-9)" /sys/class/net/*/ifindex | cut -f5 -d/)"; ethtool -K "$link" tx-checksum-ip-generic off'
: ${def_parts:=00-*.sfs settings scripts lxc-server}

eval set -- "$(getopt -o p:n:h -l path:,name:,rootfs:,mapped-uid:,mapped-gid:,mac-vlan:,mac-vlan-ip:,mac-vlan-gw:,mac-veth:,mac-veth-ip:,mac-veth-gw:,default-parts:,veth-up-script:,bind:,bind-ro:,host-network -- "$@")"

gen_vlan_config() {
  local link ip gw
  IFS="," read link ip gw <<EOF
$*
EOF
  grep -v '^$' <<EOF
lxc.network.type = macvlan
lxc.network.macvlan.mode = bridge
lxc.network.flags = up
lxc.network.link = $link
${ip:+lxc.network.ipv4 = $ip}
${gw:+lxc.network.ipv4.gateway = $gw}
EOF
}

gen_veth_config() {
  local link ip gw
  IFS="," read link ip gw <<EOF
$*
EOF
  grep -v '^$' <<EOF
lxc.network.type = veth
lxc.network.flags = up
${link:+lxc.network.link = $link}
${ip:+lxc.network.ipv4 = $ip}
${gw:+lxc.network.ipv4.gateway = $gw}
lxc.network.script.up = /bin/sh -c '$veth_up_script'
EOF
}

_nl="
"

while true;do
  case "$1" in
    -n|--name) name="$2";shift;;
    --rootfs) rootfs="$2";shift;;
    -p|--path) path="$2";shift;;
    --mac-vlan)
      extra_config="${extra_config:+$extra_config$_nl}${_nl}$(gen_vlan_config "$2")"
      shift
    ;;
    --mac-veth)
      extra_config="${extra_config:+$extra_config$_nl}${_nl}$(gen_veth_config "$2")"
      shift
    ;;
    --bind|--bind-ro)
      case "$1" in *-ro) _opts="ro";; *) _opts="";; esac
      _entry="lxc.mount.entry = $(echo "${2%=*}" | sed -e 's/ /\\040/g') ${2##*=} none bind${_opts:+,$_opts} 0 0"
      extra_config="${extra_config:+$extra_config$_nl}${_nl}$_entry${_nl}"
      shift
    ;;
    --bind-ro)
      extra_config="${extra_config:+$extra_config$_nl}${_nl}$(gen_mount_entry "${2%=*}" "${2##*=}" ro)"
      shift
    ;;
    --host-network) extra_config="${extra_config:+$extra_config$_nl}${_nl}lxc.network.type=none${_nl}";;
    --mac-veth-ip) mac_veth_ip="$2";shift;;
    --mac-veth-gw) mac_veth_gw="$2";shift;;
    --default-parts) def_parts="$2";shift;;
    --veth-up-script) veth_up_script="$2";shift;;
    -h)
      cat <<EOF
${0##*/} [<options>] [<extra_parts..>]
Options:
    --veth-up-script="<script..>"
    --mac-vlan=<host_iface>[,<ip>[,<gw>]]
    --mac-veth=[<host_bridge>][,<ip>[,<gw>]]
    --default-parts="<part1.sfs> .."
    --host-network
    {--bind|--bind-ro}="<src>=<dst>"
EOF
      exit 0
    ;;
    --) shift; break;;
    *) break;;
  esac
  shift
done

mkdir -p "${path:?require path to LXC container}"
cat >"$path/config" <<EOF

lxc.utsname = $name
lxc.rootfs = $rootfs
lxc.pts = 1024
lxc.kmsg = 0

lxc.loglevel = 1
lxc.autodev = 1
lxc.mount.auto = proc sys
lxc.hook.pre-mount = /etc/lxc/mount-sfs.sh $def_parts $@ --

# use .drop instead of .keep if you want less restritive environment
#lxc.cap.drop = sys_module mac_admin mac_override sys_time
lxc.cap.keep = sys_chroot sys_admin dac_override chown fowner kill ipc_owner ipc_lock setgid setuid sys_nice syslog lease dac_read_search audit_write setpcap net_bind_service sys_resource net_broadcast net_admin net_raw

lxc.cgroup.devices.deny = a
lxc.cgroup.devices.allow = c 1:8 r
lxc.cgroup.devices.allow = c 1:9 r
lxc.cgroup.devices.allow = c 1:5 r
lxc.cgroup.devices.allow = c 1:3 rw
lxc.cgroup.devices.allow = c 1:7 rw
lxc.cgroup.devices.allow = c 5:0 rw
lxc.cgroup.devices.allow = c 136:* rw
$extra_config
EOF
