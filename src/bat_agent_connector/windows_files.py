"""Windows local storage primitives (loaded only on Windows).

Every path component is opened relative to the previous directory handle with
FILE_OPEN_REPARSE_POINT, then checked. Handles deny delete sharing, pinning the
chain until the operation ends. New objects have a protected DACL granting only
the current user and SYSTEM; existing objects are inspected, never adopted by
rewriting their ACL. Artifact publication is an atomic handle-relative rename
without replacement, followed by read-only sealing and digest verification.

API references: Microsoft Learn NtCreateFile (winternl.h), NtSetInformationFile
and FILE_RENAME_INFORMATION (ntifs.h), GetSecurityInfo (aclapi.h), LockFileEx
and SetFileInformationByHandle (fileapi.h).
"""

from __future__ import annotations

import ctypes as c
import errno
import msvcrt
import os
import secrets
import time
from ctypes import wintypes as w
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

kernel = c.WinDLL("kernel32", use_last_error=True)
advapi = c.WinDLL("advapi32", use_last_error=True)
ntdll = c.WinDLL("ntdll", use_last_error=True)
VOID = c.c_void_p
ULONG_PTR = c.c_size_t
ATOMIC_REPLACE_MAX_ATTEMPTS = 6
ATOMIC_REPLACE_DEADLINE_S = 0.5
ATOMIC_REPLACE_RETRY_S = 0.02
PRIVATE_READ_DEADLINE_S = 0.5
PRIVATE_READ_RETRY_S = 0.02


class UnicodeString(c.Structure):
    _fields_ = [("Length", w.USHORT), ("MaximumLength", w.USHORT), ("Buffer", VOID)]


class ObjectAttributes(c.Structure):
    _fields_ = [("Length", w.ULONG), ("RootDirectory", w.HANDLE), ("ObjectName", c.POINTER(UnicodeString)),
                ("Attributes", w.ULONG), ("SecurityDescriptor", VOID), ("SecurityQualityOfService", VOID)]


class IoStatus(c.Structure):
    _fields_ = [("Status", VOID), ("Information", ULONG_PTR)]


class FileInfo(c.Structure):
    _fields_ = [("attributes", w.DWORD), ("created", w.FILETIME), ("accessed", w.FILETIME),
                ("written", w.FILETIME), ("volume", w.DWORD), ("size_high", w.DWORD),
                ("size_low", w.DWORD), ("links", w.DWORD), ("index_high", w.DWORD), ("index_low", w.DWORD)]


class Overlapped(c.Structure):
    _fields_ = [("Internal", ULONG_PTR), ("InternalHigh", ULONG_PTR), ("Offset", w.DWORD),
                ("OffsetHigh", w.DWORD), ("hEvent", w.HANDLE)]


class Acl(c.Structure):
    _fields_ = [("revision", w.BYTE), ("reserved", w.BYTE), ("size", w.WORD),
                ("count", w.WORD), ("reserved2", w.WORD)]


class AceHeader(c.Structure):
    _fields_ = [("kind", w.BYTE), ("flags", w.BYTE), ("size", w.WORD)]


class FileBasicInfo(c.Structure):
    _fields_ = [("creation", c.c_longlong), ("access", c.c_longlong), ("write", c.c_longlong),
                ("change", c.c_longlong), ("attributes", w.DWORD)]


class RenameInfo(c.Structure):
    _fields_ = [("replace", w.BYTE), ("root", w.HANDLE), ("length", w.DWORD), ("name", w.WCHAR * 1)]


def _bind(library, name, args, result=w.BOOL):
    fn = getattr(library, name)
    fn.argtypes, fn.restype = args, result
    return fn


_close = _bind(kernel, "CloseHandle", [w.HANDLE])
_local_free = _bind(kernel, "LocalFree", [VOID], VOID)
_process = _bind(kernel, "GetCurrentProcess", [], w.HANDLE)
_info = _bind(kernel, "GetFileInformationByHandle", [w.HANDLE, c.POINTER(FileInfo)])
_set_info = _bind(kernel, "SetFileInformationByHandle", [w.HANDLE, c.c_int, VOID, w.DWORD])
_flush = _bind(kernel, "FlushFileBuffers", [w.HANDLE])
_lock = _bind(kernel, "LockFileEx", [w.HANDLE, w.DWORD, w.DWORD, w.DWORD, w.DWORD, c.POINTER(Overlapped)])
_unlock = _bind(kernel, "UnlockFileEx", [w.HANDLE, w.DWORD, w.DWORD, w.DWORD, c.POINTER(Overlapped)])
_token = _bind(advapi, "OpenProcessToken", [w.HANDLE, w.DWORD, c.POINTER(w.HANDLE)])
_token_info = _bind(advapi, "GetTokenInformation", [w.HANDLE, c.c_int, VOID, w.DWORD, c.POINTER(w.DWORD)])
_sid_text = _bind(advapi, "ConvertSidToStringSidW", [VOID, c.POINTER(w.LPWSTR)])
_sddl = _bind(advapi, "ConvertStringSecurityDescriptorToSecurityDescriptorW", [w.LPCWSTR, w.DWORD, c.POINTER(VOID), VOID])
_security = _bind(advapi, "GetSecurityInfo", [w.HANDLE, c.c_int, w.DWORD, c.POINTER(VOID), VOID,
                                            c.POINTER(VOID), VOID, c.POINTER(VOID)], w.DWORD)
_ace = _bind(advapi, "GetAce", [VOID, w.DWORD, c.POINTER(VOID)])
_create = _bind(ntdll, "NtCreateFile", [c.POINTER(w.HANDLE), w.DWORD, c.POINTER(ObjectAttributes),
                c.POINTER(IoStatus), VOID, w.ULONG, w.ULONG, w.ULONG, w.ULONG, VOID, w.ULONG], c.c_long)
_nt_set_info = _bind(ntdll, "NtSetInformationFile", [w.HANDLE, c.POINTER(IoStatus), VOID,
                                                    w.ULONG, c.c_int], c.c_long)
_dos_error = _bind(ntdll, "RtlNtStatusToDosError", [c.c_long], w.ULONG)


def _error(code=None):
    raise c.WinError(c.get_last_error() if code is None else code)


def _text_sid(sid):
    value = w.LPWSTR()
    if not _sid_text(sid, c.byref(value)):
        _error()
    try:
        return value.value
    finally:
        _local_free(c.cast(value, VOID))


@lru_cache(maxsize=1)
def current_sid():
    token = w.HANDLE()
    if not _token(_process(), 0x0008, c.byref(token)):  # TOKEN_QUERY
        _error()
    try:
        size = w.DWORD()
        _token_info(token, 1, None, 0, c.byref(size))  # TokenUser
        buffer = c.create_string_buffer(size.value)
        if not _token_info(token, 1, buffer, size, c.byref(size)):
            _error()
        return _text_sid(c.cast(buffer, c.POINTER(VOID))[0])
    finally:
        _close(token)


def _descriptor():
    descriptor = VOID()
    sid = current_sid()
    if not _sddl(f"O:{sid}D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;{sid})", 1, c.byref(descriptor), None):
        _error()
    return descriptor


def check_handle_private(handle):
    owner, dacl, descriptor = VOID(), VOID(), VOID()
    result = _security(handle, 1, 0x00000005, c.byref(owner), None, c.byref(dacl), None, c.byref(descriptor))
    if result:
        _error(result)
    try:
        if not owner or _text_sid(owner) != current_sid() or not dacl:
            raise ValueError("local state must be owned by the current user with a private DACL")
        acl = c.cast(dacl, c.POINTER(Acl)).contents
        grants = set()
        for index in range(acl.count):
            ace = VOID()
            if not _ace(dacl, index, c.byref(ace)):
                _error()
            header = c.cast(ace, c.POINTER(AceHeader)).contents
            # Unknown / conditional / object ACEs cannot establish this contract.
            if header.kind not in (0, 1):
                raise ValueError("private storage contains an unsupported access rule")
            sid = _text_sid(ace.value + 8)
            if header.kind == 0:
                if sid not in {current_sid(), "S-1-5-18"}:
                    raise ValueError("private storage grants access to another principal")
                if not header.flags & 0x08:  # not INHERIT_ONLY_ACE
                    grants.add(sid)
        if current_sid() not in grants:
            raise ValueError("private storage has no current-user access rule")
    finally:
        _local_free(descriptor)


def _component(name):
    if (not name or name in {".", ".."} or any(char in name for char in '\\/:\0*?"<>|')
            or name[-1] in " ."):
        raise ValueError("local storage requires a plain path component")


def _open(name, *, parent=None, directory=False, create=False, exclusive=False,
          writable=False, delete=False, private=True, attributes_only=False):
    if parent is not None:
        _component(name)
    buffer = c.create_unicode_buffer(name)
    encoded_length = len(name.encode("utf-16-le"))
    unicode = UnicodeString(encoded_length, encoded_length + 2, c.cast(buffer, VOID))
    sd = _descriptor() if create else None
    attributes = ObjectAttributes(c.sizeof(ObjectAttributes), parent, c.pointer(unicode), 0x40, sd, None)
    handle, status = w.HANDLE(), IoStatus()
    # A pinned directory needs traverse + attributes, not list-data access.
    # The rename target's internal open must coexist with this directory handle.
    access = (0x001200A0 if directory else 0x00120081)
    access |= (0x00000116 if writable else 0) | (0x00010000 if delete else 0)
    if attributes_only:
        access = 0x00120180  # READ_CONTROL, SYNCHRONIZE, READ/WRITE_ATTRIBUTES
    options = 0x00200000 | 0x20 | (1 if directory else 0x40) | (2 if writable else 0)
    disposition = 2 if exclusive else (3 if create else 1)
    try:
        result = _create(c.byref(handle), access, c.byref(attributes), c.byref(status), None,
                         0x80, 3, disposition, options, None, 0)
    finally:
        if sd:
            _local_free(sd)
    if result < 0:
        _error(_dos_error(result))
    try:
        info = FileInfo()
        if not _info(handle, c.byref(info)):
            _error()
        if info.attributes & 0x400:  # FILE_ATTRIBUTE_REPARSE_POINT (including junctions)
            raise ValueError("local storage cannot contain a reparse point")
        if bool(info.attributes & 0x10) != directory or (not directory and info.links != 1):
            raise ValueError("local storage must be a private directory or single-link regular file")
        if private:
            check_handle_private(handle)
        return handle.value
    except BaseException:
        _close(handle)
        raise


@dataclass
class Directory:
    path: Path
    handles: list

    @property
    def handle(self):
        return self.handles[-1]

    def close(self):
        for handle in reversed(self.handles):
            _close(handle)
        self.handles.clear()


def open_dir(path: Path, *, create=False):
    path = Path(path)
    if not path.is_absolute() or len(path.drive) != 2 or path.drive[1] != ":" or ".." in path.parts:
        raise ValueError("local storage requires an absolute local-drive path")
    handles = []
    try:
        handles.append(_open("\\??\\" + path.anchor, directory=True, private=False))
        for part in path.parts[1:]:
            # System ancestors need not belong to this principal; their handles
            # are still checked for reparses and pinned against rename/delete.
            handles.append(_open(part, parent=handles[-1], directory=True, create=create, private=False))
        return Directory(path, handles)
    except BaseException:
        for handle in reversed(handles):
            _close(handle)
        raise


def open_file(name, flags, mode=0o600, *, parent):
    writable = bool(flags & (os.O_WRONLY | os.O_RDWR))
    deadline = time.monotonic() + PRIVATE_READ_DEADLINE_S
    original_error = None
    while True:
        try:
            handle = _open(name, parent=parent.handle, create=bool(flags & os.O_CREAT),
                           exclusive=bool(flags & os.O_EXCL), writable=writable)
            break
        except OSError as error:
            # A published writer retains DELETE access until flush/close. Keep
            # the pinned parent and all leaf checks; never retry ACL denial (5).
            if flags != os.O_RDONLY or getattr(error, "winerror", None) != 32:
                raise
            if original_error is None:
                original_error = error
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise original_error from None
            time.sleep(min(PRIVATE_READ_RETRY_S, remaining))
            if time.monotonic() >= deadline:
                raise original_error from None
    try:
        fd = msvcrt.open_osfhandle(handle, (os.O_RDWR if flags & os.O_RDWR else os.O_WRONLY if writable else os.O_RDONLY)
                                  | os.O_BINARY | os.O_NOINHERIT)
    except BaseException:
        _close(handle)
        raise
    try:
        if flags & os.O_TRUNC:
            os.ftruncate(fd, 0)
        if flags & os.O_APPEND:
            os.lseek(fd, 0, os.SEEK_END)
        return fd
    except BaseException:
        os.close(fd)
        raise


def check_private(path, *, directory=False):
    if directory:
        parent = open_dir(path)
        try:
            check_handle_private(parent.handle)
        finally:
            parent.close()
    else:
        parent = open_dir(path.parent)
        try:
            _close(_open(path.name, parent=parent.handle))
        finally:
            parent.close()


def lock(fd, *, blocking=True):
    overlapped = Overlapped()
    if not _lock(msvcrt.get_osfhandle(fd), 2 | (0 if blocking else 1), 0, 1, 0, c.byref(overlapped)):
        error = c.get_last_error()
        if error == 33:  # ERROR_LOCK_VIOLATION
            raise BlockingIOError(errno.EAGAIN, "another process holds the central lease")
        _error(error)


def unlock(fd):
    overlapped = Overlapped()
    if not _unlock(msvcrt.get_osfhandle(fd), 0, 1, 0, c.byref(overlapped)):
        _error()


def _rename(handle, destination, name, *, replace=False):
    _component(name)
    encoded = name.encode("utf-16-le")
    size = c.sizeof(RenameInfo) + len(encoded)
    buffer = c.create_string_buffer(size)
    info = c.cast(buffer, c.POINTER(RenameInfo)).contents
    info.replace, info.root, info.length = replace, destination.handle, len(encoded)
    c.memmove(c.addressof(buffer) + RenameInfo.name.offset, encoded, len(encoded))
    # The Win32 FileRenameInfo wrapper rewrites relative names before calling
    # NT, breaking a non-null RootDirectory. Call the native API directly so
    # only the verified directory handle and this single component are used.
    status = IoStatus()
    result = _nt_set_info(handle, c.byref(status), buffer, size, 10)  # FileRenameInformation
    if result < 0:
        _error(_dos_error(result))


def seal_file(parent, name):
    handle = _open(name, parent=parent.handle, attributes_only=True)
    try:
        basic = FileBasicInfo(attributes=0x01)  # FILE_ATTRIBUTE_READONLY
        if not _set_info(handle, 0, c.byref(basic), c.sizeof(basic)):
            _error()
    finally:
        _close(handle)


def publish_file(source, destination, name="content"):
    try:
        handle = _open(name, parent=source.handle, writable=True, delete=True)
    except FileNotFoundError:
        # Recovery after a completed rename: caller verifies existing bytes.
        handle = None
    if handle is not None:
        try:
            if not _flush(handle):
                _error()
            try:
                _rename(handle, destination, name)
            except FileExistsError:
                pass  # never replace an existing immutable revision
        finally:
            _close(handle)
    seal_file(destination, name)


def _delete(handle):
    # Explicit cleanup can remove a sealed artifact without first making its
    # name writable. Ownership/reparse/hard-link checks precede this handle.
    flags = w.ULONG(0x01 | 0x10)  # DELETE | IGNORE_READONLY_ATTRIBUTE
    status = IoStatus()
    result = _nt_set_info(handle, c.byref(status), c.byref(flags), c.sizeof(flags), 64)
    if result < 0:  # FileDispositionInformationEx
        _error(_dos_error(result))


def _atomic_replace(handle, parent, name):
    # A legitimate private reader denies delete sharing. Retry only this
    # replace collision, retaining the already-written temporary file and the
    # pinned directory. Immutable artifact publication never calls this helper.
    deadline = time.monotonic() + ATOMIC_REPLACE_DEADLINE_S
    original_error = None
    for attempt in range(ATOMIC_REPLACE_MAX_ATTEMPTS):
        check_handle_private(parent.handle)
        try:
            _close(_open(name, parent=parent.handle))
        except FileNotFoundError:
            pass
        if original_error is not None and time.monotonic() >= deadline:
            raise original_error
        try:
            return _rename(handle, parent, name, replace=True)
        except OSError as error:
            if getattr(error, "winerror", None) not in {5, 32}:
                raise
            if original_error is None:
                original_error = error
            remaining = deadline - time.monotonic()
            if attempt + 1 == ATOMIC_REPLACE_MAX_ATTEMPTS:
                raise original_error from None
            if remaining > 0:
                time.sleep(min(ATOMIC_REPLACE_RETRY_S, remaining))


def atomic_write(path, data, *, mode=0o600):
    parent = open_dir(path.parent)
    name = path.name + "." + secrets.token_hex(12) + ".tmp"
    handle, published = None, False
    try:
        check_handle_private(parent.handle)
        # Validate an existing target without repairing its ACL or following it.
        try:
            _close(_open(path.name, parent=parent.handle))
        except FileNotFoundError:
            pass
        handle = _open(name, parent=parent.handle, create=True, exclusive=True, writable=True, delete=True)
        # The CRT takes ownership; duplicate so the rename retains its handle.
        fd_handle = w.HANDLE()
        duplicate = _bind(kernel, "DuplicateHandle", [w.HANDLE, w.HANDLE, w.HANDLE, c.POINTER(w.HANDLE), w.DWORD, w.BOOL, w.DWORD])
        if not duplicate(_process(), handle, _process(), c.byref(fd_handle), 0, False, 2):
            _error()
        with os.fdopen(msvcrt.open_osfhandle(fd_handle.value, os.O_WRONLY | os.O_BINARY | os.O_NOINHERIT), "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        _atomic_replace(handle, parent, path.name)
        published = True
        if not _flush(handle):
            _error()
    except BaseException as original_error:
        if handle is not None and not published:
            try:
                _delete(handle)
            except OSError as cleanup_error:
                raise original_error from cleanup_error
        raise
    finally:
        if handle is not None:
            _close(handle)
        parent.close()


def remove_tree(path):
    parent = open_dir(path.parent)
    try:
        _remove_child(parent.handle, path.parent, path.name, directory=True)
    finally:
        parent.close()


def _remove_child(parent, path, name, *, directory):
    handle = _open(name, parent=parent, directory=directory, delete=True)
    try:
        if directory:
            # This directory and every ancestor are pinned against replacement;
            # entry type is only a hint, verified by its relative native open.
            with os.scandir(path / name) as entries:
                for entry in entries:
                    _remove_child(handle, path / name, entry.name, directory=entry.is_dir(follow_symlinks=False))
        _delete(handle)
    finally:
        _close(handle)
