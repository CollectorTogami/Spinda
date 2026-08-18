/* Copyright (c) 2013-2017 Jeffrey Pfau
 *
 * Original mGBA source is credited to Jeffrey Pfau and contributors.
 * Local custom modifications in this fork were added for this workspace and
 * are not upstream mGBA work or authored by Jeffrey Pfau.
 *
 * This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */
#ifndef PYTHON_ENGINE_H
#define PYTHON_ENGINE_H

#include <mgba-util/common.h>

/* The local Windows builds use the same declarations whether the Python bridge
 * is linked into a frontend or imported from the bridge DLL. */
#if defined(_WIN32)
#ifdef MGBA_PYTHON_DLL
#define MPYAPI __declspec(dllexport)
#elif defined(MGBA_PYTHON_IMPORT)
#define MPYAPI __declspec(dllimport)
#else
#define MPYAPI extern
#endif
#else
#define MPYAPI extern
#endif

CXX_GUARD_START

struct mScriptBridge;
struct mPythonScriptEngine;
MPYAPI struct mPythonScriptEngine* mPythonCreateScriptEngine(void);
MPYAPI void mPythonSetup(struct mScriptBridge* sb);

CXX_GUARD_END

#undef MPYAPI

#endif
