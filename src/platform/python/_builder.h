#define COMMON_H
#define PNG_H
#define OPAQUE_THREADING
#define _SYS_TIME_H
#define _SYS_TIME_H_
#define _TIME_H
#define _TIME_H_
#define VFS_H
#define TABLE_H
#define MGBA_EXPORT

/* This header is parsed by pycparser while generating the local CFFI bridge.
 * The goal is to provide a stable, parseable subset of the C API used by the
 * Python automation path, not to mirror every upstream system header exactly.
 */

#define ATTRIBUTE_FORMAT(X, Y, Z)
#define DECL_BITFIELD(newtype, oldtype) typedef oldtype newtype
#define DECL_BIT(type, field, bit) DECL_BITS(type, field, bit, 1)
#define DECL_BITS(TYPE, FIELD, START, SIZE) \
	TYPE TYPE ## Is ## FIELD (TYPE); \
	TYPE TYPE ## Get ## FIELD (TYPE); \
	TYPE TYPE ## Clear ## FIELD (TYPE); \
	TYPE TYPE ## Fill ## FIELD (TYPE); \
	TYPE TYPE ## Set ## FIELD (TYPE, TYPE); \
	TYPE TYPE ## TestFill ## FIELD (TYPE, bool);

#define CXX_GUARD_START
#define CXX_GUARD_END

#define PYCPARSE
#define va_list char*

#ifndef PATH_MAX
#define PATH_MAX 260
#endif

typedef unsigned long long size_t;
typedef long long ssize_t;
typedef long long intptr_t;
typedef unsigned long long uintptr_t;
typedef long long ptrdiff_t;
typedef int... time_t;
typedef ...* png_structp;
typedef ...* png_infop;
typedef ...* png_unknown_chunkp;

void free(void*);

/* pycparser cannot reliably consume the real util headers on every MinGW
 * build, so provide the table/VFS pieces that the Python bridge actually uses.
 */
typedef void (*TableDeinitializer)(void*);
typedef uint32_t (*HashFunction)(const void* key, size_t len, uint32_t seed);
typedef bool (*TableEqual)(const void*, const void*);
typedef void* (*TableRef)(void*);
typedef void (*TableDeref)(void*);

struct TableList;

struct TableFunctions {
	TableDeinitializer deinitializer;
	HashFunction hash;
	TableEqual equal;
	TableRef ref;
	TableDeref deref;
};

struct Table {
	struct TableList* table;
	size_t tableSize;
	size_t size;
	uint32_t seed;
	struct TableFunctions fn;
};

struct TableIterator {
	size_t bucket;
	size_t entry;
};

#include <mgba/flags.h>

#include <mgba/core/blip_buf.h>
#include <mgba/core/cache-set.h>
#include <mgba/core/core.h>
#include <mgba/core/map-cache.h>
#include <mgba/core/mem-search.h>
#include <mgba/core/thread.h>
#include <mgba/core/version.h>

enum {
	MAP_READ = 1,
	MAP_WRITE = 2
};

enum VFSType {
	VFS_UNKNOWN = 0,
	VFS_FILE,
	VFS_DIRECTORY
};

struct VFile {
	bool (*close)(struct VFile* vf);
	int64_t (*seek)(struct VFile* vf, int64_t offset, int whence);
	ssize_t (*read)(struct VFile* vf, void* buffer, size_t size);
	ssize_t (*readline)(struct VFile* vf, char* buffer, size_t size);
	ssize_t (*write)(struct VFile* vf, const void* buffer, size_t size);
	void* (*map)(struct VFile* vf, size_t size, int flags);
	void (*unmap)(struct VFile* vf, void* memory, size_t size);
	void (*truncate)(struct VFile* vf, size_t size);
	ssize_t (*size)(struct VFile* vf);
	bool (*sync)(struct VFile* vf, void* buffer, size_t size);
};

struct VDirEntry;
struct VDir;

/* These are the file entry points used by the local savestate, ROM, and Qt
 * scripting helpers.
 */
struct VFile* VFileOpen(const char* path, int flags);
struct VFile* VFileOpenFD(const char* path, int flags);
struct VFile* VFileFromFD(int fd);
struct VFile* VFileFromMemory(void* mem, size_t size);
struct VFile* VFileFromConstMemory(const void* mem, size_t size);
struct VFile* VFileMemChunk(const void* mem, size_t size);
ssize_t VFileReadline(struct VFile* vf, char* buffer, size_t size);

#define PYEXPORT extern "Python+C"
#include "platform/python/core.h"
#include "platform/python/log.h"
#include "platform/python/qt.h"
#include "platform/python/sio.h"
#include "platform/python/vfs-py.h"
#undef PYEXPORT

bool mPythonSessionRunFile(uint64_t sessionKey, const char* name, struct VFile* vf);
bool mPythonSessionRunCode(uint64_t sessionKey, const char* name, const char* code);
void mPythonSessionReset(uint64_t sessionKey);

#ifdef USE_PNG
#include <mgba-util/png-io.h>
#endif
#ifdef M_CORE_GBA
#include <mgba/gba/interface.h>
#include <mgba/internal/arm/arm.h>
#include <mgba/internal/gba/gba.h>
#include <mgba/internal/gba/input.h>
#include <mgba/internal/gba/renderers/cache-set.h>
#endif
#ifdef M_CORE_GB
#include <mgba/internal/sm83/sm83.h>
#include <mgba/internal/gb/gb.h>
#include <mgba/internal/gba/input.h>
#include <mgba/internal/gb/renderers/cache-set.h>
#endif
#ifdef USE_DEBUGGERS
#include <mgba/debugger/debugger.h>
#include <mgba/internal/debugger/cli-debugger.h>
#endif
