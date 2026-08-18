/* Copyright (c) 2013-2016 Jeffrey Pfau
 *
 * Original mGBA source is credited to Jeffrey Pfau and contributors.
 * Local custom modifications in this fork were added for this workspace and
 * are not upstream mGBA work or authored by Jeffrey Pfau.
 *
 * This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */
#include <mgba-util/vfs.h>

#include "pycommon.h"

struct VFilePy {
	struct VFile d;
	void* fileobj;
};

struct VFile* VFileFromPython(void* fileobj);

#ifdef PYCPARSE
/* pycparser does not always understand the platform off_t typedef, so expose a
 * fixed-width substitute while generating the Python bridge. */
typedef int64_t mPythonVfsOffT;
#else
typedef off_t mPythonVfsOffT;
#endif

PYEXPORT bool _vfpClose(struct VFile* vf);
PYEXPORT mPythonVfsOffT _vfpSeek(struct VFile* vf, mPythonVfsOffT offset, int whence);
PYEXPORT ssize_t _vfpRead(struct VFile* vf, void* buffer, size_t size);
PYEXPORT ssize_t _vfpWrite(struct VFile* vf, const void* buffer, size_t size);
PYEXPORT void* _vfpMap(struct VFile* vf, size_t size, int flags);
PYEXPORT void _vfpUnmap(struct VFile* vf, void* memory, size_t size);
PYEXPORT void _vfpTruncate(struct VFile* vf, size_t size);
PYEXPORT ssize_t _vfpSize(struct VFile* vf);
PYEXPORT bool _vfpSync(struct VFile* vf, void* buffer, size_t size);
