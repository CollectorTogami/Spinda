/* Copyright (c) 2013-2017 Jeffrey Pfau
 *
 * Original mGBA source is credited to Jeffrey Pfau and contributors.
 * Local custom modifications in this fork were added for this workspace and
 * are not upstream mGBA work or authored by Jeffrey Pfau.
 *
 * This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */
#include "lib.h"

#include <stdlib.h>

#ifndef _WIN32
#include <unistd.h>
#endif

void mPythonEnsureEnvironment(void) {
#ifdef MGBA_PYTHON_BASE_PREFIX
	/* Embedded Qt/SDL runs may start from mGBA.exe instead of python.exe. Seed
	 * PYTHONHOME once from the build-time interpreter prefix so the stdlib and
	 * local scripts work without manual shell setup. */
#ifdef _WIN32
	if (!getenv("PYTHONHOME")) {
		static char pythonHome[] = "PYTHONHOME=" MGBA_PYTHON_BASE_PREFIX;
		putenv(pythonHome);
	}
#else
	if (!getenv("PYTHONHOME")) {
		setenv("PYTHONHOME", MGBA_PYTHON_BASE_PREFIX, 0);
	}
#endif
#endif

#ifdef MGBA_PYTHON_VENV_PREFIX
	/* Keep the visible Qt scripting path tied to the same dedicated venv the
	 * project builds against. PYTHONHOME still targets the base interpreter for
	 * stdlib bootstrap, but these env vars let the Python startup code recover
	 * the venv's site-packages deterministically. */
#ifdef _WIN32
	if (!getenv("VIRTUAL_ENV")) {
		static char venvPrefix[] = "VIRTUAL_ENV=" MGBA_PYTHON_VENV_PREFIX;
		putenv(venvPrefix);
	}
#else
	if (!getenv("VIRTUAL_ENV")) {
		setenv("VIRTUAL_ENV", MGBA_PYTHON_VENV_PREFIX, 0);
	}
#endif
#endif

#ifdef MGBA_PYTHON_SITE_PACKAGES
#ifdef _WIN32
	if (!getenv("MGBA_PYTHON_SITE_PACKAGES")) {
		static char sitePackages[] = "MGBA_PYTHON_SITE_PACKAGES=" MGBA_PYTHON_SITE_PACKAGES;
		putenv(sitePackages);
	}
#else
	if (!getenv("MGBA_PYTHON_SITE_PACKAGES")) {
		setenv("MGBA_PYTHON_SITE_PACKAGES", MGBA_PYTHON_SITE_PACKAGES, 0);
	}
#endif
#endif
}
