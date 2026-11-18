#!/bin/sh
set -eu

host_key=/etc/ssh/hostkeys/ssh_host_ed25519_key

if [ ! -s /etc/ssh/photo-backup_authorized_keys ]; then
    echo "authorized_keys is missing or empty" >&2
    exit 1
fi

ssh-keygen -l -f /etc/ssh/photo-backup_authorized_keys >/dev/null

if [ ! -d /srv/sftp/upload ]; then
    echo "NVMe upload mount is missing" >&2
    exit 1
fi

if [ "$(stat -c '%u:%g' /srv/sftp)" != "0:0" ]; then
    echo "SFTP chroot must be owned by root" >&2
    exit 1
fi

install -d -o root -g root -m 0700 /etc/ssh/hostkeys
if [ ! -s "$host_key" ]; then
    ssh-keygen -q -t ed25519 -N '' -f "$host_key"
fi
if [ ! -s "${host_key}.pub" ]; then
    ssh-keygen -y -f "$host_key" > "${host_key}.pub"
fi
chmod 0600 "$host_key"
chmod 0644 "${host_key}.pub"

install -d -m 0755 /run/sshd
/usr/sbin/sshd -t -f /etc/ssh/sshd_config
exec /usr/sbin/sshd -D -e -f /etc/ssh/sshd_config
