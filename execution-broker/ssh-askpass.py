#!/usr/local/bin/python3
"""Return one broker-held SSH password to OpenSSH askpass."""

import os
import stat
import sys

path = os.environ.get("HERMES_SSH_PASSWORD_FILE", "")
if not path.startswith("/tmp/hermes-ssh-password-"):
    raise SystemExit(1)
try:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        file_stat = os.fstat(descriptor)
        if (not stat.S_ISREG(file_stat.st_mode) or file_stat.st_uid != os.geteuid()
                or file_stat.st_mode & 0o077):
            raise OSError
        value = os.read(descriptor, 1025)
    finally:
        os.close(descriptor)
except OSError:
    raise SystemExit(1) from None
if not value or len(value) > 1024 or any(byte in value for byte in (0, 10, 13)):
    raise SystemExit(1)
os.write(sys.stdout.fileno(), value + b"\n")
