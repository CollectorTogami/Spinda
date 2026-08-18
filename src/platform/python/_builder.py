import cffi
import os, os.path
import re
import shlex
import subprocess
import sys

ffi = cffi.FFI()
pydir = os.path.dirname(os.path.abspath(__file__))
srcdir = os.path.join(pydir, "..", "..")
incdir = os.path.join(pydir, "..", "..", "..", "include")
bindir = os.environ.get("BINDIR", os.path.join(os.getcwd(), ".."))
libdir = os.environ.get("LIBDIR")


def _strip_paren_directive(line, token):
    while True:
        start = line.find(token)
        if start < 0:
            return line

        pos = start + len(token)
        while pos < len(line) and line[pos].isspace():
            pos += 1

        if pos >= len(line) or line[pos] != '(':
            line = line[:start] + line[pos:]
            continue

        depth = 0
        end = pos
        while end < len(line):
            ch = line[end]
            if ch == '(':
                depth += 1
            elif ch == ')':
                depth -= 1
                if depth == 0:
                    end += 1
                    break
            end += 1

        line = line[:start] + line[end:]


def _directive_needs_continuation(line, token):
    start = line.find(token)
    while start >= 0:
        pos = start + len(token)
        while pos < len(line) and line[pos].isspace():
            pos += 1

        if pos >= len(line) or line[pos] != '(':
            start = line.find(token, pos)
            continue

        depth = 0
        while pos < len(line):
            ch = line[pos]
            if ch == '(':
                depth += 1
            elif ch == ')':
                depth -= 1
                if depth == 0:
                    break
            pos += 1

        if depth > 0:
            return True
        start = line.find(token, pos)

    return False


def _normalize_array_bounds(line):
    special_bounds = {
        'PATH_MAX': '260',
        'GBTAMA5_MAX': '8',
        'GBTAMA6_RTC_MAX': '16',
        'GB_SIZE_IO': '128',
        'GB_SIZE_HRAM': '127',
        'GB_VIDEO_MAX_OBJ': '40',
        'GB_VIDEO_MAX_OBJ*4': '160',
    }

    def _replace(match):
        expr = match.group(1).strip()
        expr_simple = re.sub(r'[\s()]', '', expr)
        if expr_simple in special_bounds:
            return '[{}]'.format(special_bounds[expr_simple])
        if re.fullmatch(r'(?:0x[0-9A-Fa-f]+|\d+)', expr_simple):
            return '[{}]'.format(expr_simple)
        return '[1]'

    return re.sub(r'\[([^\]]+)\]', _replace, line)


def _sanitize_cdef_line(line):
    # GCC/MinGW injects builtin varargs typedefs that pycparser cannot parse,
    # but mGBA's Python bindings do not need them.
    if "__gnuc_va_list" in line:
        return None
    if "nullptr_t" in line:
        return None
    if "__asm__" in line or "__volatile__" in line:
        return None
    if re.match(r'^(if|else|switch|case|default|for|while)\b', line):
        return None
    if re.match(r'^(return|break|continue)\b', line):
        return None
    if line.startswith('__builtin_'):
        return None
    # These Qt session helpers are embedding-only callbacks. If they leak into
    # the normal cdef surface, cffi exposes them as regular lib.* callables
    # instead of pure ``extern "Python"`` hooks, which prevents
    # ``ffi.def_extern()`` from binding them when the scripting window starts.
    if re.search(r'\bmPythonSessionRun(?:File|Code)\b', line):
        return None
    if re.search(r'\bmPythonSessionReset\b', line):
        return None
    line = _strip_paren_directive(line, '__attribute__')
    line = _strip_paren_directive(line, '__declspec')
    line = re.sub(r'\b__cdecl__\b', '', line)
    line = re.sub(r'\b__extension__\b', '', line)
    line = re.sub(r'\b__const__\b', 'const', line)
    line = re.sub(r'\b__inline__\b', 'inline', line)
    line = re.sub(r'\b__inline\b', 'inline', line)
    line = re.sub(r'\b__forceinline\b', 'inline', line)
    line = re.sub(r'\b__restrict__\b', '', line)
    line = re.sub(r'\b__restrict\b', '', line)
    line = re.sub(r'\b__signed__\b', 'signed', line)
    line = re.sub(r'\b_Float16\b', 'unsigned short', line)
    line = re.sub(r'\b__bf16\b', 'unsigned short', line)
    line = re.sub(r'\(\s*(?:unsigned\s+)?(?:char|short|int|long|long long|__int64|DWORD|WORD|BYTE|UINT\d*|INT\d*|LONG|ULONG|LONG64|DWORD64|UINT|INT|BOOLEAN|BOOL)\s*\)\s*(-?(?:0x[0-9A-Fa-f]+|\d+))', r'\1', line)
    line = _normalize_array_bounds(line)
    line = re.sub(r'\s+', ' ', line).strip()
    if '__C_ASSERT__' in line:
        return None
    if re.match(r'^(?:extern|static)\s+(?:inline|__inline(?:__)?|void|char|short|int|long|float|double|signed|unsigned)\s*$', line):
        return None
    if re.match(r'^(?:(?:void|int|long|short|double|float|char|wchar_t|errno_t|size_t|ssize_t|intptr_t|uintptr_t|ptrdiff_t)(?:\s+(?:long|short|int|double))*(?:\s+const)?(?:\s+volatile)?|(?:unsigned|signed)(?:\s+(?:char|short|int|long|long long))?)\s*(?:\*+)?\s*;$', line):
        return None
    if '(*' not in line and re.match(r'^[_A-Za-z]\w*\s*\([^;]*\)\s*;$', line):
        return None
    return line or None


def _collect_cdef_lines(preprocessed):
    # The Windows/MinGW headers fed through pycparser contain a lot of inline
    # helpers and compiler directives that are irrelevant to live scripting.
    # Trim them down to a stable API surface the bindings can actually ingest.
    lines = []
    skip_inline = False
    saw_inline_body = False
    brace_depth = 0

    raw_lines = preprocessed.splitlines()
    i = 0

    while i < len(raw_lines):
        line = raw_lines[i]
        i += 1
        line = line.strip()
        if line.startswith('#'):
            continue

        while i < len(raw_lines) and (
            _directive_needs_continuation(line, '__attribute__')
            or _directive_needs_continuation(line, '__declspec')
        ):
            line = '{} {}'.format(line, raw_lines[i].strip())
            i += 1

        detect_line = _strip_paren_directive(line, '__attribute__')
        detect_line = _strip_paren_directive(detect_line, '__declspec')
        detect_line = re.sub(r'\s+', ' ', detect_line).strip()

        if skip_inline:
            brace_depth += line.count('{')
            brace_depth -= line.count('}')
            if '{' in line:
                saw_inline_body = True
            if saw_inline_body and brace_depth <= 0:
                skip_inline = False
                saw_inline_body = False
            continue

        if re.match(r'^(?:extern|static)?\s*(?:__inline(?:__)?|inline)\b', detect_line):
            saw_inline_body = '{' in line
            brace_depth = line.count('{') - line.count('}')
            skip_inline = not (saw_inline_body and brace_depth <= 0)
            continue

        if line.endswith('{') and '(' in line and not line.startswith(('typedef', 'struct ', 'union ', 'enum ')):
            skip_inline = True
            brace_depth = line.count('{') - line.count('}')
            continue

        line = _sanitize_cdef_line(line)
        if not line:
            continue
        lines.append(line)

    return lines

cpp = shlex.split(os.environ.get("CPP", "cc -E"))
cppflags = shlex.split(os.environ.get("CPPFLAGS", ""))
cppflags.extend(["-I" + incdir, "-I" + srcdir, "-I" + bindir])

set_source_kwargs = dict(
    include_dirs=[incdir, srcdir],
    extra_compile_args=cppflags,
    libraries=["mgba"],
    library_dirs=[bindir],
    # qt.c is part of the local visible-Qt scripting bridge.
    sources=[os.path.join(pydir, path) for path in ["vfs-py.c", "core.c", "log.c", "qt.c", "sio.c"]],
)
if libdir and os.name != 'nt':
    set_source_kwargs["runtime_library_dirs"] = [libdir]

ffi.set_source("mgba._pylib", """
#define static
#define inline
#define MGBA_EXPORT
#include <mgba/flags.h>
#define OPAQUE_THREADING
#include <mgba/core/blip_buf.h>
#include <mgba/core/cache-set.h>
#include <mgba-util/common.h>
#include <mgba/core/core.h>
#include <mgba/core/map-cache.h>
#include <mgba/core/log.h>
#include <mgba/core/mem-search.h>
#include <mgba/core/thread.h>
#include <mgba/core/version.h>
#include <mgba/debugger/debugger.h>
#include <mgba/gba/interface.h>
#include <mgba/internal/arm/arm.h>
#include <mgba/internal/debugger/cli-debugger.h>
#include <mgba/internal/gba/gba.h>
#include <mgba/internal/gba/input.h>
#include <mgba/internal/gba/renderers/cache-set.h>
#include <mgba/internal/sm83/sm83.h>
#include <mgba/internal/gb/gb.h>
#include <mgba/internal/gb/renderers/cache-set.h>
#include <mgba-util/png-io.h>
#include <mgba-util/vfs.h>

#define PYEXPORT
#include "platform/python/core.h"
#include "platform/python/log.h"
#include "platform/python/qt.h"
#include "platform/python/sio.h"
#include "platform/python/vfs-py.h"
#undef PYEXPORT
""", **set_source_kwargs)

preprocessed = subprocess.check_output(cpp + ["-fno-inline", "-P"] + cppflags + [os.path.join(pydir, "_builder.h")], universal_newlines=True)

ffi.cdef('\n'.join(_collect_cdef_lines(preprocessed)), override=True)

ffi.embedding_api("""
struct VFile;
struct mDebugger;
struct mDebuggerEntryInfo;

bool mPythonLoadScript(const char*, struct VFile*);
bool mPythonRunScript(const char*, struct VFile*);
void mPythonRunPending(void);
bool mPythonLookupSymbol(const char* name, int32_t* out);
bool mPythonSessionRunFile(uint64_t sessionKey, const char* name, struct VFile*);
bool mPythonSessionRunCode(uint64_t sessionKey, const char* name, const char* code);
void mPythonSessionReset(uint64_t sessionKey);
void mPythonSetDebugger(struct mDebugger*);
void mPythonDebuggerEntered(int reason, struct mDebuggerEntryInfo*);
""")

ffi.embedding_init_code("""
    import glob
    import os, os.path
    import sys
    from mgba._pylib import ffi, lib

    def _prepend_sys_path(path):
        if path and os.path.isdir(path) and path not in sys.path:
            sys.path.insert(0, path)

    def _append_sys_path(path):
        if path and os.path.isdir(path) and path not in sys.path:
            sys.path.append(path)

    def _debug_trace(message):
        trace_path = os.environ.get("MGBA_PYTHON_TRACE")
        if not trace_path:
            return
        with open(trace_path, "a", encoding="utf-8") as handle:
            handle.write(str(message))
            handle.write("\\n")

    exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    # Embedded Qt runs do not start with the same sys.path as the host-side
    # venv. Put the freshly built package first so its generated `_pylib`
    # matches the current CFFI extern table, then add the in-tree sources as a
    # fallback for pure-Python modules and local example scripts.
    #
    # When the configured interpreter is a dedicated project venv, also fold in
    # its site-packages path explicitly. PYTHONHOME still points at the base
    # interpreter for stdlib startup, so this keeps third-party packages aligned
    # with the exact venv we built against.
    venv_site_packages = os.environ.get("MGBA_PYTHON_SITE_PACKAGES")
    if venv_site_packages:
        _prepend_sys_path(venv_site_packages)
    for candidate in glob.glob(os.path.join(exe_dir, "python", "lib.*")):
        _prepend_sys_path(candidate)
    _append_sys_path(os.path.abspath(os.path.join(exe_dir, "src", "platform", "python")))
    _append_sys_path(os.path.abspath(os.path.join(exe_dir, "..", "src", "platform", "python")))

    symbols = {}
    globalSyms = {
        'symbols': symbols
    }
    pendingCode = []
    qtSessions = {}

    def _qt_bridge_bound():
        return hasattr(lib, "mPythonQtIsBound") and lib.mPythonQtIsBound()

    def _emit_qt_console(text):
        if not _qt_bridge_bound() or not hasattr(lib, "mPythonQtConsoleWrite"):
            return
        lib.mPythonQtConsoleWrite(str(text).encode("utf-8"))

    def _script_globals(name):
        # Direct Qt startup scripts need normal Python script semantics so they
        # can use __file__ to find lg.gba, savestates, and other sibling assets.
        script_globals = dict(globalSyms)
        script_globals.update({
            '__name__': '__main__',
            '__file__': name,
            '__package__': None,
            '__cached__': None,
            '__builtins__': __builtins__,
        })
        return script_globals

    def _sync_shared_globals(script_globals):
        script_globals['symbols'] = symbols
        if 'debugger' in globalSyms:
            script_globals['debugger'] = globalSyms['debugger']
        else:
            script_globals.pop('debugger', None)
        return script_globals

    def _session_globals(session_key, name=None):
        session_key = int(session_key)
        script_globals = qtSessions.get(session_key)
        if script_globals is None:
            script_globals = _script_globals(name or '<qt-python>')
            qtSessions[session_key] = script_globals
        if name:
            script_globals['__file__'] = name
        return _sync_shared_globals(script_globals)

    def _report_script_exception():
        import traceback
        traceback_text = traceback.format_exc()
        if _qt_bridge_bound():
            lib.mPythonQtLog(2, traceback_text.encode("utf-8"))
            _emit_qt_console(traceback_text)
        else:
            traceback.print_exc()
        return traceback_text

    class _QtLogStream:
        def __init__(self, level):
            self._level = level
            self._buffer = ""

        def write(self, text):
            text = str(text)
            if not text:
                return 0
            self._buffer += text
            while "\\n" in self._buffer:
                line, self._buffer = self._buffer.split("\\n", 1)
                if line:
                    lib.mPythonQtLog(self._level, line.encode("utf-8"))
                    _emit_qt_console(line + "\\n")
            return len(text)

        def flush(self):
            if self._buffer:
                lib.mPythonQtLog(self._level, self._buffer.encode("utf-8"))
                _emit_qt_console(self._buffer)
                self._buffer = ""

    class _QtIoRedirect:
        def __enter__(self):
            self._old_stdout = None
            self._old_stderr = None
            if _qt_bridge_bound():
                self._old_stdout = sys.stdout
                self._old_stderr = sys.stderr
                sys.stdout = _QtLogStream(0)
                sys.stderr = _QtLogStream(2)
            return self

        def __exit__(self, exc_type, exc, tb):
            if self._old_stdout is None:
                return False
            sys.stdout.flush()
            sys.stderr.flush()
            sys.stdout = self._old_stdout
            sys.stderr = self._old_stderr
            return False

    def _run_python_code(code, script_globals):
        with _QtIoRedirect():
            exec(code, script_globals, script_globals)

    def _run_session_file(session_key, name, source):
        session_globals = _session_globals(session_key, name)
        code = compile(source, name, 'exec')
        _run_python_code(code, session_globals)
        return True

    def _run_session_prompt(session_key, name, source):
        session_globals = _session_globals(session_key, name)
        try:
            expr = compile(source, name, 'eval')
        except SyntaxError:
            expr = None

        with _QtIoRedirect():
            if expr is not None:
                result = eval(expr, session_globals, session_globals)
                if result is not None:
                    rendered = repr(result)
                    if _qt_bridge_bound():
                        lib.mPythonQtLog(0, rendered.encode("utf-8"))
                        _emit_qt_console(rendered + "\\n")
                    else:
                        print(rendered)
            else:
                code = compile(source, name, 'exec')
                exec(code, session_globals, session_globals)
        return True

    @ffi.def_extern()
    def mPythonSetDebugger(debugger):
        from mgba.debugger import NativeDebugger, CLIDebugger
        oldDebugger = globalSyms.get('debugger')
        if oldDebugger and oldDebugger._native == debugger:
            return
        if oldDebugger and not debugger:
            del globalSyms['debugger']
            return
        if debugger.type == lib.DEBUGGER_CLI:
            debugger = CLIDebugger(debugger)
        else:
            debugger = NativeDebugger(debugger)
        globalSyms['debugger'] = debugger

    @ffi.def_extern()
    def mPythonLoadScript(name, vf):
        from mgba.vfs import VFile
        # The script engine owns this VFile*. Python only borrows it long
        # enough to read the source; closing it here races/double-closes the
        # C++ VFileDevice after the callback returns.
        vf = VFile(vf, _borrowed=True)
        name = ffi.string(name).decode('utf-8')
        _debug_trace("load:start {}".format(name))
        source = vf.read_all().decode('utf-8-sig')
        try:
            code = compile(source, name, 'exec')
            pendingCode.append(code)
            _debug_trace("load:compiled {}".format(name))
        except Exception:
            import traceback
            if lib.mPythonQtIsBound():
                lib.mPythonQtLog(2, traceback.format_exc().encode('utf-8'))
            else:
                traceback.print_exc()
            _debug_trace("load:error {}".format(name))
            return False
        return True

    @ffi.def_extern()
    def mPythonRunScript(name, vf):
        from mgba.vfs import VFile
        # Borrow the engine-owned script file. Ownership stays with the C/C++
        # caller so the Qt startup-script return path can destroy it exactly
        # once after Python has read the bytes.
        vf = VFile(vf, _borrowed=True)
        name = ffi.string(name).decode('utf-8')
        _debug_trace("runscript:start {}".format(name))
        source = vf.read_all().decode('utf-8-sig')
        try:
            code = compile(source, name, 'exec')
            _debug_trace("runscript:compiled {}".format(name))
            # Use one shared dict for globals and locals so module-level
            # constants, helpers, and imports behave like a normal script.
            script_globals = _script_globals(name)
            _run_python_code(code, script_globals)
            _debug_trace("runscript:after-exec {}".format(name))
        except BaseException:
            if hasattr(lib, "mPythonQtAbortRequested") and lib.mPythonQtAbortRequested():
                _debug_trace("runscript:aborted {}".format(name))
                return True
            _report_script_exception()
            _debug_trace("runscript:error {}".format(name))
            return False
        return True

    @ffi.def_extern()
    def mPythonRunPending():
        global pendingCode
        _debug_trace("run:start")
        try:
            with _QtIoRedirect():
                if _qt_bridge_bound():
                    _debug_trace("run:qt-bound")
                for code in pendingCode:
                    _debug_trace("run:before-exec")
                    # Match direct startup-script execution so queued scripts and
                    # immediate scripts see the same __file__/__name__ behavior.
                    script_globals = _script_globals(code.co_filename)
                    exec(code, script_globals, script_globals)
                    _debug_trace("run:after-exec")
        except BaseException:
            if hasattr(lib, "mPythonQtAbortRequested") and lib.mPythonQtAbortRequested():
                _debug_trace("run:aborted")
            else:
                _report_script_exception()
                _debug_trace("run:error")
        finally:
            _debug_trace("run:finally")
            pendingCode = []

    @ffi.def_extern()
    def mPythonSessionRunFile(sessionKey, name, vf):
        from mgba.vfs import VFile
        # Runtime Qt Python sessions receive a C++-owned VFileDevice here. Keep
        # this wrapper borrowed; otherwise Python's VFile.__del__ can close the
        # handle and leave C++ with a dangling pointer during scope cleanup.
        vf = VFile(vf, _borrowed=True)
        name = ffi.string(name).decode('utf-8')
        _debug_trace("sessionfile:start {} {}".format(int(sessionKey), name))
        source = vf.read_all().decode('utf-8-sig')
        try:
            _run_session_file(sessionKey, name, source)
            _debug_trace("sessionfile:after-exec {} {}".format(int(sessionKey), name))
            # Startup-script execution now shares the same session runner as the
            # interactive scripting window. Emit the legacy runscript marker as
            # well so deployment tests can keep proving the file actually ran.
            _debug_trace("runscript:after-exec {}".format(name))
        except BaseException:
            if hasattr(lib, "mPythonQtAbortRequested") and lib.mPythonQtAbortRequested():
                _debug_trace("sessionfile:aborted {} {}".format(int(sessionKey), name))
                return True
            _report_script_exception()
            _debug_trace("sessionfile:error {} {}".format(int(sessionKey), name))
            return False
        return True

    @ffi.def_extern()
    def mPythonSessionRunCode(sessionKey, name, code):
        name = ffi.string(name).decode('utf-8')
        source = ffi.string(code).decode('utf-8-sig')
        _debug_trace("sessioncode:start {} {}".format(int(sessionKey), name))
        try:
            _run_session_prompt(sessionKey, name, source)
            _debug_trace("sessioncode:after-exec {} {}".format(int(sessionKey), name))
        except BaseException:
            if hasattr(lib, "mPythonQtAbortRequested") and lib.mPythonQtAbortRequested():
                _debug_trace("sessioncode:aborted {} {}".format(int(sessionKey), name))
                return True
            _report_script_exception()
            _debug_trace("sessioncode:error {} {}".format(int(sessionKey), name))
            return False
        return True

    @ffi.def_extern()
    def mPythonSessionReset(sessionKey):
        qtSessions.pop(int(sessionKey), None)
        _debug_trace("session:reset {}".format(int(sessionKey)))

    @ffi.def_extern()
    def mPythonDebuggerEntered(reason, info):
        debugger = globalSyms['debugger']
        if not debugger:
            return
        if info == ffi.NULL:
            info = None
        for cb in debugger._cbs:
            cb(reason, info)

    @ffi.def_extern()
    def mPythonLookupSymbol(name, outptr):
        name = ffi.string(name).decode('utf-8')
        if name not in symbols:
            return False
        sym = symbols[name]
        val = None
        try:
            val = int(sym)
        except:
            try:
                val = sym()
            except:
                pass
        if val is None:
            return False
        try:
            outptr[0] = ffi.cast('int32_t', val)
            return True
        except:
            return False
""")

if __name__ == "__main__":
    ffi.emit_c_code("lib.c")
