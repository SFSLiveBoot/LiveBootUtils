#!/bin/sh -e

. "$(dirname "$0")/scripts/common.func"
run_as_root "$@"

for keytype in rsa dsa;do
  keyfile="/etc/ssh/ssh_host_${keytype}_key"
  test -e "$keyfile" || ssh-keygen -t $keytype -N '' -f "$keyfile"
done

test -d /var/run/sshd || mkdir -p /var/run/sshd

if ! grep -iq "PasswordAuthentication no" /etc/ssh/sshd_config;then
  echo "Password auth enabled *NOT RECOMMENDED*"
  echo "(add a ssh identity to \$HOME/.ssh/authorized_keys)"
  echo -n "Disable password logins now? [Y/n]"
  read answ && answ="${answ:-yes}"

  case "$answ" in
    y*|Y*) ed /etc/ssh/sshd_config <<EOF
g/^PasswordAuthentication/s/^.*/#&/
2a
PasswordAuthentication no
.
wq
EOF
    ;;
    *) echo "Please be sure to change the default passwords";;
  esac
fi

if ps huww -C sshd;then cmd=restart ; else cmd=start;fi
set -x
service ssh $cmd
