/* Copyright (c) 2013-2017 Jeffrey Pfau
 *
 * Original mGBA source is credited to Jeffrey Pfau and contributors.
 * Local custom modifications in this fork were added for this workspace and
 * are not upstream mGBA work or authored by Jeffrey Pfau.
 *
 * This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */
#include "qt.h"

#include <string.h>

static struct mPythonQtBindings s_bindings;
static bool s_bound = false;

/* Store one active Qt/Python session at a time. Startup scripts and the
 * scripting window both borrow this bridge while they run, then unbind it. */
bool mPythonQtBind(const struct mPythonQtBindings* bindings) {
	if (!bindings || !bindings->context || !bindings->runFrame || !bindings->setKeys || !bindings->frameCounter) {
		mPythonQtUnbind();
		return false;
	}

	memcpy(&s_bindings, bindings, sizeof(s_bindings));
	s_bound = true;
	return true;
}

void mPythonQtUnbind(void) {
	memset(&s_bindings, 0, sizeof(s_bindings));
	s_bound = false;
}

bool mPythonQtIsBound(void) {
	return s_bound;
}

bool mPythonQtReset(void) {
	return s_bound && s_bindings.reset && s_bindings.reset(s_bindings.context);
}

bool mPythonQtRunFrame(void) {
	return s_bound && s_bindings.runFrame && s_bindings.runFrame(s_bindings.context);
}

bool mPythonQtRunFrames(uint32_t frames) {
	return s_bound && s_bindings.runFrames && s_bindings.runFrames(s_bindings.context, frames);
}

bool mPythonQtRunFramesWithKeys(uint32_t keys, uint32_t frames) {
	return s_bound && s_bindings.runFramesWithKeys && s_bindings.runFramesWithKeys(s_bindings.context, keys, frames);
}

bool mPythonQtPulseKeys(uint32_t keys, uint32_t frames) {
	return s_bound && s_bindings.pulseKeys && s_bindings.pulseKeys(s_bindings.context, keys, frames);
}

void mPythonQtSetKeys(uint32_t keys) {
	if (!s_bound || !s_bindings.setKeys) {
		return;
	}
	s_bindings.setKeys(s_bindings.context, keys);
}

int32_t mPythonQtPlatform(void) {
	if (!s_bound || !s_bindings.platform) {
		return 0;
	}
	return s_bindings.platform(s_bindings.context);
}

uint64_t mPythonQtFrameCounter(void) {
	if (!s_bound || !s_bindings.frameCounter) {
		return 0;
	}
	return s_bindings.frameCounter(s_bindings.context);
}

int32_t mPythonQtAbortRequested(void) {
	if (!s_bound || !s_bindings.abortRequested) {
		return 0;
	}
	return s_bindings.abortRequested(s_bindings.context);
}

int32_t mPythonQtPause(void) {
	if (!s_bound || !s_bindings.pause) {
		return 0;
	}
	return s_bindings.pause(s_bindings.context);
}

int32_t mPythonQtIsPaused(void) {
	if (!s_bound || !s_bindings.isPaused) {
		return 0;
	}
	return s_bindings.isPaused(s_bindings.context);
}

/* Theme helpers are exposed mainly so the local deployment tests can verify
 * that the visible Qt window really stayed in dark mode during scripted runs. */
int32_t mPythonQtDarkModeEnabled(void) {
	if (!s_bound || !s_bindings.darkModeEnabled) {
		return 0;
	}
	return s_bindings.darkModeEnabled(s_bindings.context);
}

int32_t mPythonQtHasStyleSheet(void) {
	if (!s_bound || !s_bindings.hasStyleSheet) {
		return 0;
	}
	return s_bindings.hasStyleSheet(s_bindings.context);
}

bool mPythonQtLoadRomFile(const char* path) {
	return s_bound && s_bindings.loadRomFile && s_bindings.loadRomFile(s_bindings.context, path);
}

bool mPythonQtLoadSaveFile(const char* path, int temporary) {
	return s_bound && s_bindings.loadSaveFile && s_bindings.loadSaveFile(s_bindings.context, path, temporary);
}

bool mPythonQtExportSaveFile(const char* path) {
	return s_bound && s_bindings.exportSaveFile && s_bindings.exportSaveFile(s_bindings.context, path);
}

uint32_t mPythonQtThemeColor(int which) {
	if (!s_bound || !s_bindings.themeColor) {
		return 0;
	}
	return s_bindings.themeColor(s_bindings.context, which);
}

uint32_t mPythonQtRead8(uint32_t address) {
	if (!s_bound || !s_bindings.read8) {
		return 0;
	}
	return s_bindings.read8(s_bindings.context, address);
}

uint32_t mPythonQtRead16(uint32_t address) {
	if (!s_bound || !s_bindings.read16) {
		return 0;
	}
	return s_bindings.read16(s_bindings.context, address);
}

uint32_t mPythonQtRead32(uint32_t address) {
	if (!s_bound || !s_bindings.read32) {
		return 0;
	}
	return s_bindings.read32(s_bindings.context, address);
}

void mPythonQtWrite8(uint32_t address, uint32_t value) {
	if (!s_bound || !s_bindings.write8) {
		return;
	}
	s_bindings.write8(s_bindings.context, address, value);
}

void mPythonQtWrite16(uint32_t address, uint32_t value) {
	if (!s_bound || !s_bindings.write16) {
		return;
	}
	s_bindings.write16(s_bindings.context, address, value);
}

void mPythonQtWrite32(uint32_t address, uint32_t value) {
	if (!s_bound || !s_bindings.write32) {
		return;
	}
	s_bindings.write32(s_bindings.context, address, value);
}

bool mPythonQtLoadStateFile(const char* path, int flags) {
	return s_bound && s_bindings.loadStateFile && s_bindings.loadStateFile(s_bindings.context, path, flags);
}

bool mPythonQtSaveStateFile(const char* path, int flags) {
	return s_bound && s_bindings.saveStateFile && s_bindings.saveStateFile(s_bindings.context, path, flags);
}

bool mPythonQtLoadScratchState(void) {
	return s_bound && s_bindings.loadScratchState && s_bindings.loadScratchState(s_bindings.context);
}

bool mPythonQtSaveScratchState(void) {
	return s_bound && s_bindings.saveScratchState && s_bindings.saveScratchState(s_bindings.context);
}

void mPythonQtLog(int level, const char* message) {
	if (!s_bound || !s_bindings.log) {
		return;
	}
	s_bindings.log(s_bindings.context, level, message);
}

void mPythonQtConsoleWrite(const char* message) {
	if (!s_bound || !s_bindings.consoleWrite) {
		return;
	}
	s_bindings.consoleWrite(s_bindings.context, message);
}

int32_t mPythonQtShowWarning(const char* title, const char* message) {
	if (!s_bound || !s_bindings.showWarning) {
		return 0;
	}
	return s_bindings.showWarning(s_bindings.context, title, message);
}

void mPythonQtSetTextBuffer(const char* name, const char* text, uint32_t cols, uint32_t rows) {
	if (!s_bound || !s_bindings.setTextBuffer) {
		return;
	}
	s_bindings.setTextBuffer(s_bindings.context, name, text, cols, rows);
}

int32_t mPythonQtAudioKillswitchEnabled(void) {
	if (!s_bound || !s_bindings.audioKillswitchEnabled) {
		return 0;
	}
	return s_bindings.audioKillswitchEnabled(s_bindings.context);
}

int32_t mPythonQtSetAudioKillswitch(int enable) {
	if (!s_bound || !s_bindings.setAudioKillswitch) {
		return 0;
	}
	return s_bindings.setAudioKillswitch(s_bindings.context, enable);
}

int32_t mPythonQtNoRenderModeEnabled(void) {
	if (!s_bound || !s_bindings.noRenderModeEnabled) {
		return 0;
	}
	return s_bindings.noRenderModeEnabled(s_bindings.context);
}

int32_t mPythonQtSetNoRenderMode(int enable) {
	if (!s_bound || !s_bindings.setNoRenderMode) {
		return 0;
	}
	return s_bindings.setNoRenderMode(s_bindings.context, enable);
}

int32_t mPythonQtFastForwardEnabled(void) {
	if (!s_bound || !s_bindings.fastForwardEnabled) {
		return 0;
	}
	return s_bindings.fastForwardEnabled(s_bindings.context);
}

int32_t mPythonQtSetFastForward(int enable) {
	if (!s_bound || !s_bindings.setFastForward) {
		return 0;
	}
	return s_bindings.setFastForward(s_bindings.context, enable);
}

int32_t mPythonQtSetFastForwardRatio(float ratio) {
	if (!s_bound || !s_bindings.setFastForwardRatio) {
		return 0;
	}
	return s_bindings.setFastForwardRatio(s_bindings.context, ratio);
}

int32_t mPythonQtOpenVirtualPad(void) {
	if (!s_bound || !s_bindings.openVirtualPad) {
		return 0;
	}
	return s_bindings.openVirtualPad(s_bindings.context);
}

int32_t mPythonQtOpenVirtualPadSettings(void) {
	if (!s_bound || !s_bindings.openVirtualPadSettings) {
		return 0;
	}
	return s_bindings.openVirtualPadSettings(s_bindings.context);
}

int32_t mPythonQtVirtualPadSetHeld(int key, int enable) {
	if (!s_bound || !s_bindings.virtualPadSetHeld) {
		return 0;
	}
	return s_bindings.virtualPadSetHeld(s_bindings.context, key, enable);
}

int32_t mPythonQtVirtualPadSetAutofire(int key, int enable) {
	if (!s_bound || !s_bindings.virtualPadSetAutofire) {
		return 0;
	}
	return s_bindings.virtualPadSetAutofire(s_bindings.context, key, enable);
}

int32_t mPythonQtVirtualPadPressForFrames(int key, uint32_t frames) {
	if (!s_bound || !s_bindings.virtualPadPressForFrames) {
		return 0;
	}
	return s_bindings.virtualPadPressForFrames(s_bindings.context, key, frames);
}

uint32_t mPythonQtVirtualPadKeyMask(void) {
	if (!s_bound || !s_bindings.virtualPadKeyMask) {
		return 0;
	}
	return s_bindings.virtualPadKeyMask(s_bindings.context);
}

uint32_t mPythonQtControllerKeyMask(void) {
	if (!s_bound || !s_bindings.controllerKeyMask) {
		return 0;
	}
	return s_bindings.controllerKeyMask(s_bindings.context);
}

int32_t mPythonQtVirtualPadClear(void) {
	if (!s_bound || !s_bindings.virtualPadClear) {
		return 0;
	}
	return s_bindings.virtualPadClear(s_bindings.context);
}
