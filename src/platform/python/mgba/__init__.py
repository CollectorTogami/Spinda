# Copyright (c) 2013-2017 Jeffrey Pfau
#
# Original mGBA source is credited to Jeffrey Pfau and contributors.
# Local custom modifications in this fork were added for this workspace and
# are not upstream mGBA work or authored by Jeffrey Pfau.
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
import glob
import os
import sys
import sysconfig


def _add_windows_dll_dirs():
    if os.name != 'nt' or not hasattr(os, 'add_dll_directory'):
        return

    seen = set()

    def _add(path):
        if not path:
            return
        path = os.path.abspath(path)
        if path in seen or not os.path.isdir(path):
            return
        seen.add(path)
        try:
            os.add_dll_directory(path)
        except OSError:
            pass

    pkgdir = os.path.dirname(__file__)
    repo_root = os.path.abspath(os.path.join(pkgdir, '..', '..', '..', '..'))

    # When the package is imported from the local workspace, prefer the nearby
    # built DLLs so the host-side scripts and the visible Qt build resolve the
    # same libmgba binary.
    _add(pkgdir)
    _add(os.path.dirname(sys.executable))
    _add(os.path.join(sys.base_prefix, 'bin'))

    build_dirs = []
    for candidate in glob.glob(os.path.join(repo_root, 'build*')):
        if os.path.isfile(os.path.join(candidate, 'libmgba.dll')):
            build_dirs.append(os.path.abspath(candidate))

    # Prefer Python-capable builds and, within each class, prefer the most
    # recently updated build by adding it last.
    build_dirs.sort(key=os.path.getmtime)
    for candidate in build_dirs:
        if not os.path.isfile(os.path.join(candidate, 'libmgba-pylib.dll')):
            _add(candidate)
    for candidate in build_dirs:
        if os.path.isfile(os.path.join(candidate, 'libmgba-pylib.dll')):
            _add(candidate)

    _add(os.environ.get('BINDIR'))


def _windows_runtime_mismatch_message():
    """Return a clear import error when a MinGW binding meets an MSC Python.

    The maintained Qt/Python build in this workspace is produced with the
    MinGW CPython runtime from ``<repo-root>\\.venv-mgba\\bin\\python.exe``. If the
    package is imported from the stock ``C:\\Python312\\python.exe`` interpreter,
    `_pylib` can load but then crash inside the wrong Python runtime DLL during
    module initialization. Detect that specific mismatch here and fail with a
    normal ImportError instead of a native access violation.
    """

    if os.name != 'nt':
        return None

    ext_suffix = (sysconfig.get_config_var('EXT_SUFFIX') or '').lower()
    using_mingw_python = 'mingw' in ext_suffix or 'gcc' in sys.version.lower()
    if using_mingw_python:
        return None

    pkgdir = os.path.dirname(__file__)
    repo_root = os.path.abspath(os.path.join(pkgdir, '..', '..', '..', '..'))
    mingw_binding_dirs = []
    for candidate in glob.glob(os.path.join(repo_root, 'build*')):
        if not os.path.isfile(os.path.join(candidate, 'libmgba-pylib.dll')):
            continue
        if glob.glob(os.path.join(candidate, 'libpython3*.dll')):
            mingw_binding_dirs.append(os.path.abspath(candidate))

    if not mingw_binding_dirs:
        return None

    recommended = os.path.join(repo_root, '.venv-mgba', 'bin', 'python.exe')
    return (
        "The local mGBA Python bindings in this workspace were built against a"
        " MinGW CPython runtime, but the current interpreter is a stock Windows"
        f" Python: {sys.executable}. Importing mgba from this interpreter will"
        " crash in libpython3.12.dll. Use the workspace MinGW interpreter"
        f" instead: {recommended}"
    )


_add_windows_dll_dirs()

_runtime_mismatch = _windows_runtime_mismatch_message()
if _runtime_mismatch:
    raise ImportError(_runtime_mismatch)

from ._pylib import ffi, lib  # pylint: disable=no-name-in-module


class Git:
    commit = None
    if lib.gitCommit and lib.gitCommit != "(unknown)":
        commit = ffi.string(lib.gitCommit).decode('utf-8')

    commitShort = None
    if lib.gitCommitShort and lib.gitCommitShort != "(unknown)":
        commitShort = ffi.string(lib.gitCommitShort).decode('utf-8')

    branch = None
    if lib.gitBranch and lib.gitBranch != "(unknown)":
        branch = ffi.string(lib.gitBranch).decode('utf-8')

    revision = None
    if lib.gitRevision > 0:
        revision = lib.gitRevision


def create_callback(struct_name, cb_name, func_name=None):
    func_name = func_name or "_py{}{}".format(struct_name, cb_name[0].upper() + cb_name[1:])
    full_struct = "struct {}*".format(struct_name)

    def callback(handle, *args):
        handle = ffi.cast(full_struct, handle)
        return getattr(ffi.from_handle(handle.pyobj), cb_name)(*args)

    return ffi.def_extern(name=func_name)(callback)


__version__ = ffi.string(lib.projectVersion).decode('utf-8')
