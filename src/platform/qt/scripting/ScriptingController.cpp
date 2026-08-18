/* Copyright (c) 2013-2022 Jeffrey Pfau
 *
 * Original mGBA source is credited to Jeffrey Pfau and contributors.
 * Local custom modifications in this fork were added for this workspace and
 * are not upstream mGBA work or authored by Jeffrey Pfau.
 *
 * This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */
#include "scripting/ScriptingController.h"

#include "AudioProcessor.h"
#include "CoreController.h"
#include "WindowsDarkChrome.h"
#include "Window.h"
#include "scripting/ScriptingTextBuffer.h"
#include "scripting/ScriptingTextBufferModel.h"

#include <algorithm>
#include <cstdlib>

#ifdef ENABLE_PYTHON
#include <QApplication>
#include <QByteArray>
#include <QColor>
#include <QCoreApplication>
#include <QElapsedTimer>
#include <QEventLoop>
#include <QFileInfo>
#include <QMessageBox>
#include <QPalette>
#include <QSaveFile>

#include "GBAApp.h"
#include "platform/python/lib.h"
#include "platform/python/qt.h"
#endif

using namespace QGBA;

#ifdef ENABLE_PYTHON
namespace {

bool pythonLoadStateFromBuffer(mCore* core, const QByteArray& state, int flags) {
	if (!core || state.isEmpty()) {
		return false;
	}
	VFile* vf = VFileFromConstMemory(state.constData(), state.size());
	if (!vf) {
		return false;
	}
	bool ok = mCoreLoadStateNamed(core, vf, flags);
	vf->close(vf);
	return ok;
}

bool pythonSaveStateToBuffer(mCore* core, int flags, QByteArray* out) {
	if (!core || !out) {
		return false;
	}
	VFile* vf = VFileMemChunk(nullptr, 0);
	if (!vf) {
		return false;
	}
	if (!mCoreSaveStateNamed(core, vf, flags)) {
		vf->close(vf);
		return false;
	}
	void* mapped = vf->map(vf, vf->size(vf), MAP_READ);
	*out = QByteArray(static_cast<const char*>(mapped), vf->size(vf));
	vf->close(vf);
	return true;
}

void pythonTrace(const QString& message) {
	QByteArray tracePath = qgetenv("MGBA_PYTHON_TRACE");
	if (tracePath.isEmpty()) {
		return;
	}

	QFile traceFile(QString::fromUtf8(tracePath));
	if (!traceFile.open(QIODevice::WriteOnly | QIODevice::Append | QIODevice::Text)) {
		return;
	}
	traceFile.write(message.toUtf8());
	traceFile.write("\n");
}

struct PythonQtSession {
	ScriptingController* scripting = nullptr;
	std::shared_ptr<CoreController> controller;
	CoreController::Interrupter interrupter;
	uint32_t keys = 0;
	uint32_t framesUntilEventPump = 0;
	QByteArray scratchState;
	bool skipCoreInterrupts = false;
};

constexpr const char* PYTHON_LANGUAGE = "python";
// Pumping the full Qt event loop on every scripted frame is one of the biggest
// overheads in the visible-core bridge. Keep the UI responsive, but only pay
// that cost periodically while long automation loops are running.
constexpr uint32_t PYTHON_QT_EVENT_PUMP_INTERVAL_FRAMES = 64;

uint64_t pythonSessionKey(const ScriptingController* scripting) {
	return reinterpret_cast<uint64_t>(scripting);
}

QColor pythonThemeColorForRole(int which) {
	const QPalette palette = QApplication::palette();
	switch (which) {
	case MPYQT_THEME_WINDOW:
		return palette.color(QPalette::Window);
	case MPYQT_THEME_BASE:
		return palette.color(QPalette::Base);
	case MPYQT_THEME_TEXT:
		return palette.color(QPalette::Text);
	case MPYQT_THEME_BUTTON:
		return palette.color(QPalette::Button);
	case MPYQT_THEME_HIGHLIGHT:
		return palette.color(QPalette::Highlight);
	default:
		return QColor();
	}
}

std::shared_ptr<CoreController> pythonController(PythonQtSession* session) {
	return session ? session->controller : nullptr;
}

void pythonAdoptController(PythonQtSession* session, std::shared_ptr<CoreController> controller) {
	if (!session || !controller) {
		return;
	}
	session->controller = controller;
	if (session->scripting && session->scripting->pythonScriptActive() && controller->hasStarted()) {
		controller->setScriptTimingOverride(true);
	}
	if (!session->skipCoreInterrupts && !controller->isPaused()) {
		session->interrupter.interrupt(controller);
	}
	session->keys = 0;
	session->framesUntilEventPump = 0;
}

Window* pythonWindow(PythonQtSession* session) {
	if (!GBAApp::app()) {
		return nullptr;
	}

	auto controller = pythonController(session);
	if (controller) {
		for (Window* window : GBAApp::app()->windows()) {
			if (window && window->controller() == controller) {
				return window;
			}
		}
	}

	// Runtime scripting can be opened before a ROM is loaded, so do not require
	// a controller-backed window here. Fall back to the visible Qt window so the
	// script can load the ROM into that session itself.
	if (Window* activeWindow = qobject_cast<Window*>(QApplication::activeWindow())) {
		return activeWindow;
	}

	for (Window* window : GBAApp::app()->windows()) {
		if (window && window->isVisible()) {
			return window;
		}
	}
	return nullptr;
}

bool pythonShouldAbort(PythonQtSession* session) {
	return session && session->scripting && session->scripting->pythonAbortRequested();
}

bool pythonPumpEvents(PythonQtSession* session, bool force = false, int maxMs = 1) {
	if (!session) {
		return false;
	}
	if (!force && session->framesUntilEventPump < PYTHON_QT_EVENT_PUMP_INTERVAL_FRAMES) {
		return !pythonShouldAbort(session);
	}
	session->framesUntilEventPump = 0;
	QCoreApplication::processEvents(QEventLoop::AllEvents, maxMs);
	return !pythonShouldAbort(session);
}

void pythonApplyKeys(PythonQtSession* session, uint32_t keys) {
	// The Python side works with a packed key mask. Translate that into the
	// add/clear key calls the live Qt controller already understands.
	auto controller = pythonController(session);
	if (!controller) {
		return;
	}

	uint32_t added = keys & ~session->keys;
	uint32_t removed = session->keys & ~keys;
	for (int key = 0; key < 32; ++key) {
		uint32_t mask = 1u << key;
		if (added & mask) {
			controller->addKey(key);
		}
		if (removed & mask) {
			controller->clearKey(key);
		}
	}
	session->keys = keys;
	if (controller->hasStarted() && controller->thread() && controller->thread()->core) {
		// Runtime Python advances frames by calling core->runFrame() directly
		// while the Qt thread is interrupted. That bypasses CoreController's
		// normal finishFrame()->updateKeys() path, so push the exact scripted
		// key mask into the core immediately before the next scripted frame.
		controller->thread()->core->setKeys(controller->thread()->core, keys);
	}
}

void pythonLogMessage(ScriptingController* controller, int level, const QString& message) {
	if (!controller) {
		return;
	}
	switch (level) {
	case 1:
		controller->warn(message);
		break;
	case 2:
		controller->error(message);
		break;
	default:
		controller->log(message);
		break;
	}
}

bool pythonReset(void* context) {
	PythonQtSession* session = static_cast<PythonQtSession*>(context);
	auto controller = pythonController(session);
	if (!controller || pythonShouldAbort(session) || !controller->hasStarted()) {
		return false;
	}
	// The active session already holds the controller interrupt. Avoid paying
	// that setup/teardown cost again on every reset or memory access callback.
	controller->thread()->core->reset(controller->thread()->core);
	return pythonPumpEvents(session, true);
}

bool pythonRunFrames(void* context, uint32_t frames) {
	PythonQtSession* session = static_cast<PythonQtSession*>(context);
	auto controller = pythonController(session);
	if (!controller || pythonShouldAbort(session) || !controller->hasStarted()) {
		pythonTrace(QStringLiteral("qtpy:runframes blocked controller=%1 abort=%2 started=%3")
			.arg(bool(controller))
			.arg(pythonShouldAbort(session))
			.arg(controller && controller->hasStarted()));
		return false;
	}

	// Long scripted waits are much cheaper when the frame loop stays in C++
	// instead of crossing the Python/Qt boundary once per frame.
	for (uint32_t i = 0; i < frames; ++i) {
		controller->thread()->core->runFrame(controller->thread()->core);
		++session->framesUntilEventPump;
		if (pythonShouldAbort(session)) {
			return false;
		}
		if (!pythonPumpEvents(session)) {
			return false;
		}
	}
	return !pythonShouldAbort(session);
}

bool pythonRunFrame(void* context) {
	return pythonRunFrames(context, 1);
}

bool pythonRunFramesWithKeys(void* context, uint32_t keys, uint32_t frames) {
	PythonQtSession* session = static_cast<PythonQtSession*>(context);
	// This helper is the native batching equivalent of:
	//   setKeys(keys); runFrames(frames)
	// It exists so Python seed scripts can hold one exact mask for a burst of
	// frames without paying the Python/Qt boundary cost once per frame.
	pythonApplyKeys(session, keys);
	return pythonRunFrames(context, frames);
}

bool pythonPulseKeys(void* context, uint32_t keys, uint32_t frames) {
	PythonQtSession* session = static_cast<PythonQtSession*>(context);
	if (!session) {
		return false;
	}
	const uint32_t previousKeys = session->keys;
	// `pulseKeys` is the button-pulse primitive used by the seed scripts: apply
	// one exact mask for a bounded frame window, then restore the caller's prior
	// held-mask state instead of forcing the session back to neutral.
	pythonApplyKeys(session, keys);
	const bool ok = pythonRunFrames(context, frames);
	pythonApplyKeys(session, previousKeys);
	return ok;
}

void pythonSetKeys(void* context, uint32_t keys) {
	pythonApplyKeys(static_cast<PythonQtSession*>(context), keys);
}

int32_t pythonPlatform(void* context) {
	auto controller = pythonController(static_cast<PythonQtSession*>(context));
	// Immediately after a live ROM swap the controller can still report
	// mPLATFORM_NONE for a short window. The Python side treats zero as
	// "not ready yet" rather than as a fatal mismatch.
	return controller ? controller->platform() : 0;
}

uint64_t pythonFrameCounter(void* context) {
	auto controller = pythonController(static_cast<PythonQtSession*>(context));
	return controller ? controller->frameCounter() : 0;
}

int32_t pythonAbortRequestedCallback(void* context) {
	PythonQtSession* session = static_cast<PythonQtSession*>(context);
	return pythonShouldAbort(session);
}

int32_t pythonPause(void* context) {
	auto controller = pythonController(static_cast<PythonQtSession*>(context));
	if (!controller) {
		return false;
	}
	if (controller->isPaused()) {
		// Input-tape helpers call "ensure paused" at segment boundaries. If the
		// visible core is already paused, avoid re-emitting pause-side effects or
		// pumping Qt events just to reaffirm the existing state.
		return true;
	}
	// Startup scripts normally keep the core unpaused while they step frames.
	// When a target seed is found, flip the visible controller back into the
	// normal paused UI state before showing the success dialog.
	controller->setPaused(true);
	controller->paused();
	QCoreApplication::processEvents(QEventLoop::AllEvents, 1);
	return true;
}

int32_t pythonIsPaused(void* context) {
	auto controller = pythonController(static_cast<PythonQtSession*>(context));
	return controller && controller->isPaused();
}

int32_t pythonDarkModeEnabled(void* context) {
	Q_UNUSED(context);
	return GBAApp::app() && GBAApp::app()->darkMode();
}

int32_t pythonHasStyleSheet(void* context) {
	Q_UNUSED(context);
	return GBAApp::app() && !GBAApp::app()->styleSheet().isEmpty();
}

int32_t pythonAudioKillswitchEnabled(void* context) {
	auto controller = pythonController(static_cast<PythonQtSession*>(context));
	return controller && controller->audioKillswitchEnabled();
}

bool pythonSetAudioKillswitch(void* context, int enable) {
	PythonQtSession* session = static_cast<PythonQtSession*>(context);
	Window* owner = pythonWindow(session);
	auto controller = pythonController(session);
	if (!owner || !controller) {
		return false;
	}
	const bool requested = enable != 0;
	if (controller->audioKillswitchEnabled() == requested) {
		// Scripts sometimes call this defensively on every setup path. Returning
		// early here avoids an unnecessary event pump when the live custom
		// feature is already in the requested state.
		return true;
	}
	// Route through Window rather than CoreController directly. The window path
	// also updates config, menu state, mute state, and the audio driver; skipping
	// that path can leave the core waiting on audio after scripts re-enable it.
	owner->setAudioKillswitch(requested);
	return pythonPumpEvents(session, true, 5);
}

int32_t pythonNoRenderModeEnabled(void* context) {
	auto controller = pythonController(static_cast<PythonQtSession*>(context));
	return controller && controller->noRenderModeEnabled();
}

bool pythonSetNoRenderMode(void* context, int enable) {
	PythonQtSession* session = static_cast<PythonQtSession*>(context);
	Window* owner = pythonWindow(session);
	auto controller = pythonController(session);
	if (!owner || !controller) {
		return false;
	}
	const bool requested = enable != 0;
	const bool alreadyRequested = controller->noRenderModeEnabled() == requested;
	// Even when the controller flag already matches, route through Window so a
	// rebuilt display cannot leave the black overlay or menu action stale.
	owner->setNoRenderMode(requested);
	if (alreadyRequested) {
		return true;
	}
	// Use Window::setNoRenderMode rather than CoreController directly. The
	// Window path also keeps the black overlay, Custom Features menu action, and
	// persisted config option aligned with the script-requested state.
	return pythonPumpEvents(session, true, 5);
}

int32_t pythonFastForwardEnabled(void* context) {
	auto controller = pythonController(static_cast<PythonQtSession*>(context));
	return controller && controller->fastForwardForced();
}

bool pythonSetFastForward(void* context, int enable) {
	PythonQtSession* session = static_cast<PythonQtSession*>(context);
	auto controller = pythonController(session);
	if (!controller || !controller->hasStarted()) {
		return false;
	}
	const bool requested = enable != 0;
	if (controller->fastForwardForced() == requested) {
		return true;
	}
	// Use the same forced fast-forward toggle as the Qt menu action. The active
	// Python session already owns the controller interrupt, so this cannot
	// introduce a free-running timing window.
	controller->forceFastForward(requested);
	return true;
}

bool pythonSetFastForwardRatio(void* context, float ratio) {
	PythonQtSession* session = static_cast<PythonQtSession*>(context);
	auto controller = pythonController(session);
	if (!controller || !controller->hasStarted()) {
		return false;
	}
	// mGBA uses a non-positive ratio for "Unbounded". Normalize here so scripts
	// can pass -1/0 without depending on the Qt settings UI implementation.
	controller->setFastForwardRatio(ratio > 0 ? ratio : -1.f);
	return true;
}

int32_t pythonOpenVirtualPad(void* context) {
	PythonQtSession* session = static_cast<PythonQtSession*>(context);
	Window* owner = pythonWindow(session);
	if (!owner) {
		return false;
	}
	owner->openVirtualPad();
	return pythonPumpEvents(session, true, 5);
}

int32_t pythonOpenVirtualPadSettings(void* context) {
	PythonQtSession* session = static_cast<PythonQtSession*>(context);
	Window* owner = pythonWindow(session);
	if (!owner) {
		return false;
	}
	owner->openVirtualPadSettings();
	return pythonPumpEvents(session, true, 5);
}

bool pythonVirtualPadSetHeld(void* context, int key, int value) {
	PythonQtSession* session = static_cast<PythonQtSession*>(context);
	Window* owner = pythonWindow(session);
	if (!owner || key < 0 || key >= 32) {
		return false;
	}
	const uint32_t mask = 1u << key;
	const bool requested = value != 0;
	const bool ok = owner->virtualPadSetHeld(key, requested);
	if (ok && session) {
		pythonApplyKeys(session, requested ? (session->keys | mask) : (session->keys & ~mask));
	}
	return ok && pythonPumpEvents(session, true, 5);
}

bool pythonVirtualPadSetAutofire(void* context, int key, int value) {
	PythonQtSession* session = static_cast<PythonQtSession*>(context);
	Window* owner = pythonWindow(session);
	if (!owner || key < 0 || key >= 32) {
		return false;
	}
	const bool ok = owner->virtualPadSetAutofire(key, value != 0);
	return ok && pythonPumpEvents(session, true, 5);
}

bool pythonVirtualPadPressForFrames(void* context, int key, uint32_t frames) {
	PythonQtSession* session = static_cast<PythonQtSession*>(context);
	Window* owner = pythonWindow(session);
	if (!owner || !session || key < 0 || key >= 32 || frames < 1) {
		return false;
	}

	const uint32_t previousKeys = session->keys;
	const uint32_t mask = 1u << key;
	const bool uiHeld = owner->virtualPadSetHeld(key, true);
	pythonApplyKeys(session, previousKeys | mask);
	const bool ran = pythonRunFrames(context, frames);
	pythonApplyKeys(session, previousKeys);
	owner->virtualPadSetHeld(key, false);
	pythonPause(context);
	return uiHeld && ran && pythonPumpEvents(session, true, 5);
}

uint32_t pythonVirtualPadKeyMask(void* context) {
	PythonQtSession* session = static_cast<PythonQtSession*>(context);
	Window* owner = pythonWindow(session);
	if (!owner) {
		return 0;
	}
	// A manual tape recorder samples the pad once per emulated frame. Pump Qt
	// first so mouse presses/releases that arrived since the last sample are
	// reflected in the mask that gets written to the tape.
	pythonPumpEvents(session, true, 1);
	return owner->virtualPadKeyMask();
}

uint32_t pythonControllerKeyMask(void* context) {
	PythonQtSession* session = static_cast<PythonQtSession*>(context);
	Window* owner = pythonWindow(session);
	if (!owner) {
		return 0;
	}
	// Capture mapped controller buttons, not host key codes. Pumping first lets
	// queued Qt key press/release events update CoreController::pendingKeys()
	// before the tape recorder samples this frame.
	pythonPumpEvents(session, true, 1);
	return owner->controllerKeyMask();
}

int32_t pythonVirtualPadClear(void* context) {
	PythonQtSession* session = static_cast<PythonQtSession*>(context);
	Window* owner = pythonWindow(session);
	if (!owner) {
		return false;
	}
	const bool ok = owner->virtualPadClear();
	if (session) {
		pythonApplyKeys(session, 0);
	}
	return ok && pythonPumpEvents(session, true, 5);
}

bool pythonWaitForController(PythonQtSession* session, int timeoutMs = 5000) {
	Window* owner = pythonWindow(session);
	if (!owner) {
		return false;
	}

	QElapsedTimer timer;
	timer.start();
	while (timer.elapsed() < timeoutMs) {
		auto controller = owner->controller();
		// A ROM swap can leave the existing controller in a "started" state
		// briefly while the new game is still not ready for script-side reads.
		// Wait for a real platform instead of treating "started" as sufficient.
		if (controller && controller->hasStarted()) {
			pythonAdoptController(session, controller);
			if (controller->platform() != mPLATFORM_NONE) {
				return true;
			}
		}
		QCoreApplication::processEvents(QEventLoop::AllEvents, 5);
	}

	auto controller = owner->controller();
	if (controller && controller->hasStarted()) {
		pythonAdoptController(session, controller);
		if (controller->platform() != mPLATFORM_NONE) {
			return true;
		}
	}
	return false;
}

bool pythonLoadRomFile(void* context, const char* path) {
	PythonQtSession* session = static_cast<PythonQtSession*>(context);
	Window* owner = pythonWindow(session);
	if (!owner || !path || !*path) {
		return false;
	}

	if (auto controller = owner->controller()) {
		// Reassert the session-owned interrupt before replaceGame() can touch
		// the active core. Audio killswitch/no-render remove enough host wait
		// that any unowned ROM-swap window can move the title Timer 1 seed.
		pythonAdoptController(session, controller);
	}

	if (!owner->loadScriptGame(QString::fromUtf8(path))) {
		return false;
	}

	if (!pythonWaitForController(session)) {
		return false;
	}

	session->controller->setSync(false);
	return true;
}

bool pythonLoadSaveFile(void* context, const char* path, int temporary) {
	PythonQtSession* session = static_cast<PythonQtSession*>(context);
	auto controller = pythonController(session);
	if (!controller || !path || !*path) {
		return false;
	}

	QFileInfo info(QString::fromUtf8(path));
	QString resolved = info.canonicalFilePath();
	if (resolved.isEmpty()) {
		resolved = info.absoluteFilePath();
	}
	if (!QFileInfo(resolved).isReadable()) {
		return false;
	}

	VFile* vf = VFileDevice::open(resolved, temporary ? O_RDONLY : O_RDWR);
	if (!vf) {
		return false;
	}

	// The normal Qt menu path stages save loads through a reset action. Runtime
	// Python scripts need the immediate core-level load instead so the current
	// session can keep running and then read or export the updated save data.
	bool ok = temporary
	    ? controller->thread()->core->loadTemporarySave(controller->thread()->core, vf)
	    : controller->thread()->core->loadSave(controller->thread()->core, vf);
	if (!ok) {
		vf->close(vf);
		return false;
	}
	return controller->hasStarted() && pythonPumpEvents(session, true, 5);
}

bool pythonExportSaveFile(void* context, const char* path) {
	PythonQtSession* session = static_cast<PythonQtSession*>(context);
	auto controller = pythonController(session);
	if (!controller || !path || !*path) {
		return false;
	}

	QSaveFile outfile(QString::fromUtf8(path));
	if (!outfile.open(QIODevice::WriteOnly)) {
		return false;
	}

	void* buffer = nullptr;
	ssize_t size = controller->thread()->core->savedataClone(controller->thread()->core, &buffer);
	if (size <= 0 || !buffer) {
		free(buffer);
		return false;
	}

	qint64 written = outfile.write(static_cast<const char*>(buffer), size);
	free(buffer);
	if (written != size) {
		outfile.cancelWriting();
		return false;
	}
	return outfile.commit();
}

uint32_t pythonThemeColor(void* context, int which) {
	Q_UNUSED(context);
	return pythonThemeColorForRole(which).rgba();
}

uint32_t pythonRead8(void* context, uint32_t address) {
	auto controller = pythonController(static_cast<PythonQtSession*>(context));
	if (!controller) {
		return 0;
	}
	return controller->thread()->core->busRead8(controller->thread()->core, address);
}

uint32_t pythonRead16(void* context, uint32_t address) {
	auto controller = pythonController(static_cast<PythonQtSession*>(context));
	if (!controller) {
		return 0;
	}
	return controller->thread()->core->busRead16(controller->thread()->core, address);
}

uint32_t pythonRead32(void* context, uint32_t address) {
	auto controller = pythonController(static_cast<PythonQtSession*>(context));
	if (!controller) {
		return 0;
	}
	return controller->thread()->core->busRead32(controller->thread()->core, address);
}

void pythonWrite8(void* context, uint32_t address, uint32_t value) {
	auto controller = pythonController(static_cast<PythonQtSession*>(context));
	if (!controller) {
		return;
	}
	controller->thread()->core->busWrite8(controller->thread()->core, address, static_cast<uint8_t>(value));
}

void pythonWrite16(void* context, uint32_t address, uint32_t value) {
	auto controller = pythonController(static_cast<PythonQtSession*>(context));
	if (!controller) {
		return;
	}
	controller->thread()->core->busWrite16(controller->thread()->core, address, static_cast<uint16_t>(value));
}

void pythonWrite32(void* context, uint32_t address, uint32_t value) {
	auto controller = pythonController(static_cast<PythonQtSession*>(context));
	if (!controller) {
		return;
	}
	controller->thread()->core->busWrite32(controller->thread()->core, address, value);
}

bool pythonLoadStateFile(void* context, const char* path, int flags) {
	auto controller = pythonController(static_cast<PythonQtSession*>(context));
	if (!controller || !path) {
		return false;
	}
	VFile* vf = VFileDevice::open(QString::fromUtf8(path), O_RDONLY);
	if (!vf) {
		return false;
	}
	bool ok = mCoreLoadStateNamed(controller->thread()->core, vf, flags);
	vf->close(vf);
	if (ok) {
		// Mirror the normal UI notifications so the visible window redraws after
		// a script-side savestate load.
		controller->frameAvailable();
		controller->stateLoaded();
		pythonPumpEvents(static_cast<PythonQtSession*>(context), true);
	}
	return ok;
}

bool pythonSaveStateFile(void* context, const char* path, int flags) {
	auto controller = pythonController(static_cast<PythonQtSession*>(context));
	if (!controller || !path) {
		return false;
	}
	VFile* vf = VFileDevice::open(QString::fromUtf8(path), O_RDWR | O_CREAT | O_TRUNC);
	if (!vf) {
		return false;
	}
	bool ok = mCoreSaveStateNamed(controller->thread()->core, vf, flags);
	vf->close(vf);
	if (ok) {
		pythonPumpEvents(static_cast<PythonQtSession*>(context), true);
	}
	return ok;
}

bool pythonLoadScratchState(void* context) {
	PythonQtSession* session = static_cast<PythonQtSession*>(context);
	auto controller = pythonController(session);
	if (!controller || session->scratchState.isEmpty()) {
		return false;
	}

	// Mirror the same in-memory VFile path used by the Qt savestate RAM cache.
	// The raw `core->loadState(...)` path restored too little Qt-visible state
	// for repeated runtime loops, while the named savestate path already powers
	// the stable file-backed and cached restore flows.
	//
	// In practice this keeps the Python scratch-state loop aligned with the same
	// visible-core behavior the file-backed first-half/Spinda scripts depend on.
	bool ok = pythonLoadStateFromBuffer(controller->thread()->core, session->scratchState, 0);
	if (ok) {
		// Match the visible-core refresh path used by file-backed savestate
		// loads so the window redraws from the restored checkpoint immediately.
		controller->frameAvailable();
		controller->stateLoaded();
		pythonPumpEvents(session, true);
	}
	return ok;
}

bool pythonSaveScratchState(void* context) {
	PythonQtSession* session = static_cast<PythonQtSession*>(context);
	auto controller = pythonController(session);
	if (!controller) {
		return false;
	}

	// Keep the scratch checkpoint format identical to the runtime file-backed
	// state path so the hot loop restores exactly the same state the user would
	// get from a normal savestate load.
	return pythonSaveStateToBuffer(controller->thread()->core, 0, &session->scratchState);
}

void pythonLog(void* context, int level, const char* message) {
	PythonQtSession* session = static_cast<PythonQtSession*>(context);
	pythonLogMessage(session ? session->scripting : nullptr, level, QString::fromUtf8(message ? message : ""));
}

void pythonConsoleWrite(void* context, const char* message) {
	PythonQtSession* session = static_cast<PythonQtSession*>(context);
	if (!session || !session->scripting) {
		return;
	}
	session->scripting->appendPythonConsole(QString::fromUtf8(message ? message : ""));
}

void pythonSetTextBuffer(void* context, const char* name, const char* text, uint32_t cols, uint32_t rows) {
	PythonQtSession* session = static_cast<PythonQtSession*>(context);
	if (!session || !session->scripting || !name || !*name) {
		return;
	}
	// Python status scripts only need "replace the whole named buffer" semantics.
	// That keeps the bridge narrow while still giving parity with Lua's live
	// script-buffer workflow for monitors that refresh every frame.
	session->scripting->setPythonTextBuffer(
	    QString::fromUtf8(name),
	    QString::fromUtf8(text ? text : ""),
	    QSize(static_cast<int>(cols), static_cast<int>(rows)));
}

bool pythonShowWarning(void* context, const char* title, const char* message) {
	PythonQtSession* session = static_cast<PythonQtSession*>(context);
	Window* owner = pythonWindow(session);
	QMessageBox dialog(
	    QMessageBox::Warning,
	    QString::fromUtf8(title ? title : ""),
	    QString::fromUtf8(message ? message : ""),
	    QMessageBox::Ok,
	    owner,
	    owner ? Qt::Sheet : Qt::Dialog);
	if (GBAApp::app()) {
		dialog.setPalette(GBAApp::app()->palette());
		dialog.setStyleSheet(GBAApp::app()->styleSheet());
	}
	dialog.setAttribute(Qt::WA_StyledBackground, true);
	dialog.setWindowModality(owner ? Qt::WindowModal : Qt::ApplicationModal);
	dialog.show();
	QCoreApplication::processEvents(QEventLoop::AllEvents, 1);
	applyWindowsDarkChrome(&dialog);
	return dialog.exec() == QMessageBox::Ok;
}

}
#endif

ScriptingController::ScriptingController(QObject* parent)
	: QObject(parent)
{
#ifdef ENABLE_PYTHON
	// The scripting window can exercise Python before any script is loaded, for
	// example when the user switches languages or when deployment tests open the
	// window and drive the prompt. Seed PYTHONHOME up front so those paths do
	// not depend on a launcher-side environment variable.
	mPythonEnsureEnvironment();
#endif
	m_logger.p = this;
	m_logger.log = [](mLogger* log, int, enum mLogLevel level, const char* format, va_list args) {
		Logger* logger = static_cast<Logger*>(log);
		va_list argc;
		va_copy(argc, args);
		QString message = QString::vasprintf(format, argc);
		va_end(argc);
		switch (level) {
		case mLOG_WARN:
			emit logger->p->warn(message);
			break;
		case mLOG_ERROR:
			emit logger->p->error(message);
			break;
		default:
			emit logger->p->log(message);
			break;
		}
	};

	m_bufferModel = new ScriptingTextBufferModel(this);
	QObject::connect(m_bufferModel, &ScriptingTextBufferModel::textBufferCreated, this, &ScriptingController::textBufferCreated);

	init();
	updateLanguageState();
}

ScriptingController::~ScriptingController() {
	clearController();
#ifdef ENABLE_PYTHON
	mPythonSessionReset(pythonSessionKey(this));
#endif
	mScriptContextDeinit(&m_scriptContext);
}

QStringList ScriptingController::availableLanguages() const {
	QStringList languages = m_engines.keys();
	languages.removeAll(QString::fromUtf8(PYTHON_LANGUAGE));
#ifdef ENABLE_PYTHON
	languages.append(QString::fromUtf8(PYTHON_LANGUAGE));
#endif
	std::sort(languages.begin(), languages.end(), [](const QString& lhs, const QString& rhs) {
		auto rank = [](const QString& language) {
			if (language == QLatin1String("lua")) {
				return 0;
			}
			if (language == QLatin1String(PYTHON_LANGUAGE)) {
				return 1;
			}
			return 2;
		};
		const int lhsRank = rank(lhs);
		const int rhsRank = rank(rhs);
		if (lhsRank != rhsRank) {
			return lhsRank < rhsRank;
		}
		return lhs < rhs;
	});
	return languages;
}

QString ScriptingController::activeLanguage() const {
	return m_activeLanguage;
}

bool ScriptingController::requestAbortPythonScript() {
	if (!m_pythonScriptActive) {
		pythonTrace(QStringLiteral("qtpy:abort ignored inactive"));
		return false;
	}
	pythonTrace(QStringLiteral("qtpy:abort requested"));
	m_pythonAbortRequested = true;
	return true;
}

bool ScriptingController::pythonAbortRequested() const {
	return m_pythonAbortRequested;
}

bool ScriptingController::pythonScriptActive() const {
	return m_pythonScriptActive;
}

void ScriptingController::setNextPythonSessionSkipsCoreInterrupt(bool skip) {
	m_skipNextPythonSessionCoreInterrupt = skip;
}

void ScriptingController::beginPythonScript() {
	m_pythonAbortRequested = false;
	m_pythonScriptActive = true;
}

void ScriptingController::finishPythonScript() {
	m_pythonScriptActive = false;
}

mScriptEngineContext* ScriptingController::engineForLanguage(const QString& language) const {
	return m_engines.value(language.toLower(), nullptr);
}

void ScriptingController::updateLanguageState(const QString& preferredLanguage) {
	const QStringList languages = availableLanguages();
	QString nextLanguage = preferredLanguage.isEmpty() ? m_activeLanguage : preferredLanguage;
	nextLanguage = nextLanguage.toLower();
	// Keep the visible scripting window usable even when the preferred language
	// disappears. Lua remains the safest fallback, but Python should become the
	// active language automatically on builds where it is available and Lua is
	// not the selected path.
	if (!languages.contains(nextLanguage)) {
		if (languages.contains(QStringLiteral("lua"))) {
			nextLanguage = QStringLiteral("lua");
		} else if (languages.contains(QString::fromUtf8(PYTHON_LANGUAGE))) {
			nextLanguage = QString::fromUtf8(PYTHON_LANGUAGE);
		} else if (!languages.isEmpty()) {
			nextLanguage = languages.first();
		} else {
			nextLanguage.clear();
		}
	}
	emit availableLanguagesChanged(languages);
	setActiveLanguage(nextLanguage);
}

void ScriptingController::setController(std::shared_ptr<CoreController> controller) {
	if (controller == m_controller) {
		return;
	}
#ifdef ENABLE_PYTHON
	const bool preserveActivePythonSession = m_pythonScriptActive && !m_controller;
	if (!preserveActivePythonSession) {
		clearController();
	} else {
		// A runtime Python script can be launched before any ROM is loaded. If
		// that script calls mgba.qt.load_rom(), Window::setController() reaches
		// this method while the same Python session is still executing. Do not
		// reset that session in the "no previous controller" case; otherwise the
		// first launch only boots the ROM and the user has to run the script a
		// second time from the now-existing controller.
		pythonTrace(QStringLiteral("qtpy:setController preserve active first-ROM session"));
		m_controllerStopping = false;
	}
#else
	clearController();
#endif
	m_controller = controller;
	m_controllerStopping = false;
	CoreController::Interrupter interrupter(m_controller);
	m_controller->thread()->scriptContext = &m_scriptContext;
	if (m_controller->hasStarted()) {
		mScriptContextAttachCore(&m_scriptContext, m_controller->thread()->core);
	}
	connect(m_controller.get(), &CoreController::stopping, this, [this]() {
		m_controllerStopping = true;
		clearController();
	});
}

bool ScriptingController::loadFile(const QString& path) {
	pythonTrace(QStringLiteral("qtpy:loadFile enter %1").arg(path));
	VFileDevice vf(path, QIODevice::ReadOnly);
	if (!vf.isOpen()) {
		pythonTrace(QStringLiteral("qtpy:loadFile open-failed %1").arg(path));
		return false;
	}
	bool ok = load(vf, path);
	pythonTrace(QStringLiteral("qtpy:loadFile return ok=%1 path=%2").arg(ok).arg(path));
	return ok;
}

bool ScriptingController::load(VFileDevice& vf, const QString& name) {
#ifdef ENABLE_PYTHON
	QString requestedLanguage = m_activeLanguage;
	if (name.endsWith(QLatin1String(".py"), Qt::CaseInsensitive)) {
		requestedLanguage = QString::fromUtf8(PYTHON_LANGUAGE);
	} else if (name.endsWith(QLatin1String(".lua"), Qt::CaseInsensitive)) {
		requestedLanguage = QStringLiteral("lua");
	}
	if (requestedLanguage == QLatin1String(PYTHON_LANGUAGE)) {
		return loadPythonFile(vf, name);
	}
#endif
	mScriptEngineContext* engine = engineForLanguage(requestedLanguage);
	if (!engine) {
		return false;
	}
	setActiveLanguage(requestedLanguage);
	QByteArray utf8 = name.toUtf8();
	CoreController::Interrupter interrupter(m_controller);
	if (m_controller) {
		m_controller->setSync(false);
		m_controller->unpaused();
	}
	bool ok = true;
	if (!engine->load(engine, utf8.constData(), vf) || !engine->run(engine)) {
		ok = false;
	}
	if (m_controller) {
		m_controller->setSync(true);
		if (m_controller->isPaused()) {
			m_controller->paused();
		}
	}
	return ok;
}

void ScriptingController::clearController() {
	m_pythonAbortRequested = true;
#ifdef ENABLE_PYTHON
	mPythonSessionReset(pythonSessionKey(this));
	clearPythonConsole();
#endif
	bool controllerStopping = m_controllerStopping;
	m_controllerStopping = false;
	pythonTrace(QStringLiteral("qtpy:clearController stopping=%1 hasController=%2")
		.arg(controllerStopping)
		.arg(bool(m_controller)));
	if (!m_controller) {
		m_pythonScriptActive = false;
		return;
	}

	if (!controllerStopping) {
		CoreController::Interrupter interrupter(m_controller);
		// Live controller swaps still need an immediate detach so the outgoing
		// core stops exposing emu/memory globals before a new core is attached.
		mScriptContextDetachCore(&m_scriptContext);
	}
	// When CoreController emits stopping(), the core thread has already run its
	// own shutdown callback and detached the script context. Avoid taking a
	// second interrupt/detach during teardown, which can race with window-close
	// aborts from long-running Python scripts.
	m_controller->thread()->scriptContext = nullptr;
	m_controller.reset();
	m_pythonScriptActive = false;
}

void ScriptingController::reset() {
	const QString preferredLanguage = m_activeLanguage;
	CoreController::Interrupter interrupter(m_controller);
#ifdef ENABLE_PYTHON
	mPythonSessionReset(pythonSessionKey(this));
	clearPythonConsole();
#endif
	m_bufferModel->reset();
	mScriptContextDetachCore(&m_scriptContext);
	mScriptContextDeinit(&m_scriptContext);
	m_engines.clear();
	m_activeEngine = nullptr;
	m_activeLanguage.clear();
	init();
	if (m_controller && m_controller->hasStarted()) {
		mScriptContextAttachCore(&m_scriptContext, m_controller->thread()->core);
	}
	updateLanguageState(preferredLanguage);
}

void ScriptingController::runCode(const QString& code) {
#ifdef ENABLE_PYTHON
	if (m_activeLanguage == QLatin1String(PYTHON_LANGUAGE)) {
		runPythonPrompt(code);
		return;
	}
#endif
	VFileDevice vf(code.toUtf8());
	load(vf, "*prompt");
}

void ScriptingController::setActiveLanguage(const QString& language) {
	const QString normalized = language.toLower();
	if (normalized == m_activeLanguage) {
		return;
	}
	if (!normalized.isEmpty() && !availableLanguages().contains(normalized)) {
		return;
	}
	m_activeLanguage = normalized;
	m_activeEngine = engineForLanguage(m_activeLanguage);
	emit activeLanguageChanged(m_activeLanguage);
}

void ScriptingController::init() {
	mScriptContextInit(&m_scriptContext);
	mScriptContextAttachStdlib(&m_scriptContext);
	mScriptContextAttachSocket(&m_scriptContext);
	mScriptContextRegisterEngines(&m_scriptContext);

	mScriptContextAttachLogger(&m_scriptContext, &m_logger);
	m_bufferModel->attachToContext(&m_scriptContext);

	HashTableEnumerate(&m_scriptContext.engines, [](const char* key, void* engine, void* context) {
	ScriptingController* self = static_cast<ScriptingController*>(context);
		self->m_engines[QString::fromUtf8(key)] = static_cast<mScriptEngineContext*>(engine);
	}, this);
}

#ifdef ENABLE_PYTHON
ScriptingTextBuffer* ScriptingController::ensurePythonConsoleBuffer() {
	if (m_pythonConsoleBuffer) {
		return m_pythonConsoleBuffer;
	}
	// Lua scripts already surface named buffers in the scripting window. Give
	// Python a dedicated transcript buffer so prompt output and print() calls are
	// inspectable there too, not only in the log pane.
	m_pythonConsoleBuffer = m_bufferModel->ensureNamedBuffer(tr("Python Console"));
	return m_pythonConsoleBuffer;
}

void ScriptingController::appendPythonConsole(const QString& text) {
	if (text.isEmpty()) {
		return;
	}
	ensurePythonConsoleBuffer()->print(text);
}

void ScriptingController::setPythonTextBuffer(const QString& name, const QString& text, const QSize& size) {
	ScriptingTextBuffer* buffer = m_bufferModel->ensureNamedBuffer(name);
	buffer->setSize(size);
	buffer->clear();
	if (!text.isEmpty()) {
		buffer->print(text);
	}
}

void ScriptingController::clearPythonConsole() {
	m_pythonConsoleBuffer = nullptr;
}

bool ScriptingController::runPythonSession(const QString& traceName, const std::function<bool()>& action) {
	pythonTrace(QStringLiteral("qtpy:session begin %1").arg(traceName));
	// Python parity with the Lua window depends on sharing one persistent
	// session across file loads and prompt snippets. The session key is derived
	// from this controller, so "load file" followed by prompt input behaves
	// like one long-lived interpreter instead of separate one-shot scripts.
	const bool skipCoreInterrupts = m_skipNextPythonSessionCoreInterrupt;
	m_skipNextPythonSessionCoreInterrupt = false;
	PythonQtSession session;
	session.scripting = this;
	session.skipCoreInterrupts = skipCoreInterrupts;
	pythonAdoptController(&session, m_controller);
	// Hold the core interrupt before changing sync or running Python. Audio and
	// no-render modes reduce host-side backpressure, so even a tiny unpaused
	// window before the script's first explicit runFrame() can shift Timer 1
	// based seed generation.
	// If the core is already paused, the emulator thread is not racing Python
	// and mCoreThreadInterrupt/mCoreThreadContinue can deadlock on startup
	// paths that began paused specifically for a startup script.
	//
	// This is a control-path accuracy safeguard, not a change to the emulated
	// RNG itself. The audit markdown points at this block because it is one of
	// the places where frontend timing can change which frame receives input.
	if (session.controller && session.controller->hasStarted()) {
		session.controller->setScriptTimingOverride(true);
		session.controller->setSync(false);
	}

	mPythonQtBindings bindings{};
	bindings.context = &session;
	bindings.reset = &pythonReset;
	bindings.runFrame = &pythonRunFrame;
	bindings.runFrames = &pythonRunFrames;
	bindings.runFramesWithKeys = &pythonRunFramesWithKeys;
	bindings.pulseKeys = &pythonPulseKeys;
	bindings.setKeys = &pythonSetKeys;
	bindings.platform = &pythonPlatform;
	bindings.frameCounter = &pythonFrameCounter;
	bindings.abortRequested = &pythonAbortRequestedCallback;
	bindings.pause = &pythonPause;
	bindings.isPaused = &pythonIsPaused;
	bindings.darkModeEnabled = &pythonDarkModeEnabled;
	bindings.hasStyleSheet = &pythonHasStyleSheet;
	bindings.loadRomFile = &pythonLoadRomFile;
	bindings.loadSaveFile = &pythonLoadSaveFile;
	bindings.exportSaveFile = &pythonExportSaveFile;
	bindings.themeColor = &pythonThemeColor;
	bindings.read8 = &pythonRead8;
	bindings.read16 = &pythonRead16;
	bindings.read32 = &pythonRead32;
	bindings.write8 = &pythonWrite8;
	bindings.write16 = &pythonWrite16;
	bindings.write32 = &pythonWrite32;
	bindings.loadStateFile = &pythonLoadStateFile;
	bindings.saveStateFile = &pythonSaveStateFile;
	bindings.loadScratchState = &pythonLoadScratchState;
	bindings.saveScratchState = &pythonSaveScratchState;
	bindings.log = &pythonLog;
	bindings.consoleWrite = &pythonConsoleWrite;
	bindings.showWarning = &pythonShowWarning;
	bindings.setTextBuffer = &pythonSetTextBuffer;
	bindings.audioKillswitchEnabled = &pythonAudioKillswitchEnabled;
	bindings.setAudioKillswitch = &pythonSetAudioKillswitch;
	bindings.noRenderModeEnabled = &pythonNoRenderModeEnabled;
	bindings.setNoRenderMode = &pythonSetNoRenderMode;
	bindings.fastForwardEnabled = &pythonFastForwardEnabled;
	bindings.setFastForward = &pythonSetFastForward;
	bindings.setFastForwardRatio = &pythonSetFastForwardRatio;
	bindings.openVirtualPad = &pythonOpenVirtualPad;
	bindings.openVirtualPadSettings = &pythonOpenVirtualPadSettings;
	bindings.virtualPadSetHeld = &pythonVirtualPadSetHeld;
	bindings.virtualPadSetAutofire = &pythonVirtualPadSetAutofire;
	bindings.virtualPadPressForFrames = &pythonVirtualPadPressForFrames;
	bindings.virtualPadKeyMask = &pythonVirtualPadKeyMask;
	bindings.controllerKeyMask = &pythonControllerKeyMask;
	bindings.virtualPadClear = &pythonVirtualPadClear;

	beginPythonScript();
	bool ok = false;
	bool bound = mPythonQtBind(&bindings);
	pythonTrace(QStringLiteral("qtpy:session bound=%1").arg(bound));
	if (bound) {
		mPythonEnsureEnvironment();
		ok = action();
		pythonTrace(QStringLiteral("qtpy:session action-ok=%1").arg(ok));
	}
	bool abortRequested = pythonAbortRequested();
	pythonTrace(QStringLiteral("qtpy:session abort=%1").arg(abortRequested));
	pythonApplyKeys(&session, 0);
	if (bound) {
		mPythonQtUnbind();
		pythonTrace(QStringLiteral("qtpy:session unbound"));
	}
	finishPythonScript();
	std::shared_ptr<CoreController> activeController = session.controller ? session.controller : m_controller;
	Window* activeWindow = pythonWindow(&session);
	if (activeController && activeController->hasStarted()) {
		activeController->setScriptTimingOverride(false);
	}
	session.interrupter.resume();
	if (!session.skipCoreInterrupts && activeController && activeController->hasStarted()) {
		activeController->setSync(true);
		if (activeController->isPaused()) {
			activeController->paused();
		}
	}
	if (!bound) {
		pythonTrace(QStringLiteral("qtpy:session bind-failed"));
		emit error(tr("Failed to bind the Qt/Python scripting bridge."));
		return false;
	}
	if (abortRequested) {
		// Window::closeEvent owns the retry-close logic. Once the Python bridge
		// is inactive, just report the abort and let the window-side timer retry
		// the normal close path from outside the scripting stack.
		Q_UNUSED(activeWindow);
		Q_UNUSED(activeController);
		pythonTrace(QStringLiteral("qtpy:session abort-finished"));
		return true;
	}
	pythonTrace(QStringLiteral("qtpy:session end ok=%1").arg(ok));
	return ok;
}

bool ScriptingController::loadPythonFile(VFileDevice& vf, const QString& name) {
	setActiveLanguage(QString::fromUtf8(PYTHON_LANGUAGE));
	appendPythonConsole(QStringLiteral("# load %1\n").arg(name));
	QByteArray utf8 = name.toUtf8();
	// Reuse the same session key as the prompt path below so mid-run file loads
	// and REPL commands see the same globals, matching the Lua workflow.
	bool ok = runPythonSession(name, [this, &vf, utf8]() {
		return mPythonSessionRunFile(pythonSessionKey(this), utf8.constData(), vf);
	});
	pythonTrace(QStringLiteral("qtpy:loadPythonFile return ok=%1 name=%2").arg(ok).arg(name));
	return ok;
}

bool ScriptingController::runPythonPrompt(const QString& code) {
	setActiveLanguage(QString::fromUtf8(PYTHON_LANGUAGE));
	appendPythonConsole(QStringLiteral(">>> %1\n").arg(code));
	QByteArray utf8 = code.toUtf8();
	static const char promptName[] = "*prompt";
	return runPythonSession(QString::fromUtf8(promptName), [this, utf8]() {
		return mPythonSessionRunCode(pythonSessionKey(this), promptName, utf8.constData());
	});
}
#endif
