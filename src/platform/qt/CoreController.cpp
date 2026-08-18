/* Copyright (c) 2013-2017 Jeffrey Pfau
 *
 * Original mGBA source is credited to Jeffrey Pfau and contributors.
 * Local custom modifications in this fork were added for this workspace and
 * are not upstream mGBA work or authored by Jeffrey Pfau.
 *
 * This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */
#include "CoreController.h"

#include "ConfigController.h"
#include "InputController.h"
#include "LogController.h"
#include "MultiplayerController.h"
#include "Override.h"
#include "SavestateMemoryCache.h"

#include <QAbstractButton>
#include <QDateTime>
#include <QFileInfo>
#include <QMessageBox>
#include <QMutexLocker>
#include <QSaveFile>

#include <cstdlib>

#include <mgba/core/serialize.h>
#include <mgba/core/version.h>
#include <mgba/feature/video-logger.h>
#ifdef M_CORE_GBA
#include <mgba/internal/gba/gba.h>
#include <mgba/internal/gba/renderers/cache-set.h>
#include <mgba/internal/gba/sharkport.h>
#endif
#ifdef M_CORE_GB
#include <mgba/internal/gb/gb.h>
#include <mgba/internal/gb/renderers/cache-set.h>
#endif
#include "feature/sqlite3/no-intro.h"
#include <mgba-util/math.h>
#include <mgba-util/vfs.h>

#define AUTOSAVE_GRANULARITY 600

using namespace QGBA;

namespace {

QByteArray readAllVFile(VFile* vf) {
	QByteArray data;
	if (!vf) {
		return data;
	}

	ssize_t size = vf->size(vf);
	if (size <= 0) {
		return data;
	}

	data.resize(size);
	ssize_t read = vf->read(vf, data.data(), data.size());
	if (read != size) {
		return QByteArray();
	}
	return data;
}

bool loadStateFromBuffer(mCore* core, const QByteArray& state, int flags) {
	if (state.isEmpty()) {
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

bool saveStateToBuffer(mCore* core, int flags, QByteArray* out) {
	if (!out) {
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

bool copyVFileContents(VFile* source, VFile* target) {
	if (!source || !target) {
		return false;
	}

	if (source->seek(source, 0, SEEK_SET) < 0 || target->seek(target, 0, SEEK_SET) < 0) {
		return false;
	}
	target->truncate(target, 0);

	uint8_t buffer[8192];
	ssize_t read = 0;
	while ((read = source->read(source, buffer, sizeof(buffer))) > 0) {
		size_t remaining = static_cast<size_t>(read);
		uint8_t* cursor = buffer;
		while (remaining) {
			ssize_t written = target->write(target, cursor, remaining);
			if (written <= 0) {
				return false;
			}
			cursor += written;
			remaining -= static_cast<size_t>(written);
		}
	}
	if (read < 0) {
		return false;
	}
	return target->seek(target, 0, SEEK_SET) >= 0;
}

}

CoreController::CoreController(mCore* core, QObject* parent)
	: QObject(parent)
	, m_loadStateFlags(SAVESTATE_SCREENSHOT | SAVESTATE_RTC)
	, m_saveStateFlags(SAVESTATE_SCREENSHOT | SAVESTATE_SAVEDATA | SAVESTATE_CHEATS | SAVESTATE_RTC)
	, m_savestateCache(std::make_unique<SavestateMemoryCache>())
{
	m_threadContext.core = core;
	m_threadContext.userData = this;
	updateROMInfo();

#ifdef M_CORE_GBA
	GBASIODolphinCreate(&m_dolphin);
#endif

	m_resetActions.append([this]() {
		if (m_autoload) {
			mCoreLoadState(m_threadContext.core, 0, m_loadStateFlags);
		}
	});

	m_threadContext.startCallback = [](mCoreThread* context) {
		CoreController* controller = static_cast<CoreController*>(context->userData);

		switch (context->core->platform(context->core)) {
#ifdef M_CORE_GBA
		case mPLATFORM_GBA:
			context->core->setPeripheral(context->core, mPERIPH_GBA_LUMINANCE, controller->m_inputController->luminance());
			break;
#endif
		default:
			break;
		}

		controller->updateFastForward();

		if (controller->m_multiplayer) {
			controller->m_multiplayer->attachGame(controller);
			controller->updatePlayerSave();
		}

		QMetaObject::invokeMethod(controller, "started");
	};

	m_threadContext.resetCallback = [](mCoreThread* context) {
		CoreController* controller = static_cast<CoreController*>(context->userData);
		for (auto action : controller->m_resetActions) {
			action();
		}

		if (controller->m_override) {
			controller->m_override->identify(context->core);
			controller->m_override->apply(context->core);
		}

		controller->m_resetActions.clear();
		controller->m_frameCounter = -1;

		if (!controller->m_hwaccel) {
			context->core->setVideoBuffer(context->core, reinterpret_cast<color_t*>(controller->m_activeBuffer.data()), controller->screenDimensions().width());
		}

		QString message(tr("Reset r%1-%2 %3").arg(gitRevision).arg(QLatin1String(gitCommitShort)).arg(controller->m_crc32, 8, 16, QLatin1Char('0')));
		QMetaObject::invokeMethod(controller, "didReset");
		if (controller->m_showResetInfo) {
			QMetaObject::invokeMethod(controller, "statusPosted", Q_ARG(const QString&, message));
		}
		if (controller->m_startPaused) {
			// Runtime Python can create the visible core by loading a ROM from
			// the scripting window. Pause at the reset boundary, before the main
			// emulation loop can run, so the script gets the first intentional
			// frame via runFrame()/runFrames() instead of a host-timing race.
			mCoreThreadPauseFromThread(context);
			controller->m_startPaused = false;
		}
		controller->finishFrame();
	};

	m_threadContext.frameCallback = [](mCoreThread* context) {
		CoreController* controller = static_cast<CoreController*>(context->userData);

		if (controller->m_autosaveCounter == AUTOSAVE_GRANULARITY) {
			if (controller->m_autosave) {
				mCoreSaveState(context->core, 0, controller->m_saveStateFlags);
			}
			controller->m_autosaveCounter = 0;
		}
		++controller->m_autosaveCounter;

		controller->finishFrame();
	};

	m_threadContext.cleanCallback = [](mCoreThread* context) {
		CoreController* controller = static_cast<CoreController*>(context->userData);

		if (controller->m_autosave) {
			mCoreSaveState(context->core, 0, controller->m_saveStateFlags);
		}

		controller->clearMultiplayerController();
#ifdef M_CORE_GBA
		controller->detachDolphin();
#endif
		QMetaObject::invokeMethod(controller, "stopping");
	};

	m_threadContext.pauseCallback = [](mCoreThread* context) {
		CoreController* controller = static_cast<CoreController*>(context->userData);

		QMetaObject::invokeMethod(controller, "paused");
	};

	m_threadContext.unpauseCallback = [](mCoreThread* context) {
		CoreController* controller = static_cast<CoreController*>(context->userData);

		QMetaObject::invokeMethod(controller, "unpaused");
	};

	m_logger.self = this;
	m_logger.log = [](mLogger* logger, int category, enum mLogLevel level, const char* format, va_list args) {
		CoreLogger* logContext = static_cast<CoreLogger*>(logger);

		static const char* savestateMessage = "State %i saved";
		static const char* loadstateMessage = "State %i loaded";
		static const char* savestateFailedMessage = "State %i failed to load";
		static int biosCat = -1;
		static int statusCat = -1;
		if (!logContext) {
			return;
		}
		CoreController* controller = logContext->self;
		QString message;
		if (biosCat < 0) {
			biosCat = mLogCategoryById("gba.bios");
		}
		if (statusCat < 0) {
			statusCat = mLogCategoryById("core.status");
		}
#ifdef M_CORE_GBA
		if (level == mLOG_STUB && category == biosCat) {
			va_list argc;
			va_copy(argc, args);
			int immediate = va_arg(argc, int);
			va_end(argc);
			QMetaObject::invokeMethod(controller, "unimplementedBiosCall", Q_ARG(int, immediate));
		} else
#endif
		if (category == statusCat) {
			// Slot 0 is reserved for suspend points
			if (strncmp(loadstateMessage, format, strlen(loadstateMessage)) == 0) {
				va_list argc;
				va_copy(argc, args);
				int slot = va_arg(argc, int);
				va_end(argc);
				if (slot == 0) {
					format = "Loaded suspend state";
				}
			} else if (strncmp(savestateFailedMessage, format, strlen(savestateFailedMessage)) == 0 || strncmp(savestateMessage, format, strlen(savestateMessage)) == 0) {
				va_list argc;
				va_copy(argc, args);
				int slot = va_arg(argc, int);
				va_end(argc);
				if (slot == 0) {
					return;
				}
			}
			va_list argc;
			va_copy(argc, args);
			message = QString::vasprintf(format, argc);
			va_end(argc);
			QMetaObject::invokeMethod(controller, "statusPosted", Q_ARG(const QString&, message));
		}
		message = QString::vasprintf(format, args);
		QMetaObject::invokeMethod(controller, "logPosted", Q_ARG(int, level), Q_ARG(int, category), Q_ARG(const QString&, message));
		if (level == mLOG_FATAL) {
			QMetaObject::invokeMethod(controller, "crashed", Q_ARG(const QString&, message));
		}
	};
	m_threadContext.logger.logger = &m_logger;
}

CoreController::~CoreController() {
	endVideoLog();
	stop();
	disconnect();

	mCoreThreadJoin(&m_threadContext);

	if (m_cacheSet) {
		mCacheSetDeinit(m_cacheSet.get());
		m_cacheSet.reset();
	}

	mCoreConfigDeinit(&m_threadContext.core->config);
	m_threadContext.core->deinit(m_threadContext.core);
}

const color_t* CoreController::drawContext() {
	if (m_hwaccel) {
		return nullptr;
	}
	QMutexLocker locker(&m_bufferMutex);
	return reinterpret_cast<const color_t*>(m_completeBuffer.constData());
}

QImage CoreController::getPixels() {
	QByteArray buffer;
	QSize size = screenDimensions();
	size_t stride = size.width() * BYTES_PER_PIXEL;

	if (!m_hwaccel) {
		buffer = m_completeBuffer;
	} else {
		Interrupter interrupter(this);
		const void* pixels;
		m_threadContext.core->getPixels(m_threadContext.core, &pixels, &stride);
		stride *= BYTES_PER_PIXEL;
		buffer = QByteArray::fromRawData(static_cast<const char*>(pixels), stride * size.height());
	}

	QImage image(reinterpret_cast<const uchar*>(buffer.constData()),
	             size.width(), size.height(), stride, QImage::Format_RGBX8888);
	image.bits(); // Cause QImage to detach
	return image;
}

bool CoreController::isPaused() {
	return mCoreThreadIsPaused(&m_threadContext);
}

bool CoreController::hasStarted() {
	return mCoreThreadHasStarted(&m_threadContext);
}

mPlatform CoreController::platform() const {
	return m_threadContext.core->platform(m_threadContext.core);
}

QSize CoreController::screenDimensions() const {
	unsigned width, height;
	m_threadContext.core->desiredVideoDimensions(m_threadContext.core, &width, &height);

	return QSize(width, height);
}

void CoreController::loadConfig(ConfigController* config) {
	Interrupter interrupter(this);
	m_loadStateFlags = config->getOption("loadStateExtdata", m_loadStateFlags).toInt();
	m_saveStateFlags = config->getOption("saveStateExtdata", m_saveStateFlags).toInt();
	// Local workspace addition: persist the Qt menu-bar fast-forward toggle
	// without touching the live sync state until the core thread is ready.
	m_fastForwardForced = config->getOption("fastForward", m_fastForwardForced).toInt();
	m_fastForwardRatio = config->getOption("fastForwardRatio", m_fastForwardRatio).toFloat();
	m_fastForwardHeldRatio = config->getOption("fastForwardHeldRatio", m_fastForwardRatio).toFloat();
	m_videoSync = config->getOption("videoSync", m_videoSync).toInt();
	m_audioSync = config->getOption("audioSync", m_audioSync).toInt();
	m_fpsTarget = config->getOption("fpsTarget").toFloat();
	m_audioKillswitch = config->getOption("customAudioKillswitch", m_audioKillswitch).toInt();
	m_noRenderMode = config->getOption("customNoRenderMode", m_noRenderMode).toInt();
	m_savestateCacheEnabled = config->getOption("customSavestateCacheEnabled", m_savestateCacheEnabled).toInt();
	m_savestateCacheMaxEntries = config->getOption("customSavestateCacheMaxEntries", m_savestateCacheMaxEntries).toInt();
	m_savestateCacheMaxBytes = config->getOption("customSavestateCacheMaxMB", static_cast<qulonglong>(m_savestateCacheMaxBytes / 1024 / 1024)).toULongLong() * 1024ull * 1024ull;
	m_autosave = config->getOption("autosave", false).toInt();
	m_autoload = config->getOption("autoload", true).toInt();
	m_autofireThreshold = config->getOption("autofireThreshold", m_autofireThreshold).toInt();
	m_fastForwardVolume = config->getOption("fastForwardVolume", -1).toInt();
	m_fastForwardMute = config->getOption("fastForwardMute", -1).toInt();
	mCoreConfigCopyValue(&m_threadContext.core->config, config->config(), "volume");
	mCoreConfigCopyValue(&m_threadContext.core->config, config->config(), "mute");
	m_preload = config->getOption("preload").toInt();

	QSize sizeBefore = screenDimensions();
	m_activeBuffer.resize(256 * 224 * sizeof(color_t));
	m_threadContext.core->setVideoBuffer(m_threadContext.core, reinterpret_cast<color_t*>(m_activeBuffer.data()), sizeBefore.width());

	mCoreLoadForeignConfig(m_threadContext.core, config->config());
	configureSavestateMemoryCache(m_savestateCacheEnabled, m_savestateCacheMaxEntries, m_savestateCacheMaxBytes);
	applyNoRenderMode();
	if (hasStarted()) {
		updateFastForward();
	}

	QSize sizeAfter = screenDimensions();
	m_activeBuffer.resize(sizeAfter.width() * sizeAfter.height() * sizeof(color_t));
	m_threadContext.core->setVideoBuffer(m_threadContext.core, reinterpret_cast<color_t*>(m_activeBuffer.data()), sizeAfter.width());

	if (hasStarted()) {
		updateFastForward();
		mCoreThreadRewindParamsChanged(&m_threadContext);
	}
#ifdef M_CORE_GB
	if (sizeBefore != sizeAfter) {
		mCoreConfigSetIntValue(&m_threadContext.core->config, "sgb.borders", 0);
		m_threadContext.core->reloadConfigOption(m_threadContext.core, "sgb.borders", nullptr);
		mCoreConfigCopyValue(&m_threadContext.core->config, config->config(), "sgb.borders");
		m_threadContext.core->reloadConfigOption(m_threadContext.core, "sgb.borders", nullptr);
	}
	m_threadContext.core->reloadConfigOption(m_threadContext.core, "gb.pal", config->config());
#endif
}

#ifdef USE_DEBUGGERS
void CoreController::setDebugger(mDebugger* debugger) {
	Interrupter interrupter(this);
	if (debugger) {
		mDebuggerAttach(debugger, m_threadContext.core);
		mDebuggerEnter(debugger, DEBUGGER_ENTER_ATTACHED, 0);
	} else {
		m_threadContext.core->detachDebugger(m_threadContext.core);
	}
}
#endif

void CoreController::setMultiplayerController(MultiplayerController* controller) {
	if (controller == m_multiplayer) {
		return;
	}
	clearMultiplayerController();
	m_multiplayer = controller;
	if (!mCoreThreadHasStarted(&m_threadContext)) {
		return;
	}
	mCoreThreadRunFunction(&m_threadContext, [](mCoreThread* thread) {
		CoreController* controller = static_cast<CoreController*>(thread->userData);
		controller->m_multiplayer->attachGame(controller);
	});
}

void CoreController::clearMultiplayerController() {
	if (!m_multiplayer) {
		return;
	}
	m_multiplayer->detachGame(this);
	m_multiplayer = nullptr;
}

mCacheSet* CoreController::graphicCaches() {
	if (m_cacheSet) {
		return m_cacheSet.get();
	}
	Interrupter interrupter(this);
	switch (platform()) {
#ifdef M_CORE_GBA
	case mPLATFORM_GBA: {
		GBA* gba = static_cast<GBA*>(m_threadContext.core->board);
		m_cacheSet = std::make_unique<mCacheSet>();
		GBAVideoCacheInit(m_cacheSet.get());
		GBAVideoCacheAssociate(m_cacheSet.get(), &gba->video);
		break;
	}
#endif
#ifdef M_CORE_GB
	case mPLATFORM_GB: {
		GB* gb = static_cast<GB*>(m_threadContext.core->board);
		m_cacheSet = std::make_unique<mCacheSet>();
		GBVideoCacheInit(m_cacheSet.get());
		GBVideoCacheAssociate(m_cacheSet.get(), &gb->video);
		break;
	}
#endif
	default:
		return nullptr;
	}
	return m_cacheSet.get();
}

#ifdef M_CORE_GBA
bool CoreController::attachDolphin(const Address& address) {
	if (platform() != mPLATFORM_GBA) {
		return false;
	}
	if (GBASIODolphinConnect(&m_dolphin, &address, 0, 0)) {
		GBA* gba = static_cast<GBA*>(m_threadContext.core->board);
		GBASIOSetDriver(&gba->sio, &m_dolphin.d, SIO_JOYBUS);
		return true;
	}
	return false;
}

void CoreController::detachDolphin() {
	if (platform() == mPLATFORM_GBA) {
		GBA* gba = static_cast<GBA*>(m_threadContext.core->board);
		GBASIOSetDriver(&gba->sio, nullptr, SIO_JOYBUS);
	}
	GBASIODolphinDestroy(&m_dolphin);
}
#endif

void CoreController::setOverride(std::unique_ptr<Override> override) {
	Interrupter interrupter(this);
	m_override = std::move(override);
	m_override->identify(m_threadContext.core);
}

void CoreController::setInputController(InputController* inputController) {
	m_inputController = inputController;
	m_threadContext.core->setPeripheral(m_threadContext.core, mPERIPH_ROTATION, m_inputController->rotationSource());
	m_threadContext.core->setPeripheral(m_threadContext.core, mPERIPH_RUMBLE, m_inputController->rumble());
	m_threadContext.core->setPeripheral(m_threadContext.core, mPERIPH_IMAGE_SOURCE, m_inputController->imageSource());
}

void CoreController::setLogger(LogController* logger) {
	disconnect(m_log);
	m_log = logger;
	m_logger.filter = logger->filter();
	connect(this, &CoreController::logPosted, m_log, &LogController::postLog);
}

void CoreController::start(bool startPaused) {
	m_startPaused = startPaused;
	QSize size(screenDimensions());
	m_activeBuffer.resize(size.width() * size.height() * sizeof(color_t));
	m_activeBuffer.fill(0xFF);
	m_completeBuffer = m_activeBuffer;

	m_threadContext.core->setVideoBuffer(m_threadContext.core, reinterpret_cast<color_t*>(m_activeBuffer.data()), size.width());

	if (!m_patched) {
		mCoreAutoloadPatch(m_threadContext.core);
	}
	if (!mCoreThreadStart(&m_threadContext)) {
		m_startPaused = false;
		emit failed();
		emit stopping();
	}
}

void CoreController::stop() {
	setSync(false);
#ifdef USE_DEBUGGERS
	setDebugger(nullptr);
#endif
	setPaused(false);
	mCoreThreadEnd(&m_threadContext);
}

void CoreController::reset() {
	mCoreThreadReset(&m_threadContext);
}

void CoreController::setPaused(bool paused) {
	QMutexLocker locker(&m_actionMutex);
	if (paused) {
		if (m_moreFrames < 0) {
			m_moreFrames = 1;
		}
	} else {
		m_moreFrames = -1;
		if (isPaused()) {
			mCoreThreadUnpause(&m_threadContext);
		}
	}
}

void CoreController::frameAdvance() {
	QMutexLocker locker(&m_actionMutex);
	// frameAdvance() wakes a paused core for exactly one emulated frame. Commit
	// the pending GUI/Virtual Pad/Input Tape mask first, otherwise the frame we
	// just woke can run with stale input from before the pause boundary.
	installPendingKeysForBoundedStep();
	m_moreFrames = 1;
	if (isPaused()) {
		mCoreThreadUnpause(&m_threadContext);
	}
}

void CoreController::addFrameAction(std::function<void ()> action) {
	QMutexLocker locker(&m_actionMutex);
	m_frameActions.append(action);
}

void CoreController::setSync(bool sync) {
	if (!m_threadContext.impl) {
		return;
	}
	const bool audioWait = sync && m_audioSync && !m_audioKillswitch && !m_audioSyncBlocked;
	MutexLock(&m_threadContext.impl->sync.audioBufferMutex);
	m_threadContext.impl->sync.audioWait = audioWait;
	if (!audioWait) {
		// Wake a core already blocked in mCoreSyncProduceAudio(). Simply
		// flipping audioWait is not enough because that thread is asleep on this
		// condition until the frontend consumes audio or explicitly wakes it.
		ConditionWake(&m_threadContext.impl->sync.audioRequiredCond);
	}
	MutexUnlock(&m_threadContext.impl->sync.audioBufferMutex);

	if (sync) {
		m_threadContext.impl->sync.videoFrameWait = m_videoSync;
	} else {
		m_threadContext.impl->sync.videoFrameWait = false;
	}
}

void CoreController::showResetInfo(bool enable) {
	m_showResetInfo = enable;
}

void CoreController::setRewinding(bool rewind) {
	if (!m_threadContext.core->opts.rewindEnable) {
		if (rewind) {
			emit statusPosted(tr("Rewinding not currently enabled"));
		}
		return;
	}
	if (rewind && m_multiplayer && m_multiplayer->attached() > 1) {
		return;
	}

	if (rewind && isPaused()) {
		setPaused(false);
		// TODO: restore autopausing
	}
	mCoreThreadSetRewinding(&m_threadContext, rewind);
}

void CoreController::rewind(int states) {
	if (!m_threadContext.core->opts.rewindEnable) {
		emit statusPosted(tr("Rewinding not currently enabled"));
	}
	Interrupter interrupter(this);
	if (!states) {
		states = INT_MAX;
	}
	for (int i = 0; i < states; ++i) {
		if (!mCoreRewindRestore(&m_threadContext.impl->rewind, m_threadContext.core)) {
			break;
		}
	}
	interrupter.resume();
	emit frameAvailable();
	emit rewound();
}

void CoreController::setFastForward(bool enable) {
	if (m_fastForward == enable) {
		return;
	}
	m_fastForward = enable;
	updateFastForward();
	emit fastForwardChanged(enable);
}

void CoreController::forceFastForward(bool enable) {
	if (m_fastForwardForced == enable) {
		return;
	}
	m_fastForwardForced = enable;
	updateFastForward();
	emit fastForwardChanged(enable || m_fastForward);
}

void CoreController::setFastForwardRatio(float ratio) {
	float normalizedRatio = ratio > 0 ? ratio : -1.f;
	if (m_fastForwardRatio == normalizedRatio) {
		return;
	}
	m_fastForwardRatio = normalizedRatio;
	// A non-positive ratio is mGBA's existing "Unbounded" speed setting. Apply
	// through updateFastForward() so the live sync flags match the menu path.
	updateFastForward();
}

void CoreController::changePlayer(int id) {
	Interrupter interrupter(this);
	int playerId = 0;
	mCoreConfigGetIntValue(&m_threadContext.core->config, "savePlayerId", &playerId);
	if (id == playerId) {
		return;
	}
	interrupter.resume();

	QMessageBox* resetPrompt = new QMessageBox(QMessageBox::Question, tr("Reset the game?"),
		tr("Most games will require a reset to load the new save. Do you want to reset now?"),
		QMessageBox::Yes | QMessageBox::No | QMessageBox::Cancel);
	connect(resetPrompt, &QMessageBox::buttonClicked, this, [this, resetPrompt, id](QAbstractButton* button) {
		Interrupter interrupter(this);
		switch (resetPrompt->standardButton(button)) {
		default:
			return;
		case QMessageBox::Yes:
			mCoreConfigSetOverrideIntValue(&m_threadContext.core->config, "savePlayerId", id);
			m_resetActions.append([this]() {
				updatePlayerSave();
			});
			interrupter.resume();
			reset();
			break;
		case QMessageBox::No:
			mCoreConfigSetOverrideIntValue(&m_threadContext.core->config, "savePlayerId", id);
			updatePlayerSave();
			break;
		}
	});
	resetPrompt->setAttribute(Qt::WA_DeleteOnClose);
	resetPrompt->show();
}

void CoreController::overrideMute(bool override) {
	m_mute = override;

	Interrupter interrupter(this);
	mCore* core = m_threadContext.core;
	if (m_mute || m_audioKillswitch) {
		core->opts.mute = true;
	} else {
		if (m_fastForward || m_fastForwardForced) {
			core->opts.mute = m_fastForwardMute >= 0;
		} else {
			mCoreConfigGetBoolValue(&core->config, "mute", &core->opts.mute);
		}
	}
	core->reloadConfigOption(core, NULL, NULL);
}

void CoreController::setAudioSyncBlocked(bool blocked) {
	if (m_audioSyncBlocked == blocked) {
		return;
	}

	m_audioSyncBlocked = blocked;
	if (!m_threadContext.impl) {
		return;
	}

	// If the frontend audio driver is stopped or failed, audio sync would leave
	// the emulation thread waiting for samples nobody will consume. Recompute the
	// sync state immediately while preserving all other configured speed options.
	Interrupter interrupter(this);
	updateFastForward();
}

void CoreController::setAudioKillswitch(bool enable) {
	if (m_audioKillswitch == enable) {
		return;
	}
	m_audioKillswitch = enable;
	Interrupter interrupter(this);
	updateFastForward();
}

void CoreController::setNoRenderMode(bool enable) {
	if (m_noRenderMode == enable) {
		return;
	}
	m_noRenderMode = enable;
	// Route the toggle through the controller interrupt so the display-side
	// presentation state changes without letting the core free-run between the
	// menu action/script call and the next deterministic frame step.
	Interrupter interrupter(this);
	applyNoRenderMode();
	interrupter.resume();
	emit frameAvailable();
}

void CoreController::setScriptTimingOverride(bool enable) {
	if (m_scriptTimingOverride == enable) {
		return;
	}
	m_scriptTimingOverride = enable;
	if (!m_threadContext.impl) {
		return;
	}

	updateFastForward();
}

void CoreController::configureSavestateMemoryCache(bool enabled, int maxEntries, quint64 maxBytes) {
	// The UI stores the cache limits in human-friendly units, but the hot path
	// only needs one normalized cache configuration at runtime.
	m_savestateCacheEnabled = enabled;
	m_savestateCacheMaxEntries = qMax(1, maxEntries);
	m_savestateCacheMaxBytes = qMax<quint64>(1024ull * 1024ull, maxBytes);
	if (m_savestateCache) {
		m_savestateCache->configure(m_savestateCacheMaxEntries, m_savestateCacheMaxBytes);
		m_savestateCache->setEnabled(m_savestateCacheEnabled);
	}
}

void CoreController::loadState(int slot) {
	if (slot > 0 && slot != m_stateSlot) {
		m_stateSlot = slot;
		m_backupSaveState.clear();
	}
	mCoreThreadClearCrashed(&m_threadContext);
	mCoreThreadRunFunction(&m_threadContext, [](mCoreThread* context) {
		CoreController* controller = static_cast<CoreController*>(context->userData);
		const QString cacheKey = controller->savestateCacheKeyForSlot(controller->m_stateSlot);
		QByteArray cachedState;
		if (!controller->m_backupLoadState.isOpen()) {
			controller->m_backupLoadState = VFileDevice::openMemory();
		}
		mCoreSaveStateNamed(context->core, controller->m_backupLoadState, controller->m_saveStateFlags);
		bool loaded = controller->m_savestateCacheEnabled
			&& controller->m_savestateCache
			&& controller->m_savestateCache->load(cacheKey, &cachedState)
			&& loadStateFromBuffer(context->core, cachedState, controller->m_loadStateFlags);
		if (!loaded) {
			// Fall back to the normal disk-backed slot load when the in-memory
			// cache has no copy yet or the cached payload is unusable.
			loaded = mCoreLoadState(context->core, controller->m_stateSlot, controller->m_loadStateFlags);
			if (loaded && controller->m_savestateCacheEnabled && controller->m_savestateCache) {
				VFile* slotState = mCoreGetState(context->core, controller->m_stateSlot, false);
				if (slotState) {
					QByteArray stateBytes = readAllVFile(slotState);
					slotState->close(slotState);
					if (!stateBytes.isEmpty()) {
						// Populate the RAM cache after the first successful disk
						// load so the second retry can stay in memory.
						controller->m_savestateCache->store(cacheKey, stateBytes);
					}
				}
			}
		}
		if (loaded) {
			emit controller->frameAvailable();
			emit controller->stateLoaded();
		}
	});
}

void CoreController::loadState(const QString& path, int flags) {
	m_statePath = path;
	int savedFlags = m_loadStateFlags;
	if (flags != -1) {
		m_loadStateFlags = flags;
	}
	mCoreThreadClearCrashed(&m_threadContext);
	mCoreThreadRunFunction(&m_threadContext, [](mCoreThread* context) {
		CoreController* controller = static_cast<CoreController*>(context->userData);
		const QString cacheKey = controller->savestateCacheKeyForPath(controller->m_statePath);
		QByteArray stateBytes;
		if (!controller->m_backupLoadState.isOpen()) {
			controller->m_backupLoadState = VFileDevice::openMemory();
		}
		mCoreSaveStateNamed(context->core, controller->m_backupLoadState, controller->m_saveStateFlags);
		bool loaded = controller->m_savestateCacheEnabled
			&& controller->m_savestateCache
			&& controller->m_savestateCache->load(cacheKey, &stateBytes);
		if (!loaded) {
			VFile* vf = VFileDevice::open(controller->m_statePath, O_RDONLY);
			if (!vf) {
				return;
			}
			stateBytes = readAllVFile(vf);
			vf->close(vf);
			if (!stateBytes.isEmpty() && controller->m_savestateCacheEnabled && controller->m_savestateCache) {
				// Cache external state files after the first read so repeated
				// route retries can stay hot in RAM.
				controller->m_savestateCache->store(cacheKey, stateBytes);
			}
		}
		if (loadStateFromBuffer(context->core, stateBytes, controller->m_loadStateFlags)) {
			emit controller->frameAvailable();
			emit controller->stateLoaded();
		}
	});
	m_loadStateFlags = savedFlags;
}

void CoreController::loadState(QIODevice* iodev, int flags) {
	m_stateVf = VFileDevice::wrap(iodev, QIODevice::ReadOnly);
	if (!m_stateVf) {
		return;
	}
	int savedFlags = m_loadStateFlags;
	if (flags != -1) {
		m_loadStateFlags = flags;
	}
	mCoreThreadClearCrashed(&m_threadContext);
	mCoreThreadRunFunction(&m_threadContext, [](mCoreThread* context) {
		CoreController* controller = static_cast<CoreController*>(context->userData);
		VFile* vf = controller->m_stateVf;
		if (!vf) {
			return;
		}
		if (!controller->m_backupLoadState.isOpen()) {
			controller->m_backupLoadState = VFileDevice::openMemory();
		}
		mCoreSaveStateNamed(context->core, controller->m_backupLoadState, controller->m_saveStateFlags);
		if (mCoreLoadStateNamed(context->core, vf, controller->m_loadStateFlags)) {
			emit controller->frameAvailable();
			emit controller->stateLoaded();
		}
		vf->close(vf);
	});
	m_loadStateFlags = savedFlags;
}

void CoreController::saveState(int slot) {
	if (slot > 0) {
		m_stateSlot = slot;
	}
	mCoreThreadRunFunction(&m_threadContext, [](mCoreThread* context) {
		CoreController* controller = static_cast<CoreController*>(context->userData);
		const QString cacheKey = controller->savestateCacheKeyForSlot(controller->m_stateSlot);
		VFile* vf = mCoreGetState(context->core, controller->m_stateSlot, false);
		if (vf) {
			controller->m_backupSaveState.resize(vf->size(vf));
			vf->read(vf, controller->m_backupSaveState.data(), controller->m_backupSaveState.size());
			vf->close(vf);
		}
		mCoreSaveState(context->core, controller->m_stateSlot, controller->m_saveStateFlags);
		if (controller->m_savestateCacheEnabled && controller->m_savestateCache) {
			QByteArray stateBytes;
			if (saveStateToBuffer(context->core, controller->m_saveStateFlags, &stateBytes) && !stateBytes.isEmpty()) {
				// Reuse a second in-memory serialization instead of rereading the
				// freshly written slot file from disk. This keeps hot save loops
				// on the CPU side when the RAM cache is enabled.
				controller->m_savestateCache->store(cacheKey, stateBytes);
			}
		}
	});
}

void CoreController::saveState(const QString& path, int flags) {
	m_statePath = path;
	int savedFlags = m_saveStateFlags;
	if (flags != -1) {
		m_saveStateFlags = flags;
	}
	mCoreThreadRunFunction(&m_threadContext, [](mCoreThread* context) {
		CoreController* controller = static_cast<CoreController*>(context->userData);
		const QString cacheKey = controller->savestateCacheKeyForPath(controller->m_statePath);
		VFile* vf = VFileDevice::open(controller->m_statePath, O_RDONLY);
		if (vf) {
			controller->m_backupSaveState.resize(vf->size(vf));
			vf->read(vf, controller->m_backupSaveState.data(), controller->m_backupSaveState.size());
			vf->close(vf);
		}
		QByteArray stateBytes;
		if (controller->m_savestateCacheEnabled && controller->m_savestateCache && saveStateToBuffer(context->core, controller->m_saveStateFlags, &stateBytes)) {
			// Save-to-memory first when the cache is enabled so the same payload
			// can satisfy the next load without another disk read.
			controller->m_savestateCache->store(cacheKey, stateBytes);
			vf = VFileDevice::open(controller->m_statePath, O_RDWR | O_CREAT | O_TRUNC);
			if (!vf) {
				return;
			}
			vf->write(vf, stateBytes.constData(), stateBytes.size());
			vf->close(vf);
			return;
		}
		vf = VFileDevice::open(controller->m_statePath, O_RDWR | O_CREAT | O_TRUNC);
		if (!vf) {
			return;
		}
		mCoreSaveStateNamed(context->core, vf, controller->m_saveStateFlags);
		vf->close(vf);
	});
	m_saveStateFlags = savedFlags;
}

void CoreController::saveState(QIODevice* iodev, int flags) {
	m_stateVf = VFileDevice::wrap(iodev, QIODevice::WriteOnly | QIODevice::Truncate);
	if (!m_stateVf) {
		return;
	}
	int savedFlags = m_saveStateFlags;
	if (flags != -1) {
		m_saveStateFlags = flags;
	}
	mCoreThreadRunFunction(&m_threadContext, [](mCoreThread* context) {
		CoreController* controller = static_cast<CoreController*>(context->userData);
		VFile* vf = controller->m_stateVf;
		if (!vf) {
			return;
		}
		mCoreSaveStateNamed(context->core, vf, controller->m_saveStateFlags);
		vf->close(vf);
	});
	m_saveStateFlags = savedFlags;
}

void CoreController::loadBackupState() {
	if (!m_backupLoadState.isOpen()) {
		return;
	}

	mCoreThreadRunFunction(&m_threadContext, [](mCoreThread* context) {
		CoreController* controller = static_cast<CoreController*>(context->userData);
		controller->m_backupLoadState.seek(0);
		if (mCoreLoadStateNamed(context->core, controller->m_backupLoadState, controller->m_loadStateFlags)) {
			mLOG(STATUS, INFO, "Undid state load");
			controller->frameAvailable();
			controller->stateLoaded();
		}
		controller->m_backupLoadState.close();
	});
}

void CoreController::saveBackupState() {
	if (m_backupSaveState.isEmpty()) {
		return;
	}

	mCoreThreadRunFunction(&m_threadContext, [](mCoreThread* context) {
		CoreController* controller = static_cast<CoreController*>(context->userData);
		VFile* vf = mCoreGetState(context->core, controller->m_stateSlot, true);
		if (vf) {
			vf->write(vf, controller->m_backupSaveState.constData(), controller->m_backupSaveState.size());
			vf->close(vf);
			mLOG(STATUS, INFO, "Undid state save");
		}
		controller->m_backupSaveState.clear();
	});
}

void CoreController::loadSave(const QString& path, bool temporary) {
	m_resetActions.append([this, path, temporary]() {
		VFile* vf = VFileDevice::open(path, temporary ? O_RDONLY : O_RDWR);
		if (!vf) {
			LOG(QT, ERROR) << tr("Failed to open save file: %1").arg(path);
			return;
		}

		bool ok;
		if (temporary) {
			ok = m_threadContext.core->loadTemporarySave(m_threadContext.core, vf);
		} else {
			ok = m_threadContext.core->loadSave(m_threadContext.core, vf);
		}
		if (!ok) {
			vf->close(vf);
		}
	});
	if (hasStarted()) {
		reset();
	}
}

void CoreController::loadSave(VFile* vf, bool temporary) {
	m_resetActions.append([this, vf, temporary]() {
		bool ok;
		if (temporary) {
			ok = m_threadContext.core->loadTemporarySave(m_threadContext.core, vf);
		} else {
			ok = m_threadContext.core->loadSave(m_threadContext.core, vf);
		}
		if (!ok) {
			vf->close(vf);
		}
	});
	if (hasStarted()) {
		reset();
	}
}

void CoreController::importSave(const QString& path) {
	m_resetActions.append([this, path]() {
		VFile* source = VFileDevice::open(path, O_RDONLY);
		if (!source) {
			LOG(QT, ERROR) << tr("Failed to open save file for import: %1").arg(path);
			return;
		}

		// Permanent import means "replace the canonical .sav/.saN backing file",
		// not "treat this as an alternate file for the current session only".
		VFile* target = openCurrentSave(O_CREAT | O_RDWR);
		if (!target) {
			source->close(source);
			LOG(QT, ERROR) << tr("Failed to open the active save slot for import.");
			return;
		}

		bool copied = copyVFileContents(source, target);
		source->close(source);
		if (!copied) {
			target->close(target);
			LOG(QT, ERROR) << tr("Failed to copy imported save into the active save slot.");
			return;
		}

		bool ok = m_threadContext.core->loadSave(m_threadContext.core, target);
		if (!ok) {
			target->close(target);
			LOG(QT, ERROR) << tr("Failed to load imported save into the core.");
			return;
		}
		emit statusPosted(tr("Imported save into %1").arg(currentSaveFileName()));
	});
	if (hasStarted()) {
		reset();
	}
}

void CoreController::exportSave(const QString& path) {
	QSaveFile outfile(path);
	if (!outfile.open(QIODevice::WriteOnly)) {
		LOG(QT, ERROR) << tr("Failed to open save file for export: %1").arg(path);
		return;
	}

	Interrupter interrupter(this);
	void* buffer = nullptr;
	// Clone from the live core instead of copying a file from disk. That keeps
	// the export aligned with the exact in-memory save state the user/script is
	// looking at, even if the backing file has not been flushed yet.
	ssize_t size = m_threadContext.core->savedataClone(m_threadContext.core, &buffer);
	if (size <= 0 || !buffer) {
		free(buffer);
		LOG(QT, ERROR) << tr("Failed to clone active save data for export.");
		return;
	}

	qint64 written = outfile.write(static_cast<const char*>(buffer), size);
	free(buffer);
	if (written != size) {
		outfile.cancelWriting();
		LOG(QT, ERROR) << tr("Failed to write exported save data to disk.");
		return;
	}
	if (!outfile.commit()) {
		LOG(QT, ERROR) << tr("Failed to commit exported save file: %1").arg(path);
		return;
	}
	emit statusPosted(tr("Exported save to %1").arg(path));
}

void CoreController::loadPatch(const QString& patchPath) {
	Interrupter interrupter(this);
	VFile* patch = VFileDevice::open(patchPath, O_RDONLY);
	if (patch) {
		m_threadContext.core->loadPatch(m_threadContext.core, patch);
		m_patched = true;
		patch->close(patch);
		updateROMInfo();
	}
	if (mCoreThreadHasStarted(&m_threadContext)) {
		interrupter.resume();
		reset();
	}
}

void CoreController::replaceGame(const QString& path) {
	QFileInfo info(path);
	if (!info.isReadable()) {
		LOG(QT, ERROR) << tr("Failed to open game file: %1").arg(path);
		return;
	}
	QString fname = info.canonicalFilePath();
	Interrupter interrupter(this);
	mDirectorySetDetachBase(&m_threadContext.core->dirs);
	if (m_preload) {
		mCorePreloadFile(m_threadContext.core, fname.toUtf8().constData());
	} else {
		mCoreLoadFile(m_threadContext.core, fname.toUtf8().constData());
	}
	updateROMInfo();
}

void CoreController::yankPak() {
	Interrupter interrupter(this);

	switch (platform()) {
#ifdef M_CORE_GBA
	case mPLATFORM_GBA:
		GBAYankROM(static_cast<GBA*>(m_threadContext.core->board));
		break;
#endif
#ifdef M_CORE_GB
	case mPLATFORM_GB:
		GBYankROM(static_cast<GB*>(m_threadContext.core->board));
		break;
#endif
	case mPLATFORM_NONE:
		LOG(QT, ERROR) << tr("Can't yank pack in unexpected platform!");
		break;
	}
}

void CoreController::addKey(int key) {
	if (key < 0 || key >= 32) {
		return;
	}

	QMutexLocker locker(&m_actionMutex);
	m_timedKeyFrames[key] = 0;
	m_activeKeys |= 1 << key;
	m_removedKeys &= ~(1 << key);
}

void CoreController::clearKey(int key) {
	if (key < 0 || key >= 32) {
		return;
	}

	QMutexLocker locker(&m_actionMutex);
	m_timedKeyFrames[key] = 0;
	m_activeKeys &= ~(1 << key);
	m_removedKeys |= 1 << key;
}

void CoreController::pressKeyForFrames(int key, int frames) {
	if (key < 0 || key >= 32 || frames < 1) {
		return;
	}

	QMutexLocker locker(&m_actionMutex);
	m_timedKeyFrames[key] = frames;
	m_activeKeys |= 1 << key;
	m_removedKeys &= ~(1 << key);
	// pressKeyForFrames uses the same pre-step path as manual frame advance so
	// a paused core sees the button on the first frame this method wakes up.
	installPendingKeysForBoundedStep();
	if (m_moreFrames < frames) {
		m_moreFrames = frames;
	}
	if (isPaused()) {
		mCoreThreadUnpause(&m_threadContext);
	}
}

void CoreController::setAutofire(int key, bool enable) {
	if (key >= 32 || key < 0) {
		return;
	}

	QMutexLocker locker(&m_actionMutex);
	if (enable) {
		m_timedKeyFrames[key] = 0;
		m_activeKeys &= ~(1 << key);
	}
	m_removedKeys |= 1 << key;
	m_autofire[key] = enable;
	m_autofireStatus[key] = 0;
}

void CoreController::setInputTapeOverride(bool enable, uint32_t keys) {
	QMutexLocker locker(&m_actionMutex);
	m_inputTapeOverride = enable;
	m_inputTapeKeys = enable ? keys : 0;
	if (enable) {
		// The tape owns the entire GBA-facing key mask while replay is active.
		// Clearing timed presses/autofire here prevents stale frontend input from
		// being merged back in on the next stepped frame.
		m_activeKeys = static_cast<int>(keys);
		m_removedKeys = 0;
		m_currentKeys = keys;
		for (int key = 0; key < 32; ++key) {
			m_timedKeyFrames[key] = 0;
			m_autofire[key] = false;
			m_autofireStatus[key] = 0;
		}
	} else {
		m_activeKeys = 0;
		m_removedKeys = -1;
		m_currentKeys = 0;
	}
	if (m_threadContext.core) {
		m_threadContext.core->setKeys(m_threadContext.core, static_cast<int>(m_inputTapeKeys));
	}
}

uint32_t CoreController::pendingKeys() {
	QMutexLocker locker(&m_actionMutex);
	if (m_inputTapeOverride) {
		return m_inputTapeKeys;
	}
	// This is the next-frame view used by the Virtual Pad status UI and input
	// tape capture. Keyboard events have already been translated to GBA button
	// bits by Window::keyPressEvent()/keyReleaseEvent(), so this exposes only
	// controller buttons, not raw Qt key codes.
	return static_cast<uint32_t>(m_activeKeys | (m_currentKeys & ~m_removedKeys));
}

#ifdef USE_PNG
void CoreController::screenshot() {
	mCoreThreadRunFunction(&m_threadContext, [](mCoreThread* context) {
		mCoreTakeScreenshot(context->core);
	});
}
#endif

void CoreController::setRealTime() {
	m_threadContext.core->rtc.override = RTC_NO_OVERRIDE;
}

void CoreController::setFixedTime(const QDateTime& time) {
	m_threadContext.core->rtc.override = RTC_FIXED;
	m_threadContext.core->rtc.value = time.toMSecsSinceEpoch();
}

void CoreController::setFakeEpoch(const QDateTime& time) {
	m_threadContext.core->rtc.override = RTC_FAKE_EPOCH;
	m_threadContext.core->rtc.value = time.toMSecsSinceEpoch();
}

void CoreController::setTimeOffset(qint64 offset) {
	m_threadContext.core->rtc.override = RTC_WALLCLOCK_OFFSET;
	m_threadContext.core->rtc.value = offset * 1000LL;
}

void CoreController::scanCard(const QString& path) {
#ifdef M_CORE_GBA
	QImage image(path);
	if (image.isNull()) {
		QFile file(path);
		if (!file.open(QIODevice::ReadOnly)) {
			return;
		}
		QByteArray eReaderData = file.read(2912);
		if (eReaderData.isEmpty()) {
			return;
		}

		file.seek(0);
		QStringList lines;
		QDir basedir(QFileInfo(path).dir());

		while (true) {
			QByteArray line = file.readLine().trimmed();
			if (line.isEmpty()) {
				break;
			}
			QString filepath(QString::fromUtf8(line));
			if (filepath.isEmpty() || filepath[0] == QChar('#')) {
				continue;
			}
			if (QFileInfo(filepath).isRelative()) {
				lines.append(basedir.filePath(filepath));
			} else {
				lines.append(filepath);
			}
		}
		scanCards(lines);
		m_eReaderData = eReaderData;
	} else if (image.size() == QSize(989, 44) || image.size() == QSize(639, 44)) {
		const uchar* bits = image.constBits();
		size_t size;
#if (QT_VERSION >= QT_VERSION_CHECK(5, 10, 0))
		size = image.sizeInBytes();
#else
		size = image.byteCount();
#endif
		m_eReaderData.setRawData(reinterpret_cast<const char*>(bits), size);
	}

	mCoreThreadRunFunction(&m_threadContext, [](mCoreThread* thread) {
		CoreController* controller = static_cast<CoreController*>(thread->userData);
		GBACartEReaderQueueCard(static_cast<GBA*>(thread->core->board), controller->m_eReaderData.constData(), controller->m_eReaderData.size());
	});
#endif
}

void CoreController::scanCards(const QStringList& paths) {
	for (const QString& path : paths) {
		scanCard(path);
	}
}

void CoreController::importSharkport(const QString& path) {
#ifdef M_CORE_GBA
	if (platform() != mPLATFORM_GBA) {
		return;
	}
	VFile* vf = VFileDevice::open(path, O_RDONLY);
	if (!vf) {
		LOG(QT, ERROR) << tr("Failed to open snapshot file for reading: %1").arg(path);
		return;
	}
	Interrupter interrupter(this);
	GBASavedataImportSharkPort(static_cast<GBA*>(m_threadContext.core->board), vf, false);
	GBASavedataImportGSV(static_cast<GBA*>(m_threadContext.core->board), vf, false);
	vf->close(vf);
#endif
}

void CoreController::exportSharkport(const QString& path) {
#ifdef M_CORE_GBA
	if (platform() != mPLATFORM_GBA) {
		return;
	}
	VFile* vf = VFileDevice::open(path, O_WRONLY | O_CREAT | O_TRUNC);
	if (!vf) {
		LOG(QT, ERROR) << tr("Failed to open snapshot file for writing: %1").arg(path);
		return;
	}
	Interrupter interrupter(this);
	GBASavedataExportSharkPort(static_cast<GBA*>(m_threadContext.core->board), vf);
	vf->close(vf);
#endif
}

#ifdef M_CORE_GB
void CoreController::attachPrinter() {
	if (platform() != mPLATFORM_GB) {
		return;
	}
	GB* gb = static_cast<GB*>(m_threadContext.core->board);
	clearMultiplayerController();
	GBPrinterCreate(&m_printer.d);
	m_printer.parent = this;
	m_printer.d.print = [](GBPrinter* printer, int height, const uint8_t* data) {
		QGBPrinter* qPrinter = reinterpret_cast<QGBPrinter*>(printer);
		QImage image(GB_VIDEO_HORIZONTAL_PIXELS, height, QImage::Format_Indexed8);
		QVector<QRgb> colors;
		colors.append(qRgb(0xF8, 0xF8, 0xF8));
		colors.append(qRgb(0xA8, 0xA8, 0xA8));
		colors.append(qRgb(0x50, 0x50, 0x50));
		colors.append(qRgb(0x00, 0x00, 0x00));
		image.setColorTable(colors);
		for (int y = 0; y < height; ++y) {
			for (int x = 0; x < GB_VIDEO_HORIZONTAL_PIXELS; x += 4) {
				uint8_t byte = data[(x + y * GB_VIDEO_HORIZONTAL_PIXELS) / 4];
				image.setPixel(x + 0, y, (byte & 0xC0) >> 6);
				image.setPixel(x + 1, y, (byte & 0x30) >> 4);
				image.setPixel(x + 2, y, (byte & 0x0C) >> 2);
				image.setPixel(x + 3, y, (byte & 0x03) >> 0);
			}
		}
		QMetaObject::invokeMethod(qPrinter->parent, "imagePrinted", Q_ARG(const QImage&, image));
	};
	Interrupter interrupter(this);
	GBSIOSetDriver(&gb->sio, &m_printer.d.d);
}

void CoreController::detachPrinter() {
	if (platform() != mPLATFORM_GB) {
		return;
	}
	Interrupter interrupter(this);
	GB* gb = static_cast<GB*>(m_threadContext.core->board);
	GBPrinterDonePrinting(&m_printer.d);
	GBSIOSetDriver(&gb->sio, nullptr);
}

void CoreController::endPrint() {
	if (platform() != mPLATFORM_GB) {
		return;
	}
	Interrupter interrupter(this);
	GBPrinterDonePrinting(&m_printer.d);
}
#endif

#ifdef M_CORE_GBA
void CoreController::attachBattleChipGate() {
	if (platform() != mPLATFORM_GBA) {
		return;
	}
	Interrupter interrupter(this);
	clearMultiplayerController();
	GBASIOBattlechipGateCreate(&m_battlechip);
	m_threadContext.core->setPeripheral(m_threadContext.core, mPERIPH_GBA_BATTLECHIP_GATE, &m_battlechip);
}

void CoreController::detachBattleChipGate() {
	if (platform() != mPLATFORM_GBA) {
		return;
	}
	Interrupter interrupter(this);
	m_threadContext.core->setPeripheral(m_threadContext.core, mPERIPH_GBA_BATTLECHIP_GATE, nullptr);
}

void CoreController::setBattleChipId(uint16_t id) {
	if (platform() != mPLATFORM_GBA) {
		return;
	}
	Interrupter interrupter(this);
	m_battlechip.chipId = id;
}

void CoreController::setBattleChipFlavor(int flavor) {
	if (platform() != mPLATFORM_GBA) {
		return;
	}
	Interrupter interrupter(this);
	m_battlechip.flavor = flavor;
}
#endif

void CoreController::setAVStream(mAVStream* stream) {
	Interrupter interrupter(this);
	m_threadContext.core->setAVStream(m_threadContext.core, stream);
}

void CoreController::clearAVStream() {
	Interrupter interrupter(this);
	m_threadContext.core->setAVStream(m_threadContext.core, nullptr);
}

void CoreController::clearOverride() {
	m_override.reset();
}

void CoreController::startVideoLog(const QString& path, bool compression) {
	if (m_vl) {
		return;
	}

	VFile* vf = VFileDevice::open(path, O_WRONLY | O_CREAT | O_TRUNC);
	if (!vf) {
		return;
	}
	startVideoLog(vf, compression);
}

void CoreController::startVideoLog(VFile* vf, bool compression) {
	if (m_vl || !vf) {
		return;
	}

	Interrupter interrupter(this);
	m_vl = mVideoLogContextCreate(m_threadContext.core);
	m_vlVf = vf;
	mVideoLogContextSetOutput(m_vl, m_vlVf);
	mVideoLogContextSetCompression(m_vl, compression);
	mVideoLogContextWriteHeader(m_vl, m_threadContext.core);
}

void CoreController::endVideoLog(bool closeVf) {
	if (!m_vl) {
		return;
	}

	Interrupter interrupter(this);
	mVideoLogContextDestroy(m_threadContext.core, m_vl, closeVf);
	if (closeVf) {
		m_vlVf = nullptr;
	}
	m_vl = nullptr;
}

void CoreController::setFramebufferHandle(int fb) {
	Interrupter interrupter(this);
	if (fb < 0) {
		if (!m_hwaccel) {
			return;
		}
		mCoreConfigSetIntValue(&m_threadContext.core->config, "hwaccelVideo", 0);
		m_threadContext.core->setVideoGLTex(m_threadContext.core, -1);
		m_hwaccel = false;
	} else {
		mCoreConfigSetIntValue(&m_threadContext.core->config, "hwaccelVideo", 1);
		m_threadContext.core->setVideoGLTex(m_threadContext.core, fb);
		if (m_hwaccel) {
			return;
		}
		m_hwaccel = true;
	}
	if (hasStarted()) {
		m_threadContext.core->reloadConfigOption(m_threadContext.core, "hwaccelVideo", NULL);
		if (!m_hwaccel) {
			m_threadContext.core->setVideoBuffer(m_threadContext.core, reinterpret_cast<color_t*>(m_activeBuffer.data()), screenDimensions().width());
		}
	}
}

void CoreController::updateKeys() {
	if (m_inputTapeOverride) {
		// Native input-tape replay is intentionally exact: one explicit GBA
		// mask per frame. Do not merge live keyboard/controller polling or
		// autofire while the override owns input for deterministic playback.
		m_currentKeys = m_inputTapeKeys;
		m_threadContext.core->setKeys(m_threadContext.core, static_cast<int>(m_inputTapeKeys));
		return;
	}
	// The normal GUI path always installs an input controller, but keep the
	// worker/runtime edge cases safe by treating "no controller" as "no new
	// host input" instead of dereferencing a null pointer.
	int polledKeys = (m_inputController ? m_inputController->pollEvents() : 0) | updateAutofire();
	int activeKeys = m_activeKeys | polledKeys;
	activeKeys |= m_threadContext.core->getKeys(m_threadContext.core) & ~m_removedKeys;
	m_removedKeys = polledKeys;
	// Cache the merged key mask after every frame so the OSD can display the
	// same button state the core will see on the next step without reaching back
	// into the core from the UI thread.
	m_currentKeys = static_cast<uint32_t>(activeKeys);
	m_threadContext.core->setKeys(m_threadContext.core, activeKeys);
}

void CoreController::installPendingKeysForBoundedStep() {
	if (!m_threadContext.core) {
		return;
	}

	// This method is intentionally called while m_actionMutex is already held.
	// It only pushes the already queued Qt/Virtual Pad/tape state into the core
	// before unpausing for a bounded step; it does not itself decide how many
	// frames should run.
	updateKeys();
}

int CoreController::updateAutofire() {
	int active = 0;
	for (int k = 0; k < 32; ++k) {
		if (!m_autofire[k]) {
			continue;
		}
		++m_autofireStatus[k];
		if (m_autofireStatus[k] >= 2 * m_autofireThreshold) {
			m_autofireStatus[k] = 0;
		} else if (m_autofireStatus[k] >= m_autofireThreshold) {
			active |= 1 << k;
		}
	}
	return active;
}

void CoreController::finishFrame() {
	if (!m_hwaccel) {
		unsigned width, height;
		m_threadContext.core->desiredVideoDimensions(m_threadContext.core, &width, &height);

		QMutexLocker locker(&m_bufferMutex);
		memcpy(m_completeBuffer.data(), m_activeBuffer.constData(), width * height * BYTES_PER_PIXEL);
	}

	bool keysUpdatedBeforePause = false;
	{
		QMutexLocker locker(&m_actionMutex);
		// updateKeys() refreshes m_currentKeys for the next frame. Capture the
		// old value first so the native Input Tapes recorder stores the frame
		// that just completed, without reaching back through core callbacks
		// during script-driven frame loops.
		//
		// This is one of the main determinism-sensitive differences between the
		// custom GUI tooling and stock "poll whatever is live right now" input:
		// the recorded frame mask is taken from the completed frame boundary.
		m_lastFrameKeys = m_currentKeys;
		QList<std::function<void ()>> frameActions(m_frameActions);
		m_frameActions.clear();
		for (auto& action : frameActions) {
			action();
		}
		// Timed Virtual Pad presses are frame-accurate but fixed-size: each key
		// has one countdown slot, avoiding per-frame callback churn.
		for (int key = 0; key < 32; ++key) {
			if (m_timedKeyFrames[key] <= 0) {
				continue;
			}
			--m_timedKeyFrames[key];
			if (!m_timedKeyFrames[key]) {
				m_activeKeys &= ~(1 << key);
				m_removedKeys |= 1 << key;
			}
		}
		if (m_moreFrames > 0) {
			--m_moreFrames;
			if (!m_moreFrames) {
				// Make "press for N frames" release visible to the core before
				// the thread stops on the final frame.
				updateKeys();
				keysUpdatedBeforePause = true;
				mCoreThreadPauseFromThread(&m_threadContext);
			}
		}
		++m_frameCounter;
	}
	if (!keysUpdatedBeforePause) {
		updateKeys();
	}

	QMetaObject::invokeMethod(this, "frameAvailable");
}

int CoreController::resolvedSavePlayerId() const {
	int savePlayerId = 0;
	mCoreConfigGetIntValue(&m_threadContext.core->config, "savePlayerId", &savePlayerId);
	if (m_multiplayer && (savePlayerId == 0 || m_multiplayer->attached() > 1)) {
		CoreController* self = const_cast<CoreController*>(this);
		if (savePlayerId == m_multiplayer->playerId(self) + 1) {
			// Player 1 is using our save, so let's use theirs, at least for now.
			savePlayerId = 1;
		} else {
			savePlayerId = m_multiplayer->playerId(self) + 1;
		}
	}
	return savePlayerId;
}

QString CoreController::currentSaveSuffix() const {
	int savePlayerId = resolvedSavePlayerId();
	if (savePlayerId < 2) {
		return QLatin1String(".sav");
	}
	return QString(".sa%1").arg(savePlayerId);
}

QString CoreController::currentSaveFileName() const {
	const QString baseName = QString::fromUtf8(m_threadContext.core->dirs.baseName);
	const QString stem = baseName.isEmpty() ? QStringLiteral("save") : baseName;
	return stem + currentSaveSuffix();
}

VFile* CoreController::openCurrentSave(int mode) const {
	if (!m_threadContext.core->dirs.save) {
		return nullptr;
	}
	const QByteArray suffix = currentSaveSuffix().toUtf8();
	return mDirectorySetOpenSuffix(&m_threadContext.core->dirs, m_threadContext.core->dirs.save, suffix.constData(), mode);
}

void CoreController::updatePlayerSave() {
	VFile* save = openCurrentSave(O_CREAT | O_RDWR);
	if (save) {
		m_threadContext.core->loadSave(m_threadContext.core, save);
	}
}

quint64 CoreController::savestateCacheUsedBytes() const {
	return m_savestateCache ? m_savestateCache->stats().usedBytes : 0;
}

int CoreController::savestateCacheEntryCount() const {
	return m_savestateCache ? m_savestateCache->stats().entryCount : 0;
}

bool CoreController::videoLayerEnabled(size_t id) const {
	switch (platform()) {
#ifdef M_CORE_GBA
	case mPLATFORM_GBA: {
		const GBA* gba = static_cast<const GBA*>(m_threadContext.core->board);
		if (!gba || !gba->video.renderer) {
			return true;
		}
		switch (id) {
		case GBA_LAYER_BG0:
		case GBA_LAYER_BG1:
		case GBA_LAYER_BG2:
		case GBA_LAYER_BG3:
			return !gba->video.renderer->disableBG[id];
		case GBA_LAYER_OBJ:
			return !gba->video.renderer->disableOBJ;
		case GBA_LAYER_WIN0:
			return !gba->video.renderer->disableWIN[0];
		case GBA_LAYER_WIN1:
			return !gba->video.renderer->disableWIN[1];
		case GBA_LAYER_OBJWIN:
			return !gba->video.renderer->disableOBJWIN;
		default:
			return true;
		}
	}
#endif
#ifdef M_CORE_GB
	case mPLATFORM_GB: {
		const GB* gb = static_cast<const GB*>(m_threadContext.core->board);
		if (!gb || !gb->video.renderer) {
			return true;
		}
		switch (id) {
		case GB_LAYER_BACKGROUND:
			return !gb->video.renderer->disableBG;
		case GB_LAYER_WINDOW:
			return !gb->video.renderer->disableWIN;
		case GB_LAYER_OBJ:
			return !gb->video.renderer->disableOBJ;
		default:
			return true;
		}
	}
#endif
	default:
		return true;
	}
}

void CoreController::applyNoRenderMode() {
	const mCoreChannelInfo* videoLayers = nullptr;
	size_t count = m_threadContext.core->listVideoLayers(m_threadContext.core, &videoLayers);
	if (!count || !videoLayers) {
		return;
	}

	if (m_noRenderMode) {
		if (m_savedVideoLayers.isEmpty()) {
			m_savedVideoLayers.resize(static_cast<int>(count));
			for (size_t i = 0; i < count; ++i) {
				m_savedVideoLayers[static_cast<int>(i)] = videoLayerEnabled(videoLayers[i].id);
			}
		}
		for (size_t i = 0; i < count; ++i) {
			m_threadContext.core->enableVideoLayer(m_threadContext.core, videoLayers[i].id, false);
		}
		return;
	}
	if (m_savedVideoLayers.isEmpty()) {
		return;
	}

	for (size_t i = 0; i < count; ++i) {
		const bool enabled = i < static_cast<size_t>(m_savedVideoLayers.size())
			? m_savedVideoLayers[static_cast<int>(i)]
			: true;
		m_threadContext.core->enableVideoLayer(m_threadContext.core, videoLayers[i].id, enabled);
	}
	m_savedVideoLayers.clear();
}

QString CoreController::savestateCacheKeyForSlot(int slot) const {
	return QStringLiteral("slot:%1").arg(slot);
}

QString CoreController::savestateCacheKeyForPath(const QString& path) const {
	QFileInfo info(path);
	const QString resolved = info.canonicalFilePath();
	return resolved.isEmpty() ? info.absoluteFilePath() : resolved;
}

void CoreController::updateFastForward() {
	auto setAudioWait = [this](bool wait) {
		MutexLock(&m_threadContext.impl->sync.audioBufferMutex);
		m_threadContext.impl->sync.audioWait = wait;
		if (!wait) {
			ConditionWake(&m_threadContext.impl->sync.audioRequiredCond);
		}
		MutexUnlock(&m_threadContext.impl->sync.audioBufferMutex);
	};

	// If we have "Fast forward" checked in the menu (m_fastForwardForced)
	// or are holding the fast forward button (m_fastForward):
	if (m_fastForward || m_fastForwardForced) {
		if (m_fastForwardVolume >= 0) {
			m_threadContext.core->opts.volume = m_fastForwardVolume;
		}
		if (m_fastForwardMute >= 0) {
			m_threadContext.core->opts.mute = m_fastForwardMute || m_mute || m_audioKillswitch;
		}
		setSync(false);

		// If we aren't holding the fast forward button
		// then use the non "(held)" ratio
		if(!m_fastForward) {
			if (m_fastForwardRatio > 0) {
				m_threadContext.impl->sync.fpsTarget = m_fpsTarget * m_fastForwardRatio;
				setAudioWait(!m_scriptTimingOverride && !m_audioKillswitch && !m_audioSyncBlocked);
			}
		} else {
			// If we are holding the fast forward button,
			// then use the held ratio
			if (m_fastForwardHeldRatio > 0) {
				m_threadContext.impl->sync.fpsTarget = m_fpsTarget * m_fastForwardHeldRatio;
				setAudioWait(!m_scriptTimingOverride && !m_audioKillswitch && !m_audioSyncBlocked);
			}
		}
	} else {
		if (!mCoreConfigGetIntValue(&m_threadContext.core->config, "volume", &m_threadContext.core->opts.volume)) {
			m_threadContext.core->opts.volume = 0x100;
		}
		mCoreConfigGetBoolValue(&m_threadContext.core->config, "mute", &m_threadContext.core->opts.mute);
		if (m_audioKillswitch) {
			m_threadContext.core->opts.mute = true;
		}
		m_threadContext.impl->sync.fpsTarget = m_fpsTarget;
		if (m_scriptTimingOverride) {
			setSync(false);
			setAudioWait(false);
		} else {
			setSync(true);
		}
	}

	if (m_scriptTimingOverride) {
		setSync(false);
		setAudioWait(false);
	}

	m_threadContext.core->reloadConfigOption(m_threadContext.core, NULL, NULL);
}

void CoreController::updateROMInfo() {
	const NoIntroDB* db = GBAApp::app()->gameDB();
	NoIntroGame game{};
	m_crc32 = 0;
	mCore* core = m_threadContext.core;
	core->checksum(core, &m_crc32, mCHECKSUM_CRC32);

	char gameTitle[17] = { '\0' };
	core->getGameTitle(core, gameTitle);
	m_internalTitle = QLatin1String(gameTitle);

#ifdef USE_SQLITE3
	if (db && m_crc32 && NoIntroDBLookupGameByCRC(db, m_crc32, &game)) {
		m_dbTitle = QString::fromUtf8(game.name);
	}
#endif
}

CoreController::Interrupter::Interrupter()
	: m_parent(nullptr)
	, m_held(false)
{
}

CoreController::Interrupter::Interrupter(CoreController* parent)
	: m_parent(parent)
	, m_held(false)
{
	interrupt();
}

CoreController::Interrupter::Interrupter(std::shared_ptr<CoreController> parent)
	: m_parent(parent.get())
	, m_held(false)
{
	interrupt();
}

CoreController::Interrupter::Interrupter(const Interrupter& other)
	: m_parent(other.m_parent)
	, m_held(false)
{
	interrupt();
}

CoreController::Interrupter::~Interrupter() {
	resume();
}

CoreController::Interrupter& CoreController::Interrupter::operator=(const Interrupter& other)
{
	interrupt(other.m_parent);
	return *this;
}

void CoreController::Interrupter::interrupt(CoreController* controller) {
	if (m_parent != controller) {
		CoreController* old = m_parent;
		bool oldHeld = m_held;
		m_parent = controller;
		m_held = false;
		interrupt();
		if (oldHeld) {
			resume(old);
		}
	} else if (m_parent && !m_held) {
		// A Python runtime session can be created while the Qt window owns a
		// controller whose core thread has not finished starting yet. The first
		// interrupt attempt records the controller pointer, but cannot hold the
		// core until thread()->impl exists. Retry when the same controller becomes
		// interruptible so ROM/save/state setup cannot free-run before runFrame().
		interrupt();
	}
}

void CoreController::Interrupter::interrupt(std::shared_ptr<CoreController> controller) {
	interrupt(controller.get());
}

void CoreController::Interrupter::interrupt() {
	if (!m_parent || !m_parent->thread()->impl) {
		return;
	}

	if (mCoreThreadGet() != m_parent->thread()) {
		mCoreThreadInterrupt(m_parent->thread());
	} else {
		mCoreThreadInterruptFromThread(m_parent->thread());
	}
	m_held = true;
}

void CoreController::Interrupter::resume() {
	if (m_held) {
		resume(m_parent);
	}
	m_parent = nullptr;
	m_held = false;
}

void CoreController::Interrupter::resume(CoreController* controller) {
	if (!controller || !controller->thread()->impl) {
		return;
	}

	mCoreThreadContinue(controller->thread());
}

bool CoreController::Interrupter::held() const {
	return m_held && m_parent && m_parent->thread()->impl;
}
