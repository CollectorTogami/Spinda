# Copyright (c) 2013-2016 Jeffrey Pfau
#
# Original mGBA source is credited to Jeffrey Pfau and contributors.
# Local custom modifications in this fork were added for this workspace and
# are not upstream mGBA work or authored by Jeffrey Pfau.
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
# pylint: disable=invalid-name,unused-argument
from ._pylib import ffi, lib  # pylint: disable=no-name-in-module
import os


@ffi.def_extern()
def _vfpClose(vf):
    vfp = ffi.cast("struct VFilePy*", vf)
    ffi.from_handle(vfp.fileobj).close()
    return True


@ffi.def_extern()
def _vfpSeek(vf, offset, whence):
    vfp = ffi.cast("struct VFilePy*", vf)
    f = ffi.from_handle(vfp.fileobj)
    f.seek(offset, whence)
    return f.tell()


@ffi.def_extern()
def _vfpRead(vf, buffer, size):
    vfp = ffi.cast("struct VFilePy*", vf)
    pybuf = ffi.buffer(buffer, size)
    ffi.from_handle(vfp.fileobj).readinto(pybuf)
    return size


@ffi.def_extern()
def _vfpWrite(vf, buffer, size):
    vfp = ffi.cast("struct VFilePy*", vf)
    pybuf = ffi.buffer(buffer, size)
    ffi.from_handle(vfp.fileobj).write(pybuf)
    return size


@ffi.def_extern()
def _vfpMap(vf, size, flags):
    pass


@ffi.def_extern()
def _vfpUnmap(vf, memory, size):
    pass


@ffi.def_extern()
def _vfpTruncate(vf, size):
    vfp = ffi.cast("struct VFilePy*", vf)
    ffi.from_handle(vfp.fileobj).truncate(size)


@ffi.def_extern()
def _vfpSize(vf):
    vfp = ffi.cast("struct VFilePy*", vf)
    f = ffi.from_handle(vfp.fileobj)
    pos = f.tell()
    f.seek(0, os.SEEK_END)
    size = f.tell()
    f.seek(pos, os.SEEK_SET)
    return size


@ffi.def_extern()
def _vfpSync(vf, buffer, size):
    vfp = ffi.cast("struct VFilePy*", vf)
    f = ffi.from_handle(vfp.fileobj)
    if buffer and size:
        pos = f.tell()
        f.seek(0, os.SEEK_SET)
        res = _vfpWrite(vf, buffer, size)
        f.seek(pos, os.SEEK_SET)
        return res == size
    f.flush()
    os.fsync()
    return True


def open(f):  # pylint: disable=redefined-builtin
    handle = ffi.new_handle(f)
    vf = VFile(lib.VFileFromPython(handle), _no_gc=(f, handle))
    return vf


def open_path(path, mode="r"):
    if not mode:
        return None

    # Match the normal Python file mode letters closely enough for the local
    # savestate helpers. In particular, `w+` and `x` need real read/write and
    # create semantics instead of the older read-only shortcut.
    base_mode = mode[0]
    update = "+" in mode[1:]
    access = os.O_RDWR if update else os.O_WRONLY

    if base_mode == "r":
        flags = os.O_RDWR if update else os.O_RDONLY
    elif base_mode == "w":
        flags = access | os.O_CREAT | os.O_TRUNC
    elif base_mode == "a":
        flags = access | os.O_CREAT | os.O_APPEND
    elif base_mode == "x":
        flags = access | os.O_CREAT | os.O_EXCL
    else:
        return None

    vf = lib.VFileOpen(path.encode("UTF-8"), flags)
    if vf == ffi.NULL:
        return None
    return VFile(vf)


class VFile:
    def __init__(self, vf, _no_gc=None, _borrowed=False):
        self.handle = vf
        self._no_gc = _no_gc
        # Some CFFI callbacks receive a VFile* that is still owned by the C++
        # caller. Mark those wrappers as borrowed so Python can read from them
        # without closing the handle before the C++ VFileDevice goes out of
        # scope. Closing borrowed handles caused nondeterministic double-close
        # crashes after Qt startup scripts returned.
        self._borrowed = _borrowed
        self._claimed = False

    @staticmethod
    def fromEmpty():
        return VFile(lib.VFileMemChunk(ffi.NULL, 0))

    def __del__(self):
        if not self._claimed and not self._borrowed:
            self.close()

    def close(self):
        if self._borrowed:
            self._claimed = True
            return False
        if self._claimed:
            return False
        self._claimed = True
        return bool(self.handle.close(self.handle))

    def seek(self, offset, whence):
        return self.handle.seek(self.handle, offset, whence)

    def read(self, buffer, size):
        return self.handle.read(self.handle, buffer, size)

    def read_all(self, size=0):
        if not size:
            size = self.size()
        buffer = ffi.new("char[%i]" % size)
        size = self.handle.read(self.handle, buffer, size)
        return ffi.unpack(buffer, size)

    def readline(self, buffer, size):
        return self.handle.readline(self.handle, buffer, size)

    def write(self, buffer, size):
        return self.handle.write(self.handle, buffer, size)

    def map(self, size, flags):
        return self.handle.map(self.handle, size, flags)

    def unmap(self, memory, size):
        self.handle.unmap(self.handle, memory, size)

    def truncate(self, size):
        self.handle.truncate(self.handle, size)

    def size(self):
        return self.handle.size(self.handle)

    def sync(self, buffer, size):
        return self.handle.sync(self.handle, buffer, size)
