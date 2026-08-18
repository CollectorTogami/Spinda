/* Copyright (c) 2013-2017 Jeffrey Pfau
 *
 * Original mGBA source is credited to Jeffrey Pfau and contributors.
 * Local custom modifications in this fork were added for this workspace and
 * are not upstream mGBA work or authored by Jeffrey Pfau.
 *
 * This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */
#ifndef PYTHON_QT_H
#define PYTHON_QT_H

#include <mgba-util/common.h>

#include "pycommon.h"

#if defined(_WIN32)
#ifdef MGBA_PYTHON_DLL
#define MPYQTAPI __declspec(dllexport)
#elif defined(MGBA_PYTHON_IMPORT)
#define MPYQTAPI __declspec(dllimport)
#else
#define MPYQTAPI extern
#endif
#else
#define MPYQTAPI extern
#endif

CXX_GUARD_START

/* This is the narrow bridge between the visible Qt window and Python. It only
 * exposes the operations needed by local automation scripts: frame stepping,
 * keys, memory, savestates, log output, and a small set of custom-feature
 * toggles that are useful for long-running RNG workflows. */
typedef bool (*mPythonQtResetCallback)(void* context);
typedef bool (*mPythonQtRunFrameCallback)(void* context);
typedef bool (*mPythonQtRunFramesCallback)(void* context, uint32_t frames);
typedef bool (*mPythonQtRunFramesWithKeysCallback)(void* context, uint32_t keys, uint32_t frames);
typedef void (*mPythonQtSetKeysCallback)(void* context, uint32_t keys);
typedef int32_t (*mPythonQtPlatformCallback)(void* context);
typedef uint64_t (*mPythonQtFrameCounterCallback)(void* context);
typedef int32_t (*mPythonQtBoolCallback)(void* context);
typedef uint32_t (*mPythonQtUIntCallback)(void* context);
typedef bool (*mPythonQtPathCallback)(void* context, const char* path);
typedef bool (*mPythonQtPathIntCallback)(void* context, const char* path, int value);
typedef uint32_t (*mPythonQtThemeColorCallback)(void* context, int which);
typedef uint32_t (*mPythonQtReadMemoryCallback)(void* context, uint32_t address);
typedef void (*mPythonQtWriteMemoryCallback)(void* context, uint32_t address, uint32_t value);
typedef bool (*mPythonQtStateFileCallback)(void* context, const char* path, int flags);
typedef bool (*mPythonQtStateCallback)(void* context);
typedef void (*mPythonQtLogCallback)(void* context, int level, const char* message);
typedef void (*mPythonQtConsoleCallback)(void* context, const char* message);
typedef bool (*mPythonQtDialogCallback)(void* context, const char* title, const char* message);
typedef void (*mPythonQtTextBufferCallback)(void* context, const char* name, const char* text, uint32_t cols, uint32_t rows);
typedef bool (*mPythonQtSetBoolCallback)(void* context, int value);
typedef bool (*mPythonQtSetFloatCallback)(void* context, float value);
typedef bool (*mPythonQtKeyBoolCallback)(void* context, int key, int value);
typedef bool (*mPythonQtKeyFramesCallback)(void* context, int key, uint32_t frames);

enum mPythonQtThemeColor {
	MPYQT_THEME_WINDOW = 0,
	MPYQT_THEME_BASE = 1,
	MPYQT_THEME_TEXT = 2,
	MPYQT_THEME_BUTTON = 3,
	MPYQT_THEME_HIGHLIGHT = 4
};

struct mPythonQtBindings {
	void* context;
	mPythonQtResetCallback reset;
	mPythonQtRunFrameCallback runFrame;
	/* Batch stepping is a custom workspace speed path for long deterministic
	 * waits. It keeps the loop inside C++ instead of re-entering Python once per
	 * frame. */
	mPythonQtRunFramesCallback runFrames;
	mPythonQtRunFramesWithKeysCallback runFramesWithKeys;
	mPythonQtRunFramesWithKeysCallback pulseKeys;
	mPythonQtSetKeysCallback setKeys;
	mPythonQtPlatformCallback platform;
	mPythonQtFrameCounterCallback frameCounter;
	mPythonQtBoolCallback abortRequested;
	mPythonQtBoolCallback pause;
	mPythonQtBoolCallback isPaused;
	mPythonQtBoolCallback darkModeEnabled;
	mPythonQtBoolCallback hasStyleSheet;
	/* Runtime-only helpers used by the visible Qt scripting path. These are the
	 * operations that let a mid-session Python script swap ROMs, inject saves,
	 * and export the current save without going back through a launcher. */
	mPythonQtPathCallback loadRomFile;
	mPythonQtPathIntCallback loadSaveFile;
	mPythonQtPathCallback exportSaveFile;
	mPythonQtThemeColorCallback themeColor;
	mPythonQtReadMemoryCallback read8;
	mPythonQtReadMemoryCallback read16;
	mPythonQtReadMemoryCallback read32;
	mPythonQtWriteMemoryCallback write8;
	mPythonQtWriteMemoryCallback write16;
	mPythonQtWriteMemoryCallback write32;
	mPythonQtStateFileCallback loadStateFile;
	mPythonQtStateFileCallback saveStateFile;
	/* One in-memory scratch checkpoint for hot runtime loops. This avoids
	 * repeatedly round-tripping the same savestate through the filesystem when
	 * a visible Qt script wants to branch from one exact frame many times. */
	mPythonQtStateCallback loadScratchState;
	mPythonQtStateCallback saveScratchState;
	mPythonQtLogCallback log;
	mPythonQtConsoleCallback consoleWrite;
	mPythonQtDialogCallback showWarning;
	/* Mirror Lua's named text buffers for Python scripts that want one live
	 * status panel instead of appending a new console line every frame. */
	mPythonQtTextBufferCallback setTextBuffer;
	/* Custom-feature bridge for long-running automation scripts that want to
	 * flip the live Audio killswitch without reaching back into the Qt menus. */
	mPythonQtBoolCallback audioKillswitchEnabled;
	mPythonQtSetBoolCallback setAudioKillswitch;
	/* No-render mode is the matching video-side throughput toggle. Exposing it
	 * here lets Python replay scripts force the same deterministic low-host-load
	 * setup from a mid-session launch. */
	mPythonQtBoolCallback noRenderModeEnabled;
	mPythonQtSetBoolCallback setNoRenderMode;
	/* Fast-forward bridge used by RNG automation scripts. A non-positive ratio
	 * is the same "Unbounded" speed that the Qt menu exposes. */
	mPythonQtBoolCallback fastForwardEnabled;
	mPythonQtSetBoolCallback setFastForward;
	mPythonQtSetFloatCallback setFastForwardRatio;
	/* Virtual Pad bridge. These mirror the Custom Features tool so scripts can
	 * open the same UI and drive its hold/autofire/timed-press behavior. */
	mPythonQtBoolCallback openVirtualPad;
	mPythonQtBoolCallback openVirtualPadSettings;
	mPythonQtKeyBoolCallback virtualPadSetHeld;
	mPythonQtKeyBoolCallback virtualPadSetAutofire;
	mPythonQtKeyFramesCallback virtualPadPressForFrames;
	mPythonQtUIntCallback virtualPadKeyMask;
	/* Mapped frontend-controller mask. This samples the next-frame GBA button
	 * bits after Qt has converted keyboard events such as X/Z/Enter into
	 * A/B/Start/etc.; it never exposes raw host key codes. */
	mPythonQtUIntCallback controllerKeyMask;
	mPythonQtBoolCallback virtualPadClear;
};

MPYQTAPI bool mPythonQtBind(const struct mPythonQtBindings* bindings);
MPYQTAPI void mPythonQtUnbind(void);
MPYQTAPI bool mPythonQtIsBound(void);

MPYQTAPI bool mPythonQtReset(void);
MPYQTAPI bool mPythonQtRunFrame(void);
MPYQTAPI bool mPythonQtRunFrames(uint32_t frames);
MPYQTAPI bool mPythonQtRunFramesWithKeys(uint32_t keys, uint32_t frames);
MPYQTAPI bool mPythonQtPulseKeys(uint32_t keys, uint32_t frames);
MPYQTAPI void mPythonQtSetKeys(uint32_t keys);
MPYQTAPI int32_t mPythonQtPlatform(void);
MPYQTAPI uint64_t mPythonQtFrameCounter(void);
MPYQTAPI int32_t mPythonQtAbortRequested(void);
MPYQTAPI int32_t mPythonQtPause(void);
MPYQTAPI int32_t mPythonQtIsPaused(void);
MPYQTAPI int32_t mPythonQtDarkModeEnabled(void);
MPYQTAPI int32_t mPythonQtHasStyleSheet(void);
MPYQTAPI bool mPythonQtLoadRomFile(const char* path);
MPYQTAPI bool mPythonQtLoadSaveFile(const char* path, int temporary);
MPYQTAPI bool mPythonQtExportSaveFile(const char* path);
MPYQTAPI uint32_t mPythonQtThemeColor(int which);
MPYQTAPI uint32_t mPythonQtRead8(uint32_t address);
MPYQTAPI uint32_t mPythonQtRead16(uint32_t address);
MPYQTAPI uint32_t mPythonQtRead32(uint32_t address);
MPYQTAPI void mPythonQtWrite8(uint32_t address, uint32_t value);
MPYQTAPI void mPythonQtWrite16(uint32_t address, uint32_t value);
MPYQTAPI void mPythonQtWrite32(uint32_t address, uint32_t value);
MPYQTAPI bool mPythonQtLoadStateFile(const char* path, int flags);
MPYQTAPI bool mPythonQtSaveStateFile(const char* path, int flags);
MPYQTAPI bool mPythonQtLoadScratchState(void);
MPYQTAPI bool mPythonQtSaveScratchState(void);
MPYQTAPI void mPythonQtLog(int level, const char* message);
MPYQTAPI void mPythonQtConsoleWrite(const char* message);
MPYQTAPI int32_t mPythonQtShowWarning(const char* title, const char* message);
MPYQTAPI void mPythonQtSetTextBuffer(const char* name, const char* text, uint32_t cols, uint32_t rows);
MPYQTAPI int32_t mPythonQtAudioKillswitchEnabled(void);
MPYQTAPI int32_t mPythonQtSetAudioKillswitch(int enable);
MPYQTAPI int32_t mPythonQtNoRenderModeEnabled(void);
MPYQTAPI int32_t mPythonQtSetNoRenderMode(int enable);
MPYQTAPI int32_t mPythonQtFastForwardEnabled(void);
MPYQTAPI int32_t mPythonQtSetFastForward(int enable);
MPYQTAPI int32_t mPythonQtSetFastForwardRatio(float ratio);
MPYQTAPI int32_t mPythonQtOpenVirtualPad(void);
MPYQTAPI int32_t mPythonQtOpenVirtualPadSettings(void);
MPYQTAPI int32_t mPythonQtVirtualPadSetHeld(int key, int enable);
MPYQTAPI int32_t mPythonQtVirtualPadSetAutofire(int key, int enable);
MPYQTAPI int32_t mPythonQtVirtualPadPressForFrames(int key, uint32_t frames);
MPYQTAPI uint32_t mPythonQtVirtualPadKeyMask(void);
MPYQTAPI uint32_t mPythonQtControllerKeyMask(void);
MPYQTAPI int32_t mPythonQtVirtualPadClear(void);

CXX_GUARD_END

#undef MPYQTAPI

#endif
