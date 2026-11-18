#!/usr/bin/env python3
"""Audit the phone inbox before changing metadata; never follows symlinks.

Run with the SFTP container stopped. The manifest is a record of original
metadata, not a content backup. Only the configured inbox is traversed.
"""
import argparse
import json
import os
from pathlib import Path
import stat
import tempfile


def plan(root, uid, gid):
    root = Path(root)
    if root.is_symlink() or root.resolve() != root.absolute():
        raise ValueError("Inbox and its parents must not be symlinks")
    if not root.is_dir():
        raise ValueError("Inbox must be an existing directory")
    device = root.stat().st_dev
    entries = [root]
    for directory, dirs, files in os.walk(root, followlinks=False):
        entries.extend(Path(directory) / name for name in dirs + files)
    changes = []
    for path in entries:
        info = path.lstat()
        if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
            raise ValueError(f"Unsupported entry (including symlinks): {path}")
        if info.st_dev != device or info.st_uid != uid:
            raise ValueError(f"Unexpected device or owner: {path}; review before migration")
        # Existing executable flags on regular files are retained for the owner.
        mode = (stat.S_IMODE(info.st_mode) & 0o100) | 0o640
        if stat.S_ISDIR(info.st_mode):
            mode = 0o2750  # Setgid keeps new subdirectories in the dedicated group.
        if info.st_gid != gid or stat.S_IMODE(info.st_mode) != mode:
            changes.append({"path": str(path), "uid": info.st_uid,
                            "gid": info.st_gid, "mode": stat.S_IMODE(info.st_mode),
                            "new_gid": gid, "new_mode": mode})
    return changes


def apply(changes, manifest):
    if not changes:
        return
    manifest = Path(manifest)
    manifest.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # A new manifest per migration preserves earlier records after a rerun.
    fd, name = tempfile.mkstemp(prefix="permissions-", suffix=".json", dir=manifest.parent)
    with os.fdopen(fd, "w") as stream:
        json.dump(changes, stream, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    for entry in changes:
        path = Path(entry["path"])
        info = path.lstat()
        if (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) != (
                entry["uid"], entry["gid"], entry["mode"]):
            raise ValueError(f"Metadata changed during migration: {path}")
        os.chown(path, -1, entry["new_gid"], follow_symlinks=False)
        os.chmod(path, entry["new_mode"], follow_symlinks=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root")
    parser.add_argument("uid", type=int)
    parser.add_argument("gid", type=int)
    parser.add_argument("--manifest", default="/var/lib/photo-backup/permissions/latest.json")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    changes = plan(args.root, args.uid, args.gid)
    if args.apply:
        apply(changes, args.manifest)
    print(json.dumps({"changed": bool(changes), "entries": len(changes)}))


if __name__ == "__main__":
    main()
