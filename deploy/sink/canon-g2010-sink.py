#!/usr/bin/env python3
"""canon-g2010-sink — raw JetDirect (TCP 9100) receiver -> /dev/usb/lp0.

The Linux half of the "Windows driver does native manual duplex" design: the
Canon Windows driver (which already implements the manual-duplex popup/refeed
cycle) streams its raw inline-BJRaster3 bytes to this socket; this sink writes
them verbatim to the printer's USB character device. No Wine, no PE, no format
translation here — the transport is a byte pipe.

Safety properties
-----------------
* Single-writer mutex: the SAME lock the live-test harness uses,
  /var/tmp/canon-live-test.lock (flock LOCK_EX|LOCK_NB). If the lock is held we
  refuse the job instead of interleaving bytes with another writer.
* One connection at a time (serialized accept loop).
* Bounded job size (--max-bytes) and idle timeout (--idle-timeout) so a stuck
  client can never wedge the service.
* Dry-run mode (--dry-run): writes the received stream to a file and NEVER opens
  the device. Used for offline web-check.
* Clean SIGTERM/SIGINT shutdown; the lock is released on every exit path.
* The device is opened O_WRONLY|O_NOCTTY, written in chunks, then fsync'd.

Exit is only ever triggered by a signal; the service is meant to run under
systemd (see deploy/systemd/canon-g2010-sink.service).
"""
from __future__ import annotations

import argparse
import errno
import fcntl
import hashlib
import os
import select
import signal
import socket
import sys
import time

_stop = False


def _log(msg: str) -> None:
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def _on_signal(signum, _frame):
    global _stop
    _stop = True
    _log(f"signal {signum}: shutting down")


class Lock:
    """flock-based single-writer mutex, compatible with `flock(1)` tooling."""

    def __init__(self, path: str):
        self.path = path
        self.fd = None

    def acquire(self) -> bool:
        # The shared lock file may be root-owned and not writable by us; flock(2)
        # works on a read-only descriptor, so fall back to O_RDONLY.
        flags = None
        for f in (os.O_RDWR | os.O_CREAT, os.O_RDONLY):
            try:
                self.fd = os.open(self.path, f, 0o666)
                flags = f
                break
            except OSError as e:
                if f == (os.O_RDWR | os.O_CREAT) and e.errno in (errno.EACCES, errno.EPERM, errno.EROFS):
                    continue
                raise
        if self.fd is None:
            return False
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            os.close(self.fd)
            self.fd = None
            return False

    def release(self) -> None:
        if self.fd is not None:
            try:
                fcntl.flock(self.fd, fcntl.LOCK_UN)
            finally:
                os.close(self.fd)
                self.fd = None


def _recv_job(conn: socket.socket, max_bytes: int, idle_timeout: float) -> bytes:
    """Read one job: until the peer closes, until the idle timeout, or the cap."""
    buf = bytearray()
    conn.setblocking(False)
    last = time.monotonic()
    while not _stop:
        r, _, _ = select.select([conn], [], [], 1.0)
        if r:
            try:
                chunk = conn.recv(65536)
            except OSError as e:
                if e.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                    continue
                raise
            if not chunk:                      # clean EOF = end of job
                break
            buf += chunk
            last = time.monotonic()
            if len(buf) >= max_bytes:
                _log(f"job reached max-bytes cap {max_bytes}, truncating read")
                break
        elif time.monotonic() - last > idle_timeout:
            _log(f"idle for {idle_timeout:.0f}s, treating as end of job")
            break
    return bytes(buf)


def _deliver(device: str, data: bytes) -> None:
    fd = os.open(device, os.O_WRONLY | os.O_NOCTTY)
    try:
        off = 0
        while off < len(data):
            off += os.write(fd, data[off:off + 65536])
        # The printer is a USB character device; fsync(2) is meaningless there
        # and returns EINVAL/ENOTSUP on many kernels. Treat it as best-effort so
        # a completed write is never reported as a delivery failure.
        try:
            os.fsync(fd)
        except OSError as e:
            if e.errno not in (errno.EINVAL, errno.ENOTSUP, errno.ENOSYS, errno.EROFS):
                raise
    finally:
        os.close(fd)


def _deliver_dryrun(dirpath: str, data: bytes, digest: str) -> str:
    os.makedirs(dirpath, exist_ok=True)
    out = os.path.join(dirpath, f"job-{time.strftime('%Y%m%d-%H%M%S')}-{digest[:12]}.prn")
    with open(out, "wb") as fh:
        fh.write(data)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--listen", default="0.0.0.0", help="bind address (default 0.0.0.0)")
    ap.add_argument("--port", type=int, default=9100, help="bind port (default 9100)")
    ap.add_argument("--device", default="/dev/usb/lp0", help="printer char device")
    ap.add_argument("--lock", default="/var/tmp/canon-live-test.lock",
                    help="single-writer flock path (shared with the live-test harness)")
    ap.add_argument("--max-bytes", type=int, default=64 * 1024 * 1024,
                    help="per-job cap (default 64 MiB)")
    ap.add_argument("--idle-timeout", type=float, default=8.0,
                    help="seconds of silence that end a job (default 8)")
    ap.add_argument("--dry-run", action="store_true",
                    help="write received jobs to --dry-run-dir instead of the device")
    ap.add_argument("--dry-run-dir", default="/var/tmp/canon-g2010-dryrun",
                    help="dry-run output directory")
    args = ap.parse_args()

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    mode = "DRY-RUN -> " + args.dry_run_dir if args.dry_run else "DEVICE " + args.device
    _log(f"canon-g2010-sink starting: bind {args.listen}:{args.port}, {mode}, lock {args.lock}")

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((args.listen, args.port))
    srv.listen(1)
    srv.settimeout(1.0)
    _log("listening")

    while not _stop:
        try:
            conn, peer = srv.accept()
        except socket.timeout:
            continue
        except OSError as e:
            if _stop:
                break
            _log(f"accept error: {e}")
            continue
        _log(f"connection from {peer[0]}:{peer[1]}")
        lock = Lock(args.lock)
        try:
            if not lock.acquire():
                _log("REFUSED: lock held by another writer; dropping connection")
                conn.close()
                continue
            try:
                data = _recv_job(conn, args.max_bytes, args.idle_timeout)
                digest = hashlib.sha256(data).hexdigest()
                if not data:
                    _log("empty job, nothing to deliver")
                elif args.dry_run:
                    out = _deliver_dryrun(args.dry_run_dir, data, digest)
                    _log(f"dry-run wrote {len(data)} bytes -> {out} sha256={digest}")
                else:
                    _deliver(args.device, data)
                    _log(f"delivered {len(data)} bytes to {args.device} sha256={digest}")
            except OSError as e:
                _log(f"delivery error: {e}")
            finally:
                lock.release()
                try:
                    conn.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                conn.close()
        except Exception as e:  # never let one bad job kill the service
            _log(f"unexpected error handling job: {e}")
            try:
                conn.close()
            except Exception:
                pass

    srv.close()
    _log("stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
