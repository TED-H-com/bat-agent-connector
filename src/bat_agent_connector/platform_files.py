"""Private local state and kernel leases for the central service.

Remote BAT helper programs retain their own POSIX contracts. These helpers are
only for the machine running central. Windows uses ACLs and pinned no-reparse
handles; chmod and PID files are not substitutes for those checks.
"""

from __future__ import annotations

import errno
import os
import secrets
import stat
from contextlib import suppress
from pathlib import Path

WINDOWS = os.name == "nt"
_OPEN_CREATE_ATTEMPTS = 8
if WINDOWS:
    from . import windows_files as native
else:
    import fcntl


class PrivateFileCreateRaceError(OSError):
    """The private leaf kept changing between lookup and exclusive creation."""


def lock(fd: int, *, blocking: bool = True) -> None:
    if WINDOWS:
        native.lock(fd, blocking=blocking)
    else:
        fcntl.flock(fd, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))


def unlock(fd: int) -> None:
    if WINDOWS:
        native.unlock(fd)
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)


def check_private(path, *, directory=False) -> None:
    path = Path(path)
    if WINDOWS:
        native.check_private(path, directory=directory)
        return
    info = path.lstat()
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if (not expected(info.st_mode) or info.st_uid != os.getuid()
            or info.st_mode & 0o077 or (not directory and info.st_nlink != 1)):
        raise ValueError("local state must be private and owned by the current user")


def ensure_private_directory(path) -> None:
    path = Path(path)
    if WINDOWS:
        handle = native.open_dir(path, create=True)
        try:
            native.check_handle_private(handle.handle)
        finally:
            handle.close()
        return
    fd = _posix_dir(path, create=True)
    try:
        _check_private_stat(os.fstat(fd), directory=True)
    finally:
        os.close(fd)


def _posix_dir(path, *, create=False):
    path = Path(path).absolute()
    if ".." in path.parts:
        raise ValueError("private storage cannot contain parent traversal")
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for name in path.parts[1:]:
            if create:
                with suppress(FileExistsError):
                    os.mkdir(name, 0o700, dir_fd=fd)
                    os.fsync(fd)
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def _check_private_stat(info, *, directory=False):
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if (not expected(info.st_mode) or info.st_uid != os.getuid()
            or info.st_mode & 0o077 or (not directory and info.st_nlink != 1)):
        raise ValueError("local state must be private and owned by the current user")


def _posix_open(parent, name, flags, mode):
    flags = (flags & ~os.O_TRUNC) | os.O_NOFOLLOW
    if not flags & os.O_CREAT or flags & os.O_EXCL:
        return os.open(name, flags, mode, dir_fd=parent)
    # Keep lookup and creation distinct: concurrent first clients must not rely
    # on a filesystem's nonexclusive O_CREAT race handling. Never replace the
    # winner's inode; all lease contenders must lock that same file.
    for _ in range(_OPEN_CREATE_ATTEMPTS):
        try:
            return os.open(name, flags & ~os.O_CREAT, mode, dir_fd=parent)
        except FileNotFoundError:
            try:
                return os.open(name, flags | os.O_EXCL, mode, dir_fd=parent)
            except FileExistsError:
                pass
    raise PrivateFileCreateRaceError(errno.EAGAIN, "private file open/create race did not settle", name)


def open_private_file(path, flags, mode=0o600) -> int:
    path = Path(path)
    if WINDOWS:
        parent = native.open_dir(path.parent)
        try:
            native.check_handle_private(parent.handle)
            return native.open_file(path.name, flags, mode, parent=parent)
        finally:
            parent.close()
    parent = _posix_dir(path.parent)
    try:
        # Validate before a truncating open can damage a link or foreign file.
        fd = _posix_open(parent, path.name, flags, mode)
    finally:
        os.close(parent)
    try:
        _check_private_stat(os.fstat(fd))
        if flags & os.O_TRUNC:
            os.ftruncate(fd, 0)
        return fd
    except BaseException:
        os.close(fd)
        raise


def read_private(path, *, max_bytes=1024 * 1024) -> bytes:
    with os.fdopen(open_private_file(path, os.O_RDONLY), "rb") as stream:
        data = stream.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError("private state exceeds its size limit")
    return data


def sync_directory(path) -> None:
    if WINDOWS:
        # NTFS does not expose directory fsync to an unprivileged application.
        # Native mutations use FILE_WRITE_THROUGH and flush their file handles.
        # Opening the directory still verifies its no-reparse path.
        native.open_dir(Path(path)).close()
        return
    fd = _posix_dir(path)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write(path, data: bytes, *, mode=0o600) -> None:
    path = Path(path)
    ensure_private_directory(path.parent)
    if WINDOWS:
        native.atomic_write(path, data, mode=mode)
        return
    temporary = path.name + "." + secrets.token_hex(12) + ".tmp"
    parent = _posix_dir(path.parent)
    try:
        _check_private_stat(os.fstat(parent), directory=True)
        try:
            _check_private_stat(os.stat(path.name, dir_fd=parent, follow_symlinks=False))
        except FileNotFoundError:
            pass
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode, dir_fd=parent)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path.name, src_dir_fd=parent, dst_dir_fd=parent)
        os.fsync(parent)
    finally:
        with suppress(FileNotFoundError):
            os.unlink(temporary, dir_fd=parent)
        os.close(parent)
