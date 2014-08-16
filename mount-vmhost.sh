#!/bin/sh

. "$(dirname "$0")/scripts/common.func"
run_as_root "$@"
trap_fail

dst=/vmhost

install_vmware_modules() {
  local tool_ver=$(dpkg -s open-vm-tools | awk '/^Version: /{gsub(/-.*/,"");print $2}')
  if test-e /var/lib/dkms/open-vm-tools/$tool_ver;then
    dkms remove -m open-vm-tools -v $tool_ver --all >&2
  fi
  dkms add -m open-vm-tools -v $tool_ver >&2
  dkms build -m open-vm-tools -v $tool_ver >&2
  dkms install -m open-vm-tools -v $tool_ver >&2
  service open-vm-tools restart >&2
}

vmware-checkvm || {
  show_info "This script works only when running under VMWare."
  exit 0
}

modinfo vmhgfs || install_vmware_modules | zenity --progress --pulsate --auto-close --no-cancel --text="Installing kernel modules.."

test -d "$dst" || mkdir -p "$dst"
if msg="$(mount -t vmhgfs .host:/ "$dst" 2>&1)";then
  show_info "Mounted at $dst"
else
  show_info "Mount failed: $msg. Shared folders disabled in Virtual Machine settings?"
  exit 1
fi

exit_succ
