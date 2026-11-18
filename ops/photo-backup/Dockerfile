FROM debian:bookworm-slim

ARG PHOTO_BACKUP_UID=10001
ARG PHOTO_BACKUP_GID=10001

RUN set -eux; \
    apt-get update; \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        iproute2 \
        openssh-server \
        openssl \
        passwd; \
    rm -rf /var/lib/apt/lists/*; \
    groupadd --gid "${PHOTO_BACKUP_GID}" photo-backup; \
    password="$(od -An -N32 -tx1 /dev/urandom | tr -d ' \n')"; \
    password_hash="$(printf '%s' "$password" | openssl passwd -6 -stdin)"; \
    useradd --uid "${PHOTO_BACKUP_UID}" --gid "${PHOTO_BACKUP_GID}" \
        --home-dir /upload --no-create-home --shell /usr/sbin/nologin \
        --password "$password_hash" photo-backup; \
    unset password password_hash; \
    install -d -o root -g root -m 0755 /srv/sftp

COPY sshd_config /etc/ssh/sshd_config
COPY entrypoint.sh /usr/local/sbin/photo-backup-entrypoint

RUN set -eux; \
    chmod 0755 /usr/local/sbin/photo-backup-entrypoint; \
    chmod 0644 /etc/ssh/sshd_config; \
    install -d -m 0755 /run/sshd /etc/ssh/hostkeys; \
    ssh-keygen -q -t ed25519 -N '' -f /etc/ssh/hostkeys/ssh_host_ed25519_key; \
    /usr/sbin/sshd -t -f /etc/ssh/sshd_config; \
    rm -f /etc/ssh/hostkeys/ssh_host_ed25519_key*

ARG PHOTO_BACKUP_BUILD_ID=manual
LABEL homelab.photo-backup.build-id="${PHOTO_BACKUP_BUILD_ID}"

EXPOSE 22

ENTRYPOINT ["/usr/local/sbin/photo-backup-entrypoint"]
