#include <stdbool.h>
#include <stdint.h>

struct VFile;

/* Shared declarations for the embedded frontends and the generated CFFI
 * module. The explicit exports keep the local Qt bridge and the older SDL
 * bridge using the same entry points. */
#ifndef CXX_GUARD_START
#ifdef __cplusplus
#define CXX_GUARD_START extern "C" {
#define CXX_GUARD_END }
#else
#define CXX_GUARD_START
#define CXX_GUARD_END
#endif
#define MPYLIB_LOCAL_CXX_GUARD
#endif

#if defined(_WIN32)
#ifdef MGBA_PYTHON_DLL
#define MPYLIBAPI __declspec(dllexport)
#elif defined(MGBA_PYTHON_IMPORT)
#define MPYLIBAPI __declspec(dllimport)
#else
#define MPYLIBAPI extern
#endif
#else
#define MPYLIBAPI extern
#endif

CXX_GUARD_START

MPYLIBAPI bool mPythonLoadScript(const char*, struct VFile*);
MPYLIBAPI bool mPythonRunScript(const char*, struct VFile*);
MPYLIBAPI void mPythonRunPending();
MPYLIBAPI bool mPythonLookupSymbol(const char* name, int32_t* out);
/* The Qt scripting window needs persistent Python globals so loading a file
 * and then using the prompt behaves like one live session instead of a series
 * of isolated startup-style runs. */
MPYLIBAPI bool mPythonSessionRunFile(uint64_t sessionKey, const char* name, struct VFile*);
MPYLIBAPI bool mPythonSessionRunCode(uint64_t sessionKey, const char* name, const char* code);
MPYLIBAPI void mPythonSessionReset(uint64_t sessionKey);
/* Prepare PYTHONHOME for embedded runs when mGBA.exe is the entry point. */
MPYLIBAPI void mPythonEnsureEnvironment(void);

#ifdef USE_DEBUGGERS
MPYLIBAPI void mPythonSetDebugger(struct mDebugger*);
MPYLIBAPI void mPythonDebuggerEntered(enum mDebuggerEntryReason, struct mDebuggerEntryInfo*);
#endif

CXX_GUARD_END

#undef MPYLIBAPI

#ifdef MPYLIB_LOCAL_CXX_GUARD
#undef CXX_GUARD_START
#undef CXX_GUARD_END
#undef MPYLIB_LOCAL_CXX_GUARD
#endif
