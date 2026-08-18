/* Copyright (c) 2013-2014 Jeffrey Pfau
 *
 * Original mGBA source is credited to Jeffrey Pfau and contributors.
 * Local custom modifications in this fork were added for this workspace and
 * are not upstream mGBA work or authored by Jeffrey Pfau.
 *
 * This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */
#ifndef CLI_EL_BACKEND_H
#define CLI_EL_BACKEND_H

#include <mgba-util/common.h>

CXX_GUARD_START

#include <mgba/internal/debugger/cli-debugger.h>

/* The local SDL debugger + Python path still needs a console on Windows/MSYS2
 * builds that do not ship histedit.h. Keep one backend type and swap the
 * stored state based on the editline features that are really available. */
#if defined(__has_include)
#if __has_include(<histedit.h>)
#define MGBA_HAS_HISTEDIT 1
#include <histedit.h>
#else
#define MGBA_HAS_HISTEDIT 0
#endif
#else
#define MGBA_HAS_HISTEDIT 0
#endif

struct CLIDebuggerEditLineBackend {
	struct CLIDebuggerBackend d;

#if MGBA_HAS_HISTEDIT
	EditLine* elstate;
	History* histate;
#else
	char line[4096];
	char lastLine[4096];
#endif
};

struct CLIDebuggerBackend* CLIDebuggerEditLineBackendCreate(void);

CXX_GUARD_END

#endif
