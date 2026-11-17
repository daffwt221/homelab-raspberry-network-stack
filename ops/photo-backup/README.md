# Photo intake: review and deployment

This endpoint receives phone uploads via key-only SFTP; it is not a gallery.
Code changes do not constitute a successful deployment or an independent backup.

## Storage and recovered ownership

Install collections with `ansible-galaxy collection install -r collections/requirements.yml`.
Set `nvme_uuid` in the ignored `group_vars/all.yml` after checking `sudo blkid`.
The example contains this homelab's recovered SSD UUID. Provisioning mounts that
existing ext4 filesystem and persists it in `/etc/fstab`; it never formats it.
An occupied unmounted directory or a different mounted filesystem stops deployment.
Docker requires the NVMe mount even though `nofail` allows the host to boot without it.

The recovered phone inbox belongs to UID 994. GID 989 now belongs to `spi` on the
new OS, so the default dedicated `photo-backup` group is GID 10001. IDs are explicit;
provisioning refuses IDs owned by another account or changes to an existing account's IDs.
Review the selected IDs before deploying on another host.

Only `/mnt/nvme/photos/inbox/poco-x7-pro` is migrated. The entire inbox is audited
before changes: symlinks, special files, another filesystem or unexpected owners stop
migration. Run it with intake stopped. Group changes and restrictive modes preserve
file contents and UID; original metadata is recorded in root-only manifests under
`/var/lib/photo-backup/permissions/`. These manifests are not file-content backups.
The Takeout archives and other directories are excluded. Existing group read access
is replaced by the dedicated photo group; owner executable flags are retained.
The stack user joins that group (log out/in to refresh membership). Parent directories
receive only a group traversal ACL. Inbox files are group-readable; directories use
setgid so new uploads inherit the dedicated group. SMB access uses the configured
SMB user's normal Unix permissions; this does not create or expose an SMB share.

Use the default inbox path unless the ownership and parent traversal rules are adapted.
The account's numeric UID must match the recovered files; do not select an arbitrary
UID and recursively chown the whole photo collection.

## Phone key and deployment

An existing authorized_keys file is retained. When absent, the recovered public-key
file is restored if available; on a fresh host set `photo_backup_public_key` to the
phone's PUBLIC key. The private key stays on the phone. Missing/empty/unparseable
keys stop deployment; the image entrypoint also checks the public-key file.
The container's independent SSH host key lives under the NVMe Docker data directory.
Compare its fingerprint with the phone prompt before accepting it:

```sh
sudo ssh-keygen -lf /mnt/nvme/docker/photo-backup/hostkeys/ssh_host_ed25519_key.pub
```

After review, with only the intake container stopped if metadata changes are needed:

```sh
sudo ansible-playbook -i inventory.ini playbook.yml --tags photo-backup
```

An image label records a hash of all build inputs and UID/GID. An unchanged existing
image is reused; a missing image rebuilds. This does not automatically refresh the
Debian base image: request an explicit rebuild when updating it.

## Independent copy (requires another disk)

The old backup on D: is a snapshot; it does not cover new uploads. The optional daily
copy is DISABLED until `photo_backup_secondary_enabled: true` and both
`photo_backup_secondary_mount` and `photo_backup_secondary_dir` are configured.
The destination must be strictly inside a mounted filesystem different from the
source. The helper refuses missing mounts, symlinks, nested device mismatches and
overlapping paths; it never mounts, formats or deletes source data.
Provision and persist the separate disk's mount yourself before enabling this feature.
Remote/NAS transport and mounting are outside this helper's scope.

The timer copies only phone inbox files to `current/`, keeps deleted source files,
and retains overwritten versions in `versions/<UTC timestamp>/`. It does not back up
Takeout, the OS or the rest of the stack. Rsync compares checksums on each run, which
costs disk I/O on the Pi 2. Copies are not atomic snapshots: avoid uploading/changing
files during the copy, and keep originals until a completed copy is verified. There
is no automatic version expiry; monitor free space. `last-success.json` records only
a successful rsync run, not a restore test. This protects against loss of the NVMe,
but a permanently attached writable disk is not an offline backup.

Inspect `systemctl status photo-backup-secondary.timer` and
`journalctl -u photo-backup-secondary.service`. Trigger the first copy manually after
review with `sudo systemctl start photo-backup-secondary.service`, then restore a
sample to a temporary location and compare SHA256. Disabling the feature stops the
timer; an already-running copy is allowed to finish.

## Verification

```sh
python3 -m unittest discover -s ops/photo-backup/tests -v
# Isolated fixtures; requires a working Docker daemon and root for fixture ownership:
docker build -t homelab/photo-backup-sftp:test ops/photo-backup
sudo python3 ops/photo-backup/test_sftp.py --image homelab/photo-backup-sftp:test
```

The integration test creates its own temporary uploads, keys, random localhost port
and uniquely named container; it never mounts the real inbox or changes its container.
It checks SHA256 after recreation, host-key persistence, unknown-key rejection,
chroot isolation, blocked shell commands and effective password policy. CI runs it
on the runner's native architecture and separately builds ARMv7. An ARMv7 build alone
does not prove transfers work on the physical Pi.

On the phone, configure FolderSync: SFTP, port 2222, user `photo-backup`, private key,
remote `/upload`, upload-only, no delete/move or two-way sync. Start with one test
photo and compare its SHA256 after download. Keep originals until an independent
copy and a restore test succeed. Do not recreate the real container during active uploads.
