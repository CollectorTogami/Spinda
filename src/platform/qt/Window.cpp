/* Copyright (c) 2013-2017 Jeffrey Pfau
 *
 * Original mGBA source is credited to Jeffrey Pfau and contributors.
 * Local custom modifications in this fork were added for this workspace and
 * are not upstream mGBA work or authored by Jeffrey Pfau.
 *
 * This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */
#include "Window.h"

#include <QApplication>
#include <QByteArray>
#include <QCheckBox>
#include <QDialog>
#include <QDialogButtonBox>
#include <QDir>
#include <QEvent>
#include <QFile>
#include <QFormLayout>
#include <QKeyEvent>
#include <QKeySequence>
#include <QJsonDocument>
#include <QJsonObject>
#include <QLabel>
#include <QLayout>
#include <QMenuBar>
#include <QMessageBox>
#include <QMimeData>
#include <QPainter>
#include <QPushButton>
#include <QScreen>
#include <QSpinBox>
#include <QVBoxLayout>
#include <QVector>
#include <QWindow>

#ifdef USE_SQLITE3
#include "ArchiveInspector.h"
#include "library/LibraryController.h"
#endif

#include "AboutScreen.h"
#include "AudioProcessor.h"
#include "BattleChipView.h"
#include "CheatsView.h"
#include "ConfigController.h"
#include "CoreController.h"
#include "CustomFeatures.h"
#include "DebuggerConsole.h"
#include "DebuggerConsoleController.h"
#include "Display.h"
#include "DolphinConnector.h"
#include "FrameView.h"
#include "GBAApp.h"
#include "GDBController.h"
#include "InputTapeView.h"
#include "SpindaProjectView.h"
#include "WindowsDarkChrome.h"

namespace {

struct AlwaysOnTopRestore {
	QPointer<QWidget> widget;
	bool wasVisible = false;
};

QVector<AlwaysOnTopRestore> suspendAlwaysOnTopForModalDialog(const QVector<QWidget*>& widgets) {
	QVector<AlwaysOnTopRestore> restoreOnTop;
	for (QWidget* widget : widgets) {
		if (!widget || !(widget->windowFlags() & Qt::WindowStaysOnTopHint)) {
			continue;
		}

		AlwaysOnTopRestore restore;
		restore.widget = widget;
		restore.wasVisible = widget->isVisible();
		restoreOnTop.append(restore);

		// Tool windows with WindowStaysOnTopHint outrank a normal modal dialog
		// on Windows. Drop the raw window flag for the duration of the dialog
		// without changing the user's persisted always-on-top preference.
		widget->setWindowFlags(widget->windowFlags() & ~Qt::WindowStaysOnTopHint);
		if (restore.wasVisible) {
			widget->show();
		}
	}
	return restoreOnTop;
}

void restoreAlwaysOnTopAfterModalDialog(const QVector<AlwaysOnTopRestore>& restoreOnTop) {
	for (const AlwaysOnTopRestore& restore : restoreOnTop) {
		if (!restore.widget) {
			continue;
		}

		restore.widget->setWindowFlags(restore.widget->windowFlags() | Qt::WindowStaysOnTopHint);
		if (restore.wasVisible) {
			restore.widget->show();
			restore.widget->raise();
		}
	}
}

void repolishWidget(QWidget* widget) {
	if (!widget || !widget->style()) {
		return;
	}
	widget->style()->unpolish(widget);
	widget->style()->polish(widget);
	widget->update();
}

void syncMenuWidgetTheme(QMenu* menu) {
	if (!menu) {
		return;
	}
	menu->setAutoFillBackground(true);
	menu->setAttribute(Qt::WA_StyledBackground, true);
	menu->setPalette(QApplication::palette(menu));
	menu->setBackgroundRole(QPalette::Window);
	menu->setForegroundRole(QPalette::WindowText);
	repolishWidget(menu);
}

bool spindaNativePhase3EnvBool(const char* name, bool defaultValue = false) {
	const QByteArray key = QByteArrayLiteral("MGBA_SPINDA_NATIVE_PHASE3_") + QByteArray(name);
	const QByteArray raw = qgetenv(key.constData()).trimmed().toLower();
	if (raw.isEmpty()) {
		return defaultValue;
	}
	if (raw == QByteArrayLiteral("1") || raw == QByteArrayLiteral("true") || raw == QByteArrayLiteral("yes") || raw == QByteArrayLiteral("on")) {
		return true;
	}
	if (raw == QByteArrayLiteral("0") || raw == QByteArrayLiteral("false") || raw == QByteArrayLiteral("no") || raw == QByteArrayLiteral("off")) {
		return false;
	}
	return defaultValue;
}

void syncMenuBarTheme(QMenuBar* menuBar) {
	if (!menuBar) {
		return;
	}
	// Re-apply the palette after menu rebuilds so the top-level menu strip keeps
	// the same dark chrome as the rest of the Qt window.
	menuBar->setNativeMenuBar(false);
	menuBar->setAutoFillBackground(true);
	menuBar->setAttribute(Qt::WA_StyledBackground, true);
	menuBar->setPalette(QApplication::palette(menuBar));
	menuBar->setBackgroundRole(QPalette::Window);
	menuBar->setForegroundRole(QPalette::WindowText);
	for (QAction* action : menuBar->actions()) {
		syncMenuWidgetTheme(action ? action->menu() : nullptr);
	}
	repolishWidget(menuBar);
}

void writeCustomFeaturesAutotestMarker(QGBA::Window* window) {
	// Deployment tests launch the real Qt frontend and read this one-shot JSON
	// snapshot back out to confirm the custom feature state the window actually
	// loaded, instead of only asserting on source strings.
	QByteArray markerPath = qgetenv("MGBA_CUSTOM_FEATURES_AUTOTEST_MARKER");
	if (markerPath.isEmpty() || !window) {
		return;
	}

	QFile marker(QString::fromUtf8(markerPath));
	if (!marker.open(QIODevice::WriteOnly | QIODevice::Truncate | QIODevice::Text)) {
		return;
	}

	QJsonObject root;
	if (window->controller()) {
		root.insert(QStringLiteral("audioKillswitch"), window->controller()->audioKillswitchEnabled());
		root.insert(QStringLiteral("noRenderMode"), window->controller()->noRenderModeEnabled());
		root.insert(QStringLiteral("savestateCacheEnabled"), window->controller()->savestateCacheEnabled());
		root.insert(QStringLiteral("savestateCacheMaxEntries"), window->controller()->savestateCacheMaxEntries());
		root.insert(QStringLiteral("savestateCacheMaxBytes"), static_cast<qint64>(window->controller()->savestateCacheMaxBytes()));
		root.insert(QStringLiteral("savestateCacheUsedBytes"), static_cast<qint64>(window->controller()->savestateCacheUsedBytes()));
	}
	if (window->centralWidget()) {
		root.insert(QStringLiteral("centralWidgetClass"), window->centralWidget()->metaObject()->className());
	}
	const QGBA::WorkerInstanceInfo workerInfo = QGBA::customWorkerInstanceInfo();
	root.insert(QStringLiteral("worker"), workerInfo.isWorker);
	root.insert(QStringLiteral("workerName"), workerInfo.workerName);
	root.insert(QStringLiteral("storageRoot"), workerInfo.storageRoot);
	root.insert(QStringLiteral("configDir"), workerInfo.configDir);
	root.insert(QStringLiteral("savegameDir"), workerInfo.savegameDir);
	root.insert(QStringLiteral("savestateDir"), workerInfo.savestateDir);
	root.insert(QStringLiteral("workerCoordinationRoot"), workerInfo.coordinationRoot);
	QJsonDocument doc(root);
	marker.write(doc.toJson(QJsonDocument::Indented));
}

void scheduleStartupFrameAutotestMarker(QGBA::Window* window) {
	// Regression hook for startup-freeze bugs. It intentionally waits on the Qt
	// event loop instead of running a script so the measured path is the normal
	// visible frontend startup path a user gets from opening a ROM.
	const QByteArray markerPath = qgetenv("MGBA_QT_STARTUP_FRAME_MARKER");
	if (markerPath.isEmpty() || !window || !window->controller()) {
		return;
	}

	const QString markerFile = QString::fromUtf8(markerPath);
	const quint64 initialFrame = static_cast<quint64>(window->controller()->frameCounter());
	const bool initialPaused = window->controller()->isPaused();
	QTimer::singleShot(1500, window, [window, markerFile, initialFrame, initialPaused]() {
		if (!window || !window->controller()) {
			return;
		}
		const quint64 currentFrame = static_cast<quint64>(window->controller()->frameCounter());
		QJsonObject root;
		root.insert(QStringLiteral("initialFrame"), QString::number(initialFrame));
		root.insert(QStringLiteral("currentFrame"), QString::number(currentFrame));
		root.insert(QStringLiteral("framesAdvanced"), static_cast<qint64>(currentFrame - initialFrame));
		root.insert(QStringLiteral("initialPaused"), initialPaused);
		root.insert(QStringLiteral("currentPaused"), window->controller()->isPaused());
		root.insert(QStringLiteral("audioSync"), window->controller()->audioSync());
		root.insert(QStringLiteral("audioSyncBlocked"), window->controller()->audioSyncBlocked());
		root.insert(QStringLiteral("videoSync"), window->controller()->videoSync());
		root.insert(QStringLiteral("audioKillswitch"), window->controller()->audioKillswitchEnabled());
		root.insert(QStringLiteral("noRenderMode"), window->controller()->noRenderModeEnabled());

		QFile marker(markerFile);
		if (marker.open(QIODevice::WriteOnly | QIODevice::Truncate | QIODevice::Text)) {
			marker.write(QJsonDocument(root).toJson(QJsonDocument::Indented));
		}
	});
}

}
#include "GDBWindow.h"
#include "GIFView.h"
#include "IOViewer.h"
#include "LoadSaveState.h"
#include "LogView.h"
#include "MapView.h"
#include "MemorySearch.h"
#include "MemoryView.h"
#include "MultiplayerController.h"
#include "OverrideView.h"
#include "ObjView.h"
#include "PaletteView.h"
#include "PlacementControl.h"
#include "PrinterView.h"
#include "ReportView.h"
#include "ROMInfo.h"
#include "SaveConverter.h"
#ifdef ENABLE_SCRIPTING
#include "scripting/ScriptingView.h"
#endif
#include "SensorView.h"
#include "ShaderSelector.h"
#include "ShortcutController.h"
#include "TileView.h"
#include "VirtualPad.h"
#include "VideoProxy.h"
#include "VideoView.h"
#ifdef ENABLE_PYTHON
#include "platform/python/lib.h"
#endif

#ifdef USE_DISCORD_RPC
#include "DiscordCoordinator.h"
#endif

#include <mgba/core/version.h>
#include <mgba/core/cheats.h>
#include <mgba/core/config.h>
#ifdef M_CORE_GB
#include <mgba/internal/gb/gb.h>
#include <mgba/internal/gb/video.h>
#endif
#ifdef M_CORE_GBA
#include <mgba/gba/interface.h>
#include <mgba/internal/gba/gba.h>
#endif
#include <mgba/feature/commandline.h>
#include <mgba-util/vfs.h>

#include <mgba-util/convolve.h>

using namespace QGBA;

namespace {

bool fastForwardStartsEnabled(ConfigController* config) {
	// Local workspace addition: visible RNG runs should not need an extra
	// Shift+Tab before the title-screen timing work starts.
	return config->getOption("fastForward", 1).toInt() != 0;
}

}

#ifdef ENABLE_SCRIPTING
namespace {

void traceStartupScript(const QString& message) {
	// Lightweight trace hook used by the local Qt/Python integration tests. The
	// file is only written when MGBA_PYTHON_TRACE is set.
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

}
#endif

Window::Window(CoreManager* manager, ConfigController* config, int playerId, QWidget* parent)
	: QMainWindow(parent)
	, m_manager(manager)
	, m_logView(new LogView(&m_log, this))
	, m_screenWidget(new WindowBackground())
	, m_config(config)
	, m_inputController(playerId, this)
	, m_shortcutController(new ShortcutController(this))
	, m_playerId(playerId)
{
	setFocusPolicy(Qt::StrongFocus);
	setAcceptDrops(true);
	setAttribute(Qt::WA_DeleteOnClose);
	updateTitle();

	m_logo.setDevicePixelRatio(m_screenWidget->devicePixelRatio());
	m_logo = m_logo; // Free memory left over in old pixmap

#if defined(M_CORE_GBA)
	float i = 2;
#elif defined(M_CORE_GB)
	float i = 3;
#endif
	QVariant multiplier = m_config->getOption("scaleMultiplier");
	if (!multiplier.isNull()) {
		m_savedScale = multiplier.toInt();
		i = m_savedScale;
	}
#ifdef USE_SQLITE3
	m_libraryView = new LibraryController(nullptr, ConfigController::configDir() + "/library.sqlite3", m_config);
	ConfigOption* showLibrary = m_config->addOption("showLibrary");
	showLibrary->connect([this](const QVariant& value) {
		if (!m_controller) {
			if (value.toBool()) {
				attachWidget(m_libraryView);
			} else {
				attachWidget(m_screenWidget);
			}
		}
	}, this);
	m_config->updateOption("showLibrary");

	ConfigOption* showFilenameInLibrary = m_config->addOption("showFilenameInLibrary");
	showFilenameInLibrary->connect([this](const QVariant& value) {
			m_libraryView->setShowFilename(value.toBool());
	}, this);
	m_config->updateOption("showFilenameInLibrary");
	ConfigOption* libraryStyle = m_config->addOption("libraryStyle");
	libraryStyle->connect([this](const QVariant& value) {
		m_libraryView->setViewStyle(static_cast<LibraryStyle>(value.toInt()));
	}, this);
	m_config->updateOption("libraryStyle");

	connect(m_libraryView, &LibraryController::startGame, [this]() {
		VFile* output = m_libraryView->selectedVFile();
		if (output) {
			QPair<QString, QString> path = m_libraryView->selectedPath();
			setController(m_manager->loadGame(output, path.second, path.first), path.first + "/" + path.second);
		}
	});
#endif
#if defined(M_CORE_GBA)
	QSize minimumSize = QSize(GBA_VIDEO_HORIZONTAL_PIXELS, GBA_VIDEO_VERTICAL_PIXELS);
#elif defined(M_CORE_GB)
	QSize minimumSize = QSize(GB_VIDEO_HORIZONTAL_PIXELS, GB_VIDEO_VERTICAL_PIXELS);
#endif
	setMinimumSize(minimumSize);
	if (i > 0) {
		m_initialSize = minimumSize * i;
	} else {
		m_initialSize = minimumSize * 2;
	}
	setLogo();

	connect(this, &Window::shutdown, m_logView, &QWidget::hide);
	connect(&m_fpsTimer, &QTimer::timeout, this, &Window::showFPS);
	connect(&m_focusCheck, &QTimer::timeout, this, &Window::focusCheck);
	connect(&m_inputController, &InputController::profileLoaded, m_shortcutController, &ShortcutController::loadProfile);

	m_log.setLevels(mLOG_WARN | mLOG_ERROR | mLOG_FATAL);
	m_log.load(m_config);
	m_fpsTimer.setInterval(FPS_TIMER_INTERVAL);
	m_focusCheck.setInterval(200);
	m_mustRestart.setInterval(MUST_RESTART_TIMEOUT);
	m_mustRestart.setSingleShot(true);
	m_mustReset.setInterval(MUST_RESTART_TIMEOUT);
	m_mustReset.setSingleShot(true);

	m_shortcutController->setConfigController(m_config);
	m_shortcutController->setActionMapper(&m_actions);
	setupMenu(menuBar());
	setupOptions();
	if (customVirtualPadSettings(m_config).autoboot) {
		QTimer::singleShot(0, this, &Window::openVirtualPad);
	}
}

Window::~Window() {
	delete m_logView;

#ifdef USE_SQLITE3
	delete m_libraryView;
#endif
}

void Window::argumentsPassed() {
	const mArguments* args = m_config->args();

	if (args->patch) {
		m_pendingPatch = args->patch;
	}

	if (args->savestate) {
		m_pendingState = args->savestate;
	}

#ifdef ENABLE_SCRIPTING
	QVariant startupScript = m_config->takeArgvOption("script");
	if (!startupScript.isNull()) {
		// Delay actual loading until the window is shown and the core has started.
		// That lets Python scripts drive the visible GUI instance instead of a
		// second hidden core.
		m_pendingScript = startupScript.toString();
		m_pendingScriptRetries = 0;
		traceStartupScript(QStringLiteral("qt:args script=%1").arg(m_pendingScript));
		traceStartupScript(QStringLiteral("qt:args schedule"));
		QTimer::singleShot(250, this, &Window::runPendingStartupScript);
	}
#endif

#ifdef USE_GDB_STUB
	if (args->debuggerType == DEBUGGER_GDB) {
		if (!m_gdbController) {
			m_gdbController = new GDBController(this);
			if (m_controller) {
				m_gdbController->setController(m_controller);
			}
			m_gdbController->attach();
			m_gdbController->listen();
		}
	}
#endif

	if (m_config->graphicsOpts()->multiplier > 0) {
		m_savedScale = m_config->graphicsOpts()->multiplier;

#if defined(M_CORE_GBA)
		QSize size(GBA_VIDEO_HORIZONTAL_PIXELS, GBA_VIDEO_VERTICAL_PIXELS);
#elif defined(M_CORE_GB)
		QSize size(GB_VIDEO_HORIZONTAL_PIXELS, GB_VIDEO_VERTICAL_PIXELS);
#endif
		m_initialSize = size * m_savedScale;
	}

	if (args->fname) {
		setController(m_manager->loadGame(args->fname), args->fname);
	}
#ifdef ENABLE_SCRIPTING
	else if (qEnvironmentVariableIsSet("MGBA_QT_SCRIPTING_AUTOTEST_OPEN") && !m_scriptingAutotestOpened) {
		// The first-run runtime-load regression starts with no ROM at all, then
		// has Python call mgba.qt.load_rom() from the scripting window. The
		// original autotest hook lived in gameStarted(), which cannot fire until
		// after a ROM exists, so open the test-driven scripting window here only
		// for the no-initial-ROM case.
		m_scriptingAutotestOpened = true;
		QTimer::singleShot(0, this, &Window::scriptingOpen);
	}
#endif

	if (m_config->graphicsOpts()->fullscreen) {
		enterFullScreen();
	}
}

void Window::resizeFrame(const QSize& size) {
	QSize newSize(size);
	if (windowHandle()) {
		QRect geom = windowHandle()->screen()->availableGeometry();
		if (newSize.width() > geom.width()) {
			newSize.setWidth(geom.width());
		}
		if (newSize.height() > geom.height()) {
			newSize.setHeight(geom.height());
		}
	}
	newSize += this->size();
	newSize -= centralWidget()->size();
	if (!isFullScreen()) {
		resize(newSize);
	}
}

void Window::updateMultiplayerStatus(bool canOpenAnother) {
	m_multiWindow->setEnabled(canOpenAnother);
	if (m_controller) {
		MultiplayerController* multiplayer = m_controller->multiplayerController();
		if (multiplayer) {
			m_playerId = multiplayer->playerId(m_controller.get());
		}
	}
}

void Window::updateMultiplayerActive(bool active) {
	m_multiActive = active;
	updateMute();
}

void Window::setConfig(ConfigController* config) {
	m_config = config;
}

void Window::loadConfig() {
	const mCoreOptions* opts = m_config->options();
	reloadConfig();

	if (opts->width && opts->height) {
		m_initialSize = QSize(opts->width, opts->height);
	}

	if (opts->fullscreen) {
		enterFullScreen();
	}

	m_mruFiles = m_config->getMRU();
	updateMRU();

	m_inputController.setConfiguration(m_config);
}

void Window::reloadConfig() {
	const mCoreOptions* opts = m_config->options();

	m_log.setLevels(opts->logLevel);

	if (m_controller) {
		m_controller->loadConfig(m_config);
		const bool fastForwardEnabled = fastForwardStartsEnabled(m_config);
		Action* fastForwardAction = m_actions.getAction("fastForward");
		if (fastForwardAction) {
			fastForwardAction->setActive(fastForwardEnabled);
		}
		if (m_audioProcessor) {
			m_audioProcessor->configure(m_config);
		}
		updateMute();
		syncCustomFeatureState();
		applyVirtualPadSettings();
		m_display->resizeContext();
	}

	m_inputController.setScreensaverSuspendable(opts->suspendScreensaver);
}

void Window::saveConfig() {
	m_inputController.saveConfiguration();
	m_config->write();
}

QString Window::getFilters() const {
	QStringList filters;
	QStringList formats;

#ifdef M_CORE_GBA
	QStringList gbaFormats{
		"*.gba",
#if defined(USE_LIBZIP) || defined(USE_MINIZIP)
		"*.zip",
#endif
#ifdef USE_LZMA
		"*.7z",
#endif
#ifdef USE_ELF
		"*.elf",
#endif
		"*.agb",
		"*.mb",
		"*.rom",
		"*.bin"};
	formats.append(gbaFormats);
	filters.append(tr("Game Boy Advance ROMs (%1)").arg(gbaFormats.join(QChar(' '))));
#endif

#ifdef M_CORE_GB
	QStringList gbFormats{
		"*.gb",
		"*.gbc",
		"*.sgb",
#if defined(USE_LIBZIP) || defined(USE_MINIZIP)
		"*.zip",
#endif
#ifdef USE_LZMA
		"*.7z",
#endif
		"*.rom",
		"*.bin"};
	formats.append(gbFormats);
	filters.append(tr("Game Boy ROMs (%1)").arg(gbFormats.join(QChar(' '))));
#endif

	formats.removeDuplicates();
	filters.prepend(tr("All ROMs (%1)").arg(formats.join(QChar(' '))));
	filters.append(tr("%1 Video Logs (*.mvl)").arg(projectName));
	return filters.join(";;");
}

QString Window::getFiltersArchive() const {
	QStringList filters;

	QStringList formats{
#if defined(USE_LIBZIP) || defined(USE_MINIZIP)
		"*.zip",
#endif
#ifdef USE_LZMA
		"*.7z",
#endif
	};
	filters.append(tr("Archives (%1)").arg(formats.join(QChar(' '))));
	return filters.join(";;");
}

void Window::selectROM() {
	QString filename = GBAApp::app()->getOpenFileName(this, tr("Select ROM"), getFilters());
	if (!filename.isEmpty()) {
		setController(m_manager->loadGame(filename), filename);
	}
}

void Window::bootBIOS() {
	QString bios(m_config->getOption("gba.bios"));
	if (bios.isEmpty()) {
		bios = m_config->getOption("bios");
	}
	setController(m_manager->loadBIOS(mPLATFORM_GBA, bios), QString());
}

#ifdef USE_SQLITE3
void Window::selectROMInArchive() {
	QString filename = GBAApp::app()->getOpenFileName(this, tr("Select ROM"), getFiltersArchive());
	if (filename.isEmpty()) {
		return;
	}
	ArchiveInspector* archiveInspector = new ArchiveInspector(filename);
	connect(archiveInspector, &QDialog::accepted, [this,  archiveInspector]() {
		VFile* output = archiveInspector->selectedVFile();
		QPair<QString, QString> path = archiveInspector->selectedPath();
		if (output) {
			setController(m_manager->loadGame(output, path.second, path.first), path.first + "/" + path.second);
		}
		archiveInspector->close();
	});
	archiveInspector->setAttribute(Qt::WA_DeleteOnClose);
	archiveInspector->show();
}

void Window::addDirToLibrary() {
	QString filename = GBAApp::app()->getOpenDirectoryName(this, tr("Select folder"));
	if (filename.isEmpty()) {
		return;
	}
	m_libraryView->addDirectory(filename);
}
#endif

void Window::replaceROM() {
	QString filename = GBAApp::app()->getOpenFileName(this, tr("Select ROM"), getFilters());
	if (!filename.isEmpty()) {
		m_controller->replaceGame(filename);
	}
}

QString Window::canonicalSavePathHint() const {
	if (!m_controller) {
		return QString();
	}
	// Use the mapped core options here, not just the raw config table. Portable
	// defaults such as `userdata/savegames` are injected as defaults and would be
	// invisible if we only queried the explicit config entries.
	const char* configuredPath = m_controller->thread()->core->opts.savegamePath;
	QString directory = configuredPath ? QString::fromUtf8(configuredPath) : QString();
	if (directory.isEmpty() && !windowFilePath().isEmpty()) {
		QFileInfo romInfo(windowFilePath());
		directory = romInfo.dir().canonicalPath();
	}
	if (directory.isEmpty()) {
		return m_controller->currentSaveFileName();
	}
	return QDir(directory).filePath(m_controller->currentSaveFileName());
}

void Window::importSaveFile() {
	QStringList formats{"*.sav", "*.sa?"};
	QString filter = tr("Save games (%1)").arg(formats.join(QChar(' ')));
	QString filename = GBAApp::app()->getOpenFileName(this, tr("Import save game"), filter, canonicalSavePathHint());
	if (!filename.isEmpty()) {
		m_controller->importSave(filename);
	}
}

void Window::exportSaveFile() {
	QStringList formats{"*.sav", "*.sa?"};
	QString filter = tr("Save games (%1)").arg(formats.join(QChar(' ')));
	QString filename = GBAApp::app()->getSaveFileName(this, tr("Export save game"), filter, canonicalSavePathHint());
	if (filename.isEmpty()) {
		return;
	}
	QFileInfo info(filename);
	if (info.suffix().isEmpty()) {
		// Mirror the active save slot suffix so exporting player 2/3/4 saves
		// does not silently collapse them all to plain `.sav`.
		filename += m_controller->currentSaveSuffix();
	}
	m_controller->exportSave(filename);
}

void Window::selectSave(bool temporary) {
	QStringList formats{"*.sav", "*.sa?"};
	QString filter = tr("Save games (%1)").arg(formats.join(QChar(' ')));
	QString filename = GBAApp::app()->getOpenFileName(this, tr("Select save game"), filter);
	if (!filename.isEmpty()) {
		m_controller->loadSave(filename, temporary);
	}
}

void Window::selectState(bool load) {
	QStringList formats{"*.ss0", "*.ss1", "*.ss2", "*.ss3", "*.ss4", "*.ss5", "*.ss6", "*.ss7", "*.ss8", "*.ss9"};
	QString filter = tr("mGBA save state files (%1)").arg(formats.join(QChar(' ')));
	if (load) {
		QString filename = GBAApp::app()->getOpenFileName(this, tr("Select save state"), filter);
		if (!filename.isEmpty()) {
			m_controller->loadState(filename);
		}
	} else {
		QString filename = GBAApp::app()->getSaveFileName(this, tr("Select save state"), filter);
		if (!filename.isEmpty()) {
			m_controller->saveState(filename);
		}
	}
}

void Window::multiplayerChanged() {
	if (!m_controller) {
		return;
	}
	int attached = 1;
	MultiplayerController* multiplayer = m_controller->multiplayerController();
	if (multiplayer) {
		attached = multiplayer->attached();
	}
	for (Action* action : m_nonMpActions) {
		action->setEnabled(attached < 2);
	}
}

void Window::selectPatch() {
	QString filename = GBAApp::app()->getOpenFileName(this, tr("Select patch"), tr("Patches (*.ips *.ups *.bps)"));
	if (!filename.isEmpty()) {
		if (m_controller) {
			m_controller->loadPatch(filename);
		} else {
			m_pendingPatch = filename;
		}
	}
}

void Window::scanCard() {
	QStringList filenames = GBAApp::app()->getOpenFileNames(this, tr("Select e-Reader dotcode"), tr("e-Reader card (*.raw *.bin *.bmp)"));
	for (QString& filename : filenames) {
		m_controller->scanCard(filename);
	}
}

void Window::parseCard() {
#ifdef USE_FFMPEG
	QStringList filenames = GBAApp::app()->getOpenFileNames(this, tr("Select e-Reader card images"), tr("Image file (*.png *.jpg *.jpeg)"));
	QMessageBox* dialog = new QMessageBox(QMessageBox::Information, tr("Conversion finished"),
	                                      QString("oh"), QMessageBox::Ok);
	dialog->setAttribute(Qt::WA_DeleteOnClose);
	auto status = std::make_shared<QPair<int, int>>(0, filenames.size());
	GBAApp::app()->submitWorkerJob([filenames, status]() {
		int success = 0;
		for (QString filename : filenames) {
			if (filename.isEmpty()) {
				continue;
			}
			QImage image(filename);
			if (image.isNull()) {
				continue;
			}
			EReaderScan* scan;
			switch (image.depth()) {
			case 8:
				scan = EReaderScanLoadImage8(image.constBits(), image.width(), image.height(), image.bytesPerLine());
				break;
			case 24:
				scan = EReaderScanLoadImage(image.constBits(), image.width(), image.height(), image.bytesPerLine());
				break;
			case 32:
				scan = EReaderScanLoadImageA(image.constBits(), image.width(), image.height(), image.bytesPerLine());
				break;
			default:
				continue;
			}
			QFileInfo ofile(filename);
			if (EReaderScanCard(scan)) {
				QString ofilename = ofile.path() + QDir::separator() + ofile.baseName() + ".raw";
				EReaderScanSaveRaw(scan, ofilename.toUtf8().constData(), false);
				++success;
			}
			EReaderScanDestroy(scan);
		}
		status->first = success;
	}, [dialog, status]() {
		if (status->second == 0) {
			return;
		}
		dialog->setText(tr("%1 of %2 e-Reader cards converted successfully.").arg(status->first).arg(status->second));
		dialog->show();
		applyWindowsDarkChrome(dialog);
	});
#endif
}

void Window::openView(QWidget* widget) {
	connect(this, &Window::shutdown, widget, &QWidget::close);
	widget->setAttribute(Qt::WA_DeleteOnClose);
	widget->show();
	applyWindowsDarkChrome(widget);
}

void Window::loadCamImage() {
	QString filename = GBAApp::app()->getOpenFileName(this, tr("Select image"), tr("Image file (*.png *.gif *.jpg *.jpeg);;All files (*)"));
	if (!filename.isEmpty()) {
		m_inputController.loadCamImage(filename);
	}
}

void Window::importSharkport() {
	QString filename = GBAApp::app()->getOpenFileName(this, tr("Select save"), tr("GameShark saves (*.gsv *.sps *.xps)"));
	if (!filename.isEmpty()) {
		m_controller->importSharkport(filename);
	}
}

void Window::exportSharkport() {
	QString filename = GBAApp::app()->getSaveFileName(this, tr("Select save"), tr("GameShark saves (*.sps *.xps)"));
	if (!filename.isEmpty()) {
		m_controller->exportSharkport(filename);
	}
}

void Window::openSettingsWindow() {
	openSettingsWindow(SettingsView::Page::AV);
}

void Window::openSettingsWindow(SettingsView::Page page) {
	SettingsView* settingsWindow = new SettingsView(m_config, &m_inputController, m_shortcutController, &m_log);
#if defined(BUILD_GL) || defined(BUILD_GLES2)
	if (m_display->supportsShaders()) {
		settingsWindow->setShaderSelector(m_shaderView.get());
	}
#endif
	connect(settingsWindow, &SettingsView::displayDriverChanged, this, &Window::reloadDisplayDriver);
	connect(settingsWindow, &SettingsView::audioDriverChanged, this, &Window::reloadAudioDriver);
	connect(settingsWindow, &SettingsView::cameraDriverChanged, this, &Window::mustReset);
	connect(settingsWindow, &SettingsView::cameraChanged, &m_inputController, &InputController::setCamera);
	connect(settingsWindow, &SettingsView::videoRendererChanged, this, &Window::changeRenderer);
	connect(settingsWindow, &SettingsView::languageChanged, this, &Window::mustRestart);
	connect(settingsWindow, &SettingsView::pathsChanged, this, &Window::reloadConfig);
	connect(settingsWindow, &SettingsView::audioHleChanged, this, [this]() {
		if (!m_controller) {
			return;
		}
		if (m_controller->platform() != mPLATFORM_GBA) {
			return;
		}
		mustReset();
	});
#ifdef USE_SQLITE3
	connect(settingsWindow, &SettingsView::libraryCleared, m_libraryView, &LibraryController::clear);
#endif
	openView(settingsWindow);
	settingsWindow->selectPage(page);
}

void Window::openSavestateMemoryDialog() {
	QDialog dialog(this);
	dialog.setWindowTitle(tr("Savestate Memory Cache"));
	dialog.setModal(true);
	applyWindowsDarkChrome(&dialog);

	QVBoxLayout* layout = new QVBoxLayout(&dialog);
	QLabel* intro = new QLabel(tr(
		"Keep recently used savestates resident in RAM so repeated load/save loops can avoid extra disk reads. "
		"Recent states are still written to disk when saved; this cache mainly speeds up hot reload paths."));
	intro->setWordWrap(true);
	layout->addWidget(intro);

	QCheckBox* enabled = new QCheckBox(tr("Enable savestate memory cache"));
	enabled->setChecked(m_config->getOption("customSavestateCacheEnabled", false).toInt());
	layout->addWidget(enabled);

	QFormLayout* form = new QFormLayout();
	QSpinBox* maxStates = new QSpinBox();
	maxStates->setRange(1, 1000000);
	maxStates->setValue(m_config->getOption("customSavestateCacheMaxEntries", 64).toInt());
	form->addRow(tr("Maximum cached states:"), maxStates);

	QSpinBox* maxMiB = new QSpinBox();
	maxMiB->setRange(1, 1024 * 1024);
	maxMiB->setValue(m_config->getOption("customSavestateCacheMaxMB", 4096).toInt());
	form->addRow(tr("Maximum RAM (MiB):"), maxMiB);

	if (m_controller) {
		QLabel* currentStats = new QLabel(tr("Current cache usage: %1 states, %2 MiB")
			.arg(m_controller->savestateCacheEntryCount())
			.arg(QString::number(double(m_controller->savestateCacheUsedBytes()) / (1024.0 * 1024.0), 'f', 2)));
		layout->addWidget(currentStats);
	}

	layout->addLayout(form);

	QDialogButtonBox* buttons = new QDialogButtonBox(QDialogButtonBox::Ok | QDialogButtonBox::Cancel);
	connect(buttons, &QDialogButtonBox::accepted, &dialog, &QDialog::accept);
	connect(buttons, &QDialogButtonBox::rejected, &dialog, &QDialog::reject);
	layout->addWidget(buttons);

	if (dialog.exec() != QDialog::Accepted) {
		return;
	}

	m_config->setOption("customSavestateCacheEnabled", enabled->isChecked());
	m_config->setOption("customSavestateCacheMaxEntries", maxStates->value());
	m_config->setOption("customSavestateCacheMaxMB", maxMiB->value());
	reloadConfig();
	saveConfig();
}

void Window::openWorkerInstanceDialog() {
	showWorkerInstanceDialog(this);
}

void Window::openVirtualPad() {
	if (!m_virtualPad) {
		m_virtualPad = new VirtualPad(this);
		m_virtualPad->setAttribute(Qt::WA_DeleteOnClose);
		m_virtualPad->setSettingsHandler([this]() {
			openVirtualPadSettings();
		});
		m_virtualPad->setSettingsChangedHandler([this](const VirtualPadSettings& settings) {
			saveCustomVirtualPadSettings(m_config, settings);
			saveConfig();
		});
		connect(m_virtualPad, &QObject::destroyed, this, [this]() {
			m_virtualPad = nullptr;
		});
	}

	applyVirtualPadSettings();
	m_virtualPad->setController(m_controller);
	m_virtualPad->show();
	applyWindowsDarkChrome(m_virtualPad);
	m_virtualPad->raise();
	m_virtualPad->activateWindow();
}

void Window::openVirtualPadSettings() {
	VirtualPadSettings settings = customVirtualPadSettings(m_config);
	VirtualPadSettings updatedSettings = settings;
	QWidget* settingsOwner = m_virtualPad && m_virtualPad->isVisible()
		? static_cast<QWidget*>(m_virtualPad.data())
		: static_cast<QWidget*>(this);
	QVector<AlwaysOnTopRestore> restoreOnTop = suspendAlwaysOnTopForModalDialog({
		m_virtualPad && m_virtualPad->isVisible() ? static_cast<QWidget*>(m_virtualPad.data()) : nullptr,
		m_inputTapeView && m_inputTapeView->isVisible() ? static_cast<QWidget*>(m_inputTapeView.data()) : nullptr,
	});
	const bool accepted = showVirtualPadSettingsDialog(settingsOwner, settings, &updatedSettings);
	restoreAlwaysOnTopAfterModalDialog(restoreOnTop);
	if (!accepted) {
		return;
	}

	saveCustomVirtualPadSettings(m_config, updatedSettings);
	saveConfig();
	applyVirtualPadSettings();
}

void Window::openInputTapeView() {
	if (!m_inputTapeView) {
		m_inputTapeView = new InputTapeView(m_config, this);
		m_inputTapeView->setAttribute(Qt::WA_DeleteOnClose);
		m_inputTapeView->setMappedInputSampler([this]() -> uint32_t {
			return controllerKeyMask();
		});
		m_inputTapeView->setVirtualPadSampler([this]() -> uint32_t {
			return virtualPadKeyMask();
		});
		connect(m_inputTapeView, &QObject::destroyed, this, [this]() {
			m_inputTapeView = nullptr;
		});
	}

	m_inputTapeView->setController(m_controller);
	m_inputTapeView->show();
	applyWindowsDarkChrome(m_inputTapeView);
	m_inputTapeView->raise();
	m_inputTapeView->activateWindow();
}

void Window::openSpindaProjectView() {
	if (!m_spindaProjectView) {
		m_spindaProjectView = new SpindaProjectView(this);
		m_spindaProjectView->setAttribute(Qt::WA_DeleteOnClose);
		connect(m_spindaProjectView, &QObject::destroyed, this, [this]() {
			m_spindaProjectView = nullptr;
		});
	}

	m_spindaProjectView->setController(m_controller);
	m_spindaProjectView->show();
	applyWindowsDarkChrome(m_spindaProjectView);
	m_spindaProjectView->raise();
	m_spindaProjectView->activateWindow();
}

bool Window::virtualPadSetHeld(int key, bool held) {
	openVirtualPad();
	return m_virtualPad && m_virtualPad->setHeld(key, held);
}

bool Window::virtualPadSetAutofire(int key, bool enable) {
	openVirtualPad();
	return m_virtualPad && m_virtualPad->setAutofire(key, enable);
}

bool Window::virtualPadPressForFrames(int key, int frames) {
	openVirtualPad();
	return m_virtualPad && m_virtualPad->pressForFrames(key, frames);
}

uint32_t Window::virtualPadKeyMask() const {
	return m_virtualPad ? m_virtualPad->keyMask() : 0;
}

uint32_t Window::controllerKeyMask() const {
	return m_controller ? m_controller->pendingKeys() : 0;
}

bool Window::virtualPadClear() {
	openVirtualPad();
	if (!m_virtualPad) {
		return false;
	}
	m_virtualPad->clearAll();
	return true;
}

void Window::startVideoLog() {
	QString filename = GBAApp::app()->getSaveFileName(this, tr("Select video log"), tr("Video logs (*.mvl)"));
	if (!filename.isEmpty()) {
		m_controller->startVideoLog(filename);
	}
}

template <typename T, typename... A>
std::function<void()> Window::openTView(A... arg) {
	return [=]() {
		T* view = new T(arg...);
		openView(view);
	};
}

template <typename T, typename... A>
std::function<void()> Window::openControllerTView(A... arg) {
	return [=]() {
		T* view = new T(m_controller, arg...);
		openView(view);
	};
}

template <typename T, typename... A>
std::function<void()> Window::openNamedTView(std::unique_ptr<T>* name, A... arg) {
	return [=]() {
		if (!*name) {
			*name = std::make_unique<T>(arg...);
			connect(this, &Window::shutdown, name->get(), &QWidget::close);
		}
		(*name)->show();
		(*name)->setFocus(Qt::PopupFocusReason);
	};
}

template <typename T, typename... A>
std::function<void()> Window::openNamedControllerTView(std::unique_ptr<T>* name, A... arg) {
	return [=]() {
		if (!*name) {
			*name = std::make_unique<T>(arg...);
			if (m_controller) {
				(*name)->setController(m_controller);
			}
			connect(this, &Window::shutdown, name->get(), &QWidget::close);
		}
		(*name)->show();
		(*name)->setFocus(Qt::PopupFocusReason);
	};
}

#ifdef USE_GDB_STUB
void Window::gdbOpen() {
	if (!m_gdbController) {
		m_gdbController = new GDBController(this);
	}
	GDBWindow* window = new GDBWindow(m_gdbController);
	m_gdbController->setController(m_controller);
	connect(m_controller.get(), &CoreController::stopping, window, &QWidget::close);
	openView(window);
}
#endif

#ifdef USE_DEBUGGERS
void Window::consoleOpen() {
	if (!m_console) {
		m_console = new DebuggerConsoleController(this);
	}
	DebuggerConsole* window = new DebuggerConsole(m_console);
	if (m_controller) {
		m_console->setController(m_controller);
	}
	openView(window);
}
#endif

#ifdef ENABLE_SCRIPTING
void Window::scriptingOpen() {
#ifdef ENABLE_PYTHON
	// The CFFI bridge may initialize embedded Python the first time the
	// scripting window touches Python-specific features. Seed PYTHONHOME here so
	// the visible Qt window and the startup-script path see the same stdlib.
	mPythonEnsureEnvironment();
#endif
	if (!m_scripting) {
		// Tool windows hold raw controller pointers. Parent the shared scripting
		// controller to the main window so it outlives those views during close.
		m_scripting = new ScriptingController(this);
		connect(m_scripting, &QObject::destroyed, this, [this]() {
			m_scripting = nullptr;
		});
		if (m_controller) {
			m_scripting->setController(m_controller);
		}
	}
	ScriptingView* view = new ScriptingView(m_scripting, m_config);
	openView(view);
}

void Window::runPendingStartupScript() {
	traceStartupScript(QStringLiteral("qt:runPending enter"));
	if (m_pendingScript.isEmpty()) {
		traceStartupScript(QStringLiteral("qt:runPending empty"));
		return;
	}
#ifdef ENABLE_PYTHON
	// Normalize the embedded Python home before the scripting controller or the
	// load path can trigger any CFFI-side interpreter startup.
	mPythonEnsureEnvironment();
#endif
	if (!m_controller || !m_controller->hasStarted()) {
		// Startup scripts are allowed to touch frame state immediately, so wait
		// until the live core is really running before binding Python to it.
		traceStartupScript(QStringLiteral("qt:runPending waiting started=%1 controller=%2 retry=%3")
			.arg(m_controller && m_controller->hasStarted())
			.arg(bool(m_controller))
			.arg(m_pendingScriptRetries));
		if (m_pendingScriptRetries++ < 100) {
			QTimer::singleShot(25, this, &Window::runPendingStartupScript);
		}
		return;
	}

	if (!m_scripting) {
		// Reuse the normal scripting controller so startup scripts and manual
		// Tools > Scripting runs share the same bridge logic. Keep it parented to
		// the main window so close ordering stays on Qt's side, not member order.
		m_scripting = new ScriptingController(this);
		connect(m_scripting, &QObject::destroyed, this, [this]() {
			m_scripting = nullptr;
		});
		if (m_controller) {
			m_scripting->setController(m_controller);
		}
	}
	QString scriptPath = m_pendingScript;
	m_pendingScript.clear();
	m_pendingScriptRetries = 0;
	traceStartupScript(QStringLiteral("qt:runPending loadFile=%1").arg(scriptPath));
	traceStartupScript(QStringLiteral("qt:runPending loadFile call"));
#ifdef ENABLE_PYTHON
	m_scripting->setNextPythonSessionSkipsCoreInterrupt(true);
#endif
	bool scriptOk = m_scripting->loadFile(scriptPath);
	traceStartupScript(QStringLiteral("qt:runPending loadFile returned ok=%1").arg(scriptOk));
	if (!scriptOk) {
		traceStartupScript(QStringLiteral("qt:runPending loadFile failed"));
		LOG(QT, ERROR) << tr("Failed to load startup script: %1").arg(scriptPath);
	} else {
		traceStartupScript(QStringLiteral("qt:runPending loadFile ok"));
	}
}

void Window::retryCloseAfterPythonAbort() {
	if (!m_pendingClose) {
		return;
	}
	if (m_scripting && m_scripting->pythonScriptActive()) {
		traceStartupScript(QStringLiteral("qt:close wait-python-abort"));
		QTimer::singleShot(25, this, &Window::retryCloseAfterPythonAbort);
		return;
	}
	traceStartupScript(QStringLiteral("qt:close retry-after-abort"));
	close();
}

#endif

void Window::keyPressEvent(QKeyEvent* event) {
	if (event->isAutoRepeat()) {
		QWidget::keyPressEvent(event);
		return;
	}
	GBAKey key = m_inputController.mapKeyboard(event->key());
	if (key == GBA_KEY_NONE) {
		QWidget::keyPressEvent(event);
		return;
	}
	if (m_controller) {
		m_controller->addKey(key);
	}
	event->accept();
}

void Window::keyReleaseEvent(QKeyEvent* event) {
	if (event->isAutoRepeat()) {
		QWidget::keyReleaseEvent(event);
		return;
	}
	GBAKey key = m_inputController.mapKeyboard(event->key());
	if (key == GBA_KEY_NONE) {
		QWidget::keyPressEvent(event);
		return;
	}
	if (m_controller) {
		m_controller->clearKey(key);
	}
	event->accept();
}

void Window::resizeEvent(QResizeEvent*) {
	QSize newSize = centralWidget()->size();
	updateNoRenderOverlay();
	if (!isFullScreen()) {
		m_config->setOption("height", newSize.height());
		m_config->setOption("width", newSize.width());
	}

	int factor = 0;
	QSize size(GBA_VIDEO_HORIZONTAL_PIXELS, GBA_VIDEO_VERTICAL_PIXELS);
	if (m_controller) {
		size = m_controller->screenDimensions();
	}
	if (newSize.width() % size.width() == 0 && newSize.height() % size.height() == 0 &&
	    newSize.width() / size.width() == newSize.height() / size.height()) {
		factor = newSize.width() / size.width();
	}
	m_savedScale = factor;
	for (QMap<int, Action*>::iterator iter = m_frameSizes.begin(); iter != m_frameSizes.end(); ++iter) {
		iter.value()->setActive(iter.key() == factor);
	}

	m_config->setOption("fullscreen", isFullScreen());
}

void Window::syncThemeChrome() {
	syncMenuBarTheme(menuBar());
	applyWindowsDarkChrome(this);
	repolishWidget(this);
}

void Window::changeEvent(QEvent* event) {
	QMainWindow::changeEvent(event);
	if (event->type() == QEvent::ActivationChange || event->type() == QEvent::WindowStateChange) {
		syncThemeChrome();
	}
}

void Window::showEvent(QShowEvent* event) {
	if (m_wasOpened) {
		if (event->spontaneous() && m_controller) {
			focusCheck();
			if (m_config->getOption("pauseOnMinimize").toInt() && m_autoresume) {
				m_controller->setPaused(false);
				m_autoresume = false;
			}

			if (m_config->getOption("muteOnMinimize").toInt()) {
				m_inactiveMute = false;
				updateMute();
			}
		}
		return;
	}
	m_wasOpened = true;
	syncThemeChrome();
	if (m_initialSize.isValid()) {
		resizeFrame(m_initialSize);
	}
	QVariant windowPos = m_config->getQtOption("windowPos", m_playerId > 0 ? QString("player%0").arg(m_playerId) : QString());
	bool maximized = m_config->getQtOption("maximized").toBool();
	QRect geom = windowHandle()->screen()->availableGeometry();
	if (!windowPos.isNull() && geom.contains(windowPos.toPoint())) {
		move(windowPos.toPoint());
	} else {
		QRect rect = frameGeometry();
		rect.moveCenter(geom.center());
		move(rect.topLeft());
	}
	if (maximized) {
		showMaximized();
	}
	if (m_fullscreenOnStart) {
		enterFullScreen();
		m_fullscreenOnStart = false;
	}
	reloadDisplayDriver();
	setFocus();
}

void Window::hideEvent(QHideEvent* event) {
	if (!event->spontaneous()) {
		return;
	}
	if (!m_controller) {
		return;
	}

	if (m_config->getOption("pauseOnMinimize").toInt() && !m_controller->isPaused()) {
		m_autoresume = true;
		m_controller->setPaused(true);
	}
	if (m_config->getOption("muteOnMinimize").toInt()) {
		m_inactiveMute = true;
		updateMute();
	}
}

void Window::closeEvent(QCloseEvent* event) {
	traceStartupScript(QStringLiteral("qt:close enter controller=%1 pending=%2 scripting=%3")
		.arg(bool(m_controller))
		.arg(m_pendingClose)
		.arg(bool(m_scripting && m_scripting->pythonScriptActive())));
	m_config->setQtOption("windowPos", pos(), m_playerId > 0 ? QString("player%0").arg(m_playerId) : QString());
	m_config->setQtOption("maximized", isMaximized());

	if (m_savedScale > 0) {
		m_config->setOption("height", GBA_VIDEO_VERTICAL_PIXELS * m_savedScale);
		m_config->setOption("width", GBA_VIDEO_HORIZONTAL_PIXELS * m_savedScale);
	}
	saveConfig();
	if (m_controller) {
#ifdef ENABLE_SCRIPTING
	if (m_scripting && m_scripting->requestAbortPythonScript()) {
		// A visible Qt Python script may be holding the controller interrupt
		// for deterministic frame stepping. Ask it to unwind first, remember
		// that the user wants to close, and retry one normal close after the
		// scripting bridge has gone inactive.
		// Use a small delayed retry instead of a zero-delay repost; nested event
		// processing during startup-script teardown was still able to hit the
		// retry too early and make the close path flaky.
		traceStartupScript(QStringLiteral("qt:close abort-requested"));
		event->ignore();
		m_pendingClose = true;
		QTimer::singleShot(25, this, &Window::retryCloseAfterPythonAbort);
		return;
	}
#endif
		emit shutdown();
		traceStartupScript(QStringLiteral("qt:close shutdown"));
		event->ignore();
		m_pendingClose = true;
	} else {
		clearNoRenderOverlayState();
		m_display.reset();
	}
}

void Window::focusInEvent(QFocusEvent*) {
	for (Window* window : GBAApp::app()->windows()) {
		if (window != this) {
			window->updateMultiplayerActive(false);
		} else {
			updateMultiplayerActive(true);
		}
	}
	if (m_display) {
		m_display->forceDraw();
	}
}

void Window::focusOutEvent(QFocusEvent*) {
}

void Window::dragEnterEvent(QDragEnterEvent* event) {
	if (event->mimeData()->hasFormat("text/uri-list")) {
		event->acceptProposedAction();
	}
}

void Window::dropEvent(QDropEvent* event) {
	QString uris = event->mimeData()->data("text/uri-list");
	uris = uris.trimmed();
	if (uris.contains("\n")) {
		// Only one file please
		return;
	}
	QUrl url(uris);
	if (!url.isLocalFile()) {
		// No remote loading
		return;
	}
	event->accept();
	setController(m_manager->loadGame(url.toLocalFile()), url.toLocalFile());
}

void Window::mouseDoubleClickEvent(QMouseEvent* event) {
	if (event->button() != Qt::LeftButton) {
		return;
	}
	toggleFullScreen();
}

void Window::enterFullScreen() {
	if (!isVisible()) {
		m_fullscreenOnStart = true;
		return;
	}
	if (isFullScreen()) {
		return;
	}
	showFullScreen();
#ifndef Q_OS_MAC
	if (m_controller && !m_controller->isPaused()) {
		menuBar()->hide();
	}
#endif
}

void Window::exitFullScreen() {
	if (!isFullScreen()) {
		return;
	}
	centralWidget()->unsetCursor();
	menuBar()->show();
	showNormal();
}

void Window::toggleFullScreen() {
	if (isFullScreen()) {
		exitFullScreen();
	} else {
		enterFullScreen();
	}
}

void Window::gameStarted() {
	for (Action* action : m_gameActions) {
		action->setEnabled(true);
	}
	for (auto action = m_platformActions.begin(); action != m_platformActions.end(); ++action) {
		action.value()->setEnabled(m_controller->platform() == action.key());
	}
	const bool fastForwardEnabled = fastForwardStartsEnabled(m_config);
	if (Action* fastForwardAction = m_actions.getAction("fastForward")) {
		fastForwardAction->setActive(fastForwardEnabled);
	}
	QSize size = m_controller->screenDimensions();
	m_config->updateOption("lockIntegerScaling");
	m_config->updateOption("lockAspectRatio");
	m_config->updateOption("interframeBlending");
	m_config->updateOption("resampleVideo");
	if (m_savedScale > 0) {
		resizeFrame(size * m_savedScale);
	}
	attachWidget(m_display.get());
	setFocus();

#ifndef Q_OS_MAC
	if (isFullScreen()) {
		menuBar()->hide();
	}
#endif

	syncCustomFeatureState();
	reloadAudioDriver();
	const quint64 audioStartupFrame = m_controller->frameCounter();
	QTimer::singleShot(750, this, [this, audioStartupFrame]() {
		if (!m_controller || m_controller->isPaused() || !m_controller->audioSync() || m_controller->audioKillswitchEnabled()) {
			return;
		}
		const quint64 advancedFrames = m_controller->frameCounter() - audioStartupFrame;
		if (advancedFrames >= 10) {
			return;
		}

		// Qt Multimedia can report an active output on Windows while not
		// draining samples fast enough. If audio sync is left enabled, the core
		// waits on audio and the user sees a frozen game even though Pause is
		// off. Prefer an SDL fallback, but keep the game moving even if no audio
		// backend is usable.
		LOG(QT, WARN) << tr("Audio sync startup stall detected; switching audio sync to a safe mode");
		m_controller->setAudioSyncBlocked(true);
#ifdef BUILD_SDL
		if (AudioProcessor::driver() != AudioProcessor::Driver::SDL) {
			if (m_audioProcessor) {
				m_audioProcessor->stop();
				m_audioProcessor.reset();
			}
			std::unique_ptr<AudioProcessor> fallback(AudioProcessor::create(AudioProcessor::Driver::SDL));
			fallback->setInput(m_controller);
			fallback->configure(m_config);
			if (fallback->start()) {
				m_audioProcessor = std::move(fallback);
				m_controller->setAudioSyncBlocked(false);
				LOG(QT, WARN) << tr("Using SDL audio after Qt Multimedia startup stalled");
			}
		}
#endif
	});
	writeCustomFeaturesAutotestMarker(this);
	scheduleStartupFrameAutotestMarker(this);
	multiplayerChanged();
	updateTitle();

	m_hitUnimplementedBiosCall = false;
	if (m_config->getOption("showFps", "1").toInt()) {
		m_fpsTimer.start();
		m_frameTimer.start();
	}
	m_focusCheck.start();
	if (m_display->underMouse()) {
		centralWidget()->setCursor(Qt::BlankCursor);
	}

	CoreController::Interrupter interrupter(m_controller);
	mCore* core = m_controller->thread()->core;
	m_actions.clearMenu("videoLayers");
	m_actions.clearMenu("audioChannels");
	const mCoreChannelInfo* videoLayers;
	const mCoreChannelInfo* audioChannels;
	size_t nVideo = core->listVideoLayers(core, &videoLayers);
	size_t nAudio = core->listAudioChannels(core, &audioChannels);

	if (nVideo) {
		for (size_t i = 0; i < nVideo; ++i) {
			Action* action = m_actions.addBooleanAction(videoLayers[i].visibleName, QString("videoLayer.%1").arg(videoLayers[i].internalName), [this, videoLayers, i](bool enable) {
				m_controller->thread()->core->enableVideoLayer(m_controller->thread()->core, videoLayers[i].id, enable);
			}, "videoLayers");
			action->setActive(m_controller->videoLayerEnabled(videoLayers[i].id));
		}
	}
	if (nAudio) {
		for (size_t i = 0; i < nAudio; ++i) {
			Action* action = m_actions.addBooleanAction(audioChannels[i].visibleName, QString("audioChannel.%1").arg(audioChannels[i].internalName), [this, audioChannels, i](bool enable) {
				m_controller->thread()->core->enableAudioChannel(m_controller->thread()->core, audioChannels[i].id, enable);
			}, "audioChannels");
			action->setActive(true);
		}
	}
	interrupter.resume();

	m_actions.rebuildMenu(menuBar(), this, *m_shortcutController);

#ifdef M_CORE_GBA
	if (m_controller->platform() == mPLATFORM_GBA) {
		QVariant eCardList = m_config->takeArgvOption(QString("ecard"));
		if (eCardList.canConvert(QMetaType::QStringList)) {
			m_controller->scanCards(eCardList.toStringList());
		}
	}
#endif

#ifdef USE_DISCORD_RPC
	DiscordCoordinator::gameStarted(m_controller);
#endif

#ifdef ENABLE_SCRIPTING
	if (qEnvironmentVariableIsSet("MGBA_QT_SCRIPTING_AUTOTEST_OPEN") && !m_scriptingAutotestOpened) {
		// Local regression hook: the deployment tests need the real menu-bar
		// scripting window so Python parity is exercised through the same UI the
		// user sees, not a private helper path.
		m_scriptingAutotestOpened = true;
		QTimer::singleShot(0, this, &Window::scriptingOpen);
	}
#endif
	if (qEnvironmentVariableIsSet("MGBA_SPINDA_NATIVE_PHASE3_AUTORUN") && !m_spindaNativePhase3AutorunStarted) {
		m_spindaNativePhase3AutorunStarted = true;
		const bool spindaHeadless = spindaNativePhase3EnvBool("HEADLESS");
		QTimer::singleShot(0, this, [this, spindaHeadless]() {
			if (spindaHeadless) {
				if (!m_spindaProjectView) {
					m_spindaProjectView = new SpindaProjectView(this);
					m_spindaProjectView->setAttribute(Qt::WA_DeleteOnClose);
					connect(m_spindaProjectView, &QObject::destroyed, this, [this]() {
						m_spindaProjectView = nullptr;
					});
				}
				m_spindaProjectView->setController(m_controller);
			} else {
				openSpindaProjectView();
			}
			if (m_spindaProjectView) {
				m_spindaProjectView->startPhase3LaneFromEnvironment();
			}
		});
	}
	if (!m_saveImportExportAutotestDone
	 && (qEnvironmentVariableIsSet("MGBA_QT_AUTOTEST_IMPORT_SAVE")
	  || qEnvironmentVariableIsSet("MGBA_QT_AUTOTEST_EXPORT_SAVE"))) {
		m_saveImportExportAutotestDone = true;
		QTimer::singleShot(0, this, &Window::runSaveImportExportAutotest);
	}

}

void Window::runSaveImportExportAutotest() {
	if (!m_controller) {
		return;
	}

	const QString importPath = qEnvironmentVariable("MGBA_QT_AUTOTEST_IMPORT_SAVE");
	const QString exportPath = qEnvironmentVariable("MGBA_QT_AUTOTEST_EXPORT_SAVE");
	const QString markerPath = qEnvironmentVariable("MGBA_QT_AUTOTEST_SAVE_MARKER");

	auto writeMarker = [this, markerPath, exportPath]() {
		if (markerPath.isEmpty()) {
			return;
		}

		QJsonObject root;
		root.insert(QStringLiteral("canonicalSavePath"), canonicalSavePathHint());
		root.insert(QStringLiteral("canonicalSaveExists"), QFileInfo::exists(canonicalSavePathHint()));
		root.insert(QStringLiteral("exportPath"), exportPath);
		root.insert(QStringLiteral("exportExists"), !exportPath.isEmpty() && QFileInfo::exists(exportPath));
		QFile marker(markerPath);
		if (marker.open(QIODevice::WriteOnly | QIODevice::Truncate | QIODevice::Text)) {
			marker.write(QJsonDocument(root).toJson(QJsonDocument::Indented));
		}
	};

	auto finishExport = [this, exportPath, writeMarker]() {
		if (!exportPath.isEmpty()) {
			m_controller->exportSave(exportPath);
		}
		writeMarker();
	};

	if (!importPath.isEmpty()) {
		// This hook exists only for deployment tests. It drives the same
		// permanent import path as the new menu action, then waits for the reset
		// it triggers before exporting the live save back out for comparison.
		QMetaObject::Connection* connection = new QMetaObject::Connection;
		*connection = connect(m_controller.get(), &CoreController::didReset, this, [this, connection, finishExport]() {
			disconnect(*connection);
			delete connection;
			QTimer::singleShot(150, this, finishExport);
		});
		m_controller->importSave(importPath);
		return;
	}

	QTimer::singleShot(150, this, finishExport);
}

void Window::gameStopped() {
	traceStartupScript(QStringLiteral("qt:gameStopped pending=%1 controller=%2")
		.arg(m_pendingClose)
		.arg(bool(m_controller)));
	for (Action* action : m_platformActions) {
		action->setEnabled(true);
	}
	for (Action* action : m_gameActions) {
		action->setEnabled(false);
	}
	setWindowFilePath(QString());

	m_actions.clearMenu("videoLayers");
	m_actions.clearMenu("audioChannels");

	m_fpsTimer.stop();
	m_focusCheck.stop();

	if (m_audioProcessor) {
		m_audioProcessor->stop();
		m_audioProcessor.reset();
	}
	m_display->stopDrawing();
	setLogo();
	if (m_display) {
#ifdef M_CORE_GB
		m_display->setMinimumSize(GB_VIDEO_HORIZONTAL_PIXELS, GB_VIDEO_VERTICAL_PIXELS);
#elif defined(M_CORE_GBA)
		m_display->setMinimumSize(GBA_VIDEO_HORIZONTAL_PIXELS, GBA_VIDEO_VERTICAL_PIXELS);
#endif
	}

	if (m_virtualPad) {
		m_virtualPad->setController(std::shared_ptr<CoreController>());
	}
	if (m_inputTapeView) {
		m_inputTapeView->setController(std::shared_ptr<CoreController>());
	}
	if (m_spindaProjectView) {
		m_spindaProjectView->setController(std::shared_ptr<CoreController>());
	}
	m_controller.reset();
	detachWidget();
	updateNoRenderOverlay();
	updateTitle();

	if (m_pendingClose) {
		clearNoRenderOverlayState();
		m_display.reset();
		close();
	}
#ifndef Q_OS_MAC
	menuBar()->show();
#endif

#ifdef USE_DISCORD_RPC
	DiscordCoordinator::gameStopped();
#endif

	emit paused(false);
}

void Window::gameCrashed(const QString& errorMessage) {
	QMessageBox* crash = new QMessageBox(QMessageBox::Critical, tr("Crash"),
	                                     tr("The game has crashed with the following error:\n\n%1").arg(errorMessage),
	                                     QMessageBox::Ok, this, Qt::Sheet);
	crash->setAttribute(Qt::WA_DeleteOnClose);
	crash->show();
	applyWindowsDarkChrome(crash);
}

void Window::gameFailed() {
	QMessageBox* fail = new QMessageBox(QMessageBox::Warning, tr("Couldn't Start"),
	                                    tr("Could not start game."),
	                                    QMessageBox::Ok, this, Qt::Sheet);
	fail->setAttribute(Qt::WA_DeleteOnClose);
	fail->show();
	applyWindowsDarkChrome(fail);
}

void Window::unimplementedBiosCall(int) {
	// TODO: Mention which call?
	if (m_hitUnimplementedBiosCall) {
		return;
	}
	m_hitUnimplementedBiosCall = true;

	QMessageBox* fail = new QMessageBox(
	    QMessageBox::Warning, tr("Unimplemented BIOS call"),
	    tr("This game uses a BIOS call that is not implemented. Please use the official BIOS for best experience."),
	    QMessageBox::Ok, this, Qt::Sheet);
	fail->setAttribute(Qt::WA_DeleteOnClose);
	fail->show();
	applyWindowsDarkChrome(fail);
}

void Window::reloadDisplayDriver() {
	if (m_controller) {
		m_display->stopDrawing();
		detachWidget();
	}
	clearNoRenderOverlayState();
	m_display = std::unique_ptr<QGBA::Display>(Display::create(this));
	if (!m_display) {
		LOG(QT, ERROR) << tr("Failed to create an appropriate display device, falling back to software display. "
		                     "Games may run slowly, especially with larger windows.");
		Display::setDriver(Display::Driver::QT);
		m_display = std::unique_ptr<Display>(Display::create(this));
	}
#if defined(BUILD_GL) || defined(BUILD_GLES2)
	m_shaderView.reset();
	m_shaderView = std::make_unique<ShaderSelector>(m_display.get(), m_config);
#endif

	connect(m_display.get(), &QGBA::Display::hideCursor, [this]() {
		if (centralWidget() == m_display.get()) {
			centralWidget()->setCursor(Qt::BlankCursor);
		}
	});
	connect(m_display.get(), &QGBA::Display::showCursor, [this]() {
		centralWidget()->unsetCursor();
	});

	m_display->configure(m_config);
#if defined(BUILD_GL) || defined(BUILD_GLES2)
	if (m_shaderView) {
		m_shaderView->refreshShaders();
	}
#endif

	if (m_controller) {
		attachDisplay();

		attachWidget(m_display.get());
		syncCustomFeatureState();
	}
#ifdef M_CORE_GB
	m_display->setMinimumSize(GB_VIDEO_HORIZONTAL_PIXELS, GB_VIDEO_VERTICAL_PIXELS);
#elif defined(M_CORE_GBA)
	m_display->setMinimumSize(GBA_VIDEO_HORIZONTAL_PIXELS, GBA_VIDEO_VERTICAL_PIXELS);
#endif
}

void Window::reloadAudioDriver() {
	if (!m_controller) {
		return;
	}
	// Disable audio waiting while the frontend driver is being rebuilt. Some
	// Windows Qt Multimedia configurations report an output object but never
	// consume enough samples; leaving audio sync enabled during that window makes
	// the core look frozen even though Pause is off.
	m_controller->setAudioSyncBlocked(true);
	if (m_audioProcessor) {
		m_audioProcessor->stop();
		m_audioProcessor.reset();
	}
	if (m_controller->audioKillswitchEnabled()) {
		return;
	}

	auto tryStartAudioDriver = [this](AudioProcessor::Driver driver) {
		std::unique_ptr<AudioProcessor> candidate(AudioProcessor::create(driver));
		candidate->setInput(m_controller);
		candidate->configure(m_config);
		if (!candidate->start()) {
			candidate->stop();
			return false;
		}
		m_audioProcessor = std::move(candidate);
		m_controller->setAudioSyncBlocked(false);
		return true;
	};

	const AudioProcessor::Driver configuredDriver = AudioProcessor::driver();
	if (tryStartAudioDriver(configuredDriver)) {
		return;
	}

#ifdef BUILD_SDL
	if (configuredDriver != AudioProcessor::Driver::SDL && tryStartAudioDriver(AudioProcessor::Driver::SDL)) {
		LOG(QT, WARN) << tr("Configured audio driver failed to start; using SDL audio for this session");
		return;
	}
#endif

	// Last resort: keep video/input responsive instead of letting audio sync hold
	// the emulation thread on a frontend that cannot currently consume samples.
	LOG(QT, WARN) << tr("Audio driver failed to start; audio sync disabled for this session");
}

void Window::changeRenderer() {
	if (!m_controller) {
		return;
	}

	CoreController::Interrupter interrupter(m_controller);
	if (m_config->getOption("hwaccelVideo").toInt() && m_display->supportsShaders() && m_controller->supportsFeature(CoreController::Feature::OPENGL)) {
		std::shared_ptr<VideoProxy> proxy = m_display->videoProxy();
		if (!proxy) {
			proxy = std::make_shared<VideoProxy>();
		}
		m_display->setVideoProxy(proxy);
		proxy->attach(m_controller.get());

		int fb = m_display->framebufferHandle();
		if (fb >= 0) {
			m_controller->setFramebufferHandle(fb);
			m_config->updateOption("videoScale");
		}
	} else {
		std::shared_ptr<VideoProxy> proxy = m_display->videoProxy();
		if (proxy) {
			proxy->detach(m_controller.get());
			m_display->setVideoProxy({});
		}
		m_controller->setFramebufferHandle(-1);
	}
}

void Window::tryMakePortable() {
	QMessageBox* confirm = new QMessageBox(QMessageBox::Question, tr("Really make portable?"),
	                                       tr("This will make the emulator load its configuration from the same directory as the executable. Do you want to continue?"),
	                                       QMessageBox::Yes | QMessageBox::Cancel, this, Qt::Sheet);
	confirm->setAttribute(Qt::WA_DeleteOnClose);
	connect(confirm->button(QMessageBox::Yes), &QAbstractButton::clicked, m_config, &ConfigController::makePortable);
	confirm->show();
	applyWindowsDarkChrome(confirm);
}

void Window::mustRestart() {
	if (m_mustRestart.isActive()) {
		return;
	}
	m_mustRestart.start();
	QMessageBox* dialog = new QMessageBox(QMessageBox::Warning, tr("Restart needed"),
	                                      tr("Some changes will not take effect until the emulator is restarted."),
	                                      QMessageBox::Ok, this, Qt::Sheet);
	dialog->setAttribute(Qt::WA_DeleteOnClose);
	dialog->show();
	applyWindowsDarkChrome(dialog);
}

void Window::mustReset() {
	if (m_mustReset.isActive() || !m_controller) {
		return;
	}
	m_mustReset.start();
	QMessageBox* dialog = new QMessageBox(QMessageBox::Warning, tr("Reset needed"),
	                                      tr("Some changes will not take effect until the game is reset."),
	                                      QMessageBox::Ok, this, Qt::Sheet);
	dialog->setAttribute(Qt::WA_DeleteOnClose);
	dialog->show();
	applyWindowsDarkChrome(dialog);
}

void Window::recordFrame() {
	m_frameList.append(m_frameTimer.nsecsElapsed());
	m_frameTimer.restart();
}

void Window::showFPS() {
	qint64 total = 0;
	for (qint64 t : m_frameList) {
		total += t;
	}
	if (!total) {
		updateTitle();
		return;
	}
	double fps = (m_frameList.size() * 1e10) / total;
	m_frameList.clear();
	fps = round(fps) / 10.f;
	updateTitle(fps);
}

void Window::updateTitle(float fps) {
	QString title;
	if (m_config->getOption("dynamicTitle", 1).toInt() && m_controller) {
		QString filePath = windowFilePath();
		if (m_config->getOption("showFilename").toInt() && !filePath.isNull()) {
			QFileInfo fileInfo(filePath);
			title = fileInfo.fileName();
		} else {
			title = m_controller->title();
		}

		MultiplayerController* multiplayer = m_controller->multiplayerController();
		if (multiplayer && multiplayer->attached() > 1) {
			title += tr(" -  Player %1 of %2").arg(m_playerId + 1).arg(multiplayer->attached());
			for (Action* action : m_nonMpActions) {
				action->setEnabled(false);
			}
		} else {
			for (Action* action : m_nonMpActions) {
				action->setEnabled(true);
			}
		}
	}
	if (title.isNull()) {
		setWindowTitle(tr("%1 - %2").arg(projectName).arg(projectVersion));
	} else if (fps < 0) {
		setWindowTitle(tr("%1 - %2 - %3").arg(projectName).arg(title).arg(projectVersion));
	} else {
		setWindowTitle(tr("%1 - %2 (%3 fps) - %4").arg(projectName).arg(title).arg(fps).arg(projectVersion));
	}
}

void Window::openStateWindow(LoadSave ls) {
	if (m_stateWindow) {
		return;
	}
	MultiplayerController* multiplayer = m_controller->multiplayerController();
	if (multiplayer && multiplayer->attached() > 1) {
		return;
	}
	bool wasPaused = m_controller->isPaused();
	m_stateWindow = new LoadSaveState(m_controller);
	connect(this, &Window::shutdown, m_stateWindow, &QWidget::close);
	connect(m_stateWindow, &LoadSaveState::closed, [this]() {
		attachWidget(m_display.get());
		m_stateWindow = nullptr;
		QMetaObject::invokeMethod(this, "setFocus", Qt::QueuedConnection);
	});
	if (!wasPaused) {
		m_controller->setPaused(true);
		connect(m_stateWindow, &LoadSaveState::closed, [this]() {
			if (m_controller) {
				m_controller->setPaused(false);
			}
		});
	}
	m_stateWindow->setAttribute(Qt::WA_DeleteOnClose);
	m_stateWindow->setMode(ls);

	m_stateWindow->setDimensions(m_controller->screenDimensions());
	m_config->updateOption("lockAspectRatio");
	m_config->updateOption("lockIntegerScaling");

	QImage still(m_controller->getPixels());
	if (still.format() != QImage::Format_RGB888) {
		still = still.convertToFormat(QImage::Format_RGB888);
	}
	if (still.height() > 512 || still.width() > 512) {
		still = still.scaled(384, 256, Qt::KeepAspectRatio, Qt::SmoothTransformation).convertToFormat(QImage::Format_RGB888);
	}
	QImage output(still.size(), QImage::Format_RGB888);
	size_t dims[] = {7, 7};
	struct ConvolutionKernel kern;
	ConvolutionKernelCreate(&kern, 2, dims);
	ConvolutionKernelFillRadial(&kern, true);
	Convolve2DClampChannels8(still.constBits(), output.bits(), still.width(), still.height(), still.bytesPerLine(), 3, &kern);
	ConvolutionKernelDestroy(&kern);

	QPixmap pixmap;
	pixmap.convertFromImage(output);
	m_stateWindow->setBackground(pixmap);

#ifndef Q_OS_MAC
	menuBar()->show();
#endif
	attachWidget(m_stateWindow);
}

void Window::setupMenu(QMenuBar* menubar) {
	installEventFilter(m_shortcutController);

	menubar->clear();
	m_actions.addMenu(tr("&File"), "file");

	m_actions.addAction(tr("Load &ROM..."), "loadROM", this, &Window::selectROM, "file", QKeySequence::Open);

#ifdef USE_SQLITE3
	m_actions.addAction(tr("Load ROM in archive..."), "loadROMInArchive", this, &Window::selectROMInArchive, "file");
	m_actions.addAction(tr("Add folder to library..."), "addDirToLibrary", this, &Window::addDirToLibrary, "file");
#endif

	m_actions.addMenu(tr("Save games"), "saves", "file");
	addGameAction(tr("Import Save..."), "importSave", this, &Window::importSaveFile, "saves");
	addGameAction(tr("Export Save..."), "exportSave", this, &Window::exportSaveFile, "saves");
	m_actions.addSeparator("saves");
	addGameAction(tr("Load alternate save game..."), "loadAlternateSave", [this]() {
		this->selectSave(false);
	}, "saves");
	addGameAction(tr("Load temporary save game..."), "loadTemporarySave", [this]() {
		this->selectSave(true);
	}, "saves");

	m_actions.addSeparator("saves");

	m_actions.addAction(tr("Convert save game..."), "convertSave", openTView<SaveConverter>(), "saves");

#ifdef M_CORE_GBA
	Action* importShark = addGameAction(tr("Import GameShark Save..."), "importShark", this, &Window::importSharkport, "saves");
	m_platformActions.insert(mPLATFORM_GBA, importShark);

	Action* exportShark = addGameAction(tr("Export GameShark Save..."), "exportShark", this, &Window::exportSharkport, "saves");
	m_platformActions.insert(mPLATFORM_GBA, exportShark);
#endif

	m_actions.addSeparator("saves");
	Action* savePlayerAction;
	ConfigOption* savePlayer = m_config->addOption("savePlayerId");
	savePlayerAction = savePlayer->addValue(tr("Automatically determine"), 0, &m_actions, "saves");
	m_nonMpActions.append(savePlayerAction);

	for (int i = 1; i < 5; ++i) {
		savePlayerAction = savePlayer->addValue(tr("Use player %0 save game").arg(i), i, &m_actions, "saves");
		m_nonMpActions.append(savePlayerAction);
	}
	savePlayer->connect([this](const QVariant& value) {
		if (m_controller) {
			m_controller->changePlayer(value.toInt());
		}
	}, this);
	m_config->updateOption("savePlayerId");

	m_actions.addAction(tr("Load &patch..."), "loadPatch", this, &Window::selectPatch, "file");

#ifdef M_CORE_GBA
	m_actions.addAction(tr("Boot BIOS"), "bootBIOS", this, &Window::bootBIOS, "file");
#endif

#ifdef M_CORE_GBA
	Action* scanCard = addGameAction(tr("Scan e-Reader dotcodes..."), "scanCard", this, &Window::scanCard, "file");
	m_platformActions.insert(mPLATFORM_GBA, scanCard);
#endif

	addGameAction(tr("ROM &info..."), "romInfo", openControllerTView<ROMInfo>(), "file");

	m_actions.addMenu(tr("Recent"), "mru", "file");
	m_actions.addSeparator("file");

	Action* loadState = addGameAction(tr("&Load state"), "loadState", [this]() {
		this->openStateWindow(LoadSave::LOAD);
	}, "file", QKeySequence("F10"));
	m_nonMpActions.append(loadState);

	Action* loadStateFile = addGameAction(tr("Load state file..."), "loadStateFile", [this]() {
		this->selectState(true);
	}, "file");
	m_nonMpActions.append(loadStateFile);

	Action* saveState = addGameAction(tr("&Save state"), "saveState", [this]() {
		this->openStateWindow(LoadSave::SAVE);
	}, "file", QKeySequence("Shift+F10"));
	m_nonMpActions.append(saveState);

	Action* saveStateFile = addGameAction(tr("Save state file..."), "saveStateFile", [this]() {
		this->selectState(false);
	}, "file");
	m_nonMpActions.append(saveStateFile);

	m_actions.addMenu(tr("Quick load"), "quickLoad", "file");
	m_actions.addMenu(tr("Quick save"), "quickSave", "file");

	Action* quickLoad = addGameAction(tr("Load recent"), "quickLoad", [this] {
		m_controller->loadState();
	}, "quickLoad");
	m_nonMpActions.append(quickLoad);

	Action* quickSave = addGameAction(tr("Save recent"), "quickSave", [this] {
		m_controller->saveState();
	}, "quickSave");
	m_nonMpActions.append(quickSave);

	m_actions.addSeparator("quickLoad");
	m_actions.addSeparator("quickSave");

	Action* undoLoadState = addGameAction(tr("Undo load state"), "undoLoadState", &CoreController::loadBackupState, "quickLoad", QKeySequence("F11"));
	m_nonMpActions.append(undoLoadState);

	Action* undoSaveState = addGameAction(tr("Undo save state"), "undoSaveState", &CoreController::saveBackupState, "quickSave", QKeySequence("Shift+F11"));
	m_nonMpActions.append(undoSaveState);

	m_actions.addSeparator("quickLoad");
	m_actions.addSeparator("quickSave");

	for (int i = 1; i < 10; ++i) {
		Action* quickLoad = addGameAction(tr("State &%1").arg(i),  QString("quickLoad.%1").arg(i), [this, i]() {
			m_controller->loadState(i);
		}, "quickLoad", QString("F%1").arg(i));
		m_nonMpActions.append(quickLoad);

		Action* quickSave = addGameAction(tr("State &%1").arg(i),  QString("quickSave.%1").arg(i), [this, i]() {
			m_controller->saveState(i);
		}, "quickSave", QString("Shift+F%1").arg(i));
		m_nonMpActions.append(quickSave);
	}

	m_actions.addSeparator("file");
	m_multiWindow = m_actions.addAction(tr("New multiplayer window"), "multiWindow", GBAApp::app(), &GBAApp::newWindow, "file");

#ifdef M_CORE_GBA
	Action* dolphin = m_actions.addAction(tr("Connect to Dolphin..."), "connectDolphin", openNamedTView<DolphinConnector>(&m_dolphinView, this), "file");
	m_platformActions.insert(mPLATFORM_GBA, dolphin);
#endif

	m_actions.addSeparator("file");

	m_actions.addAction(tr("Report bug..."), "bugReport", openTView<ReportView>(), "file");

#ifndef Q_OS_MAC
	m_actions.addSeparator("file");
#endif

	m_actions.addAction(tr("About..."), "about", openTView<AboutScreen>(), "file")->setRole(Action::Role::ABOUT);
	m_actions.addAction(tr("E&xit"), "quit", static_cast<QWidget*>(this), &QWidget::close, "file", QKeySequence::Quit)->setRole(Action::Role::QUIT);

	m_actions.addMenu(tr("&Emulation"), "emu");
	addGameAction(tr("&Reset"), "reset", &CoreController::reset, "emu", QKeySequence("Ctrl+R"));
	addGameAction(tr("Sh&utdown"), "shutdown", &CoreController::stop, "emu");
	m_actions.addSeparator("emu");

	addGameAction(tr("Replace ROM..."), "replaceROM", this, &Window::replaceROM, "emu");
	addGameAction(tr("Yank game pak"), "yank", &CoreController::yankPak, "emu");
	m_actions.addSeparator("emu");

	Action* pause = m_actions.addBooleanAction(tr("&Pause"), "pause", [this](bool paused) {
		if (m_controller) {
			m_controller->setPaused(paused);
		} else {
			m_pendingPause = paused;
		}
	}, "emu", QKeySequence("Ctrl+P"));
	connect(this, &Window::paused, pause, &Action::setActive);

	addGameAction(tr("&Next frame"), "frameAdvance", &CoreController::frameAdvance, "emu", QKeySequence("Ctrl+N"));

	m_actions.addSeparator("emu");

	m_actions.addHeldAction(tr("Fast forward (held)"), "holdFastForward", [this](bool held) {
		if (m_controller) {
			m_controller->setFastForward(held);
		}
	}, "emu", QKeySequence(Qt::Key_Tab));

	Action* fastForward = addGameAction(tr("&Fast forward"), "fastForward", [this](bool value) {
		m_controller->forceFastForward(value);
		m_config->setOption("fastForward", value);
	}, "emu", QKeySequence("Shift+Tab"));
	fastForward->setActive(fastForwardStartsEnabled(m_config));

	m_actions.addMenu(tr("Fast forward speed"), "fastForwardSpeed", "emu");
	ConfigOption* ffspeed = m_config->addOption("fastForwardRatio");
	ffspeed->connect([this](const QVariant&) {
		reloadConfig();
	}, this);
	ffspeed->addValue(tr("Unbounded"), -1.0f, &m_actions, "fastForwardSpeed");
	ffspeed->setValue(QVariant(-1.0f));
	m_actions.addSeparator("fastForwardSpeed");
	for (int i = 2; i < 11; ++i) {
		ffspeed->addValue(tr("%0x").arg(i), i, &m_actions, "fastForwardSpeed");
	}
	m_config->updateOption("fastForwardRatio");

	Action* rewindHeld = m_actions.addHeldAction(tr("Rewind (held)"), "holdRewind", [this](bool held) {
		if (m_controller) {
			m_controller->setRewinding(held);
		}
	}, "emu", QKeySequence("`"));
	m_nonMpActions.append(rewindHeld);

	Action* rewind = addGameAction(tr("Re&wind"), "rewind", [this]() {
		m_controller->rewind();
	}, "emu", QKeySequence("~"));
	m_nonMpActions.append(rewind);

	Action* frameRewind = addGameAction(tr("Step backwards"), "frameRewind", [this] () {
		m_controller->rewind(1);
	}, "emu", QKeySequence("Ctrl+B"));
	m_nonMpActions.append(frameRewind);

	m_actions.addSeparator("emu");

	m_actions.addMenu(tr("Solar sensor"), "solar", "emu");
	m_actions.addAction(tr("Increase solar level"), "increaseLuminanceLevel", &m_inputController, &InputController::increaseLuminanceLevel, "solar");
	m_actions.addAction(tr("Decrease solar level"), "decreaseLuminanceLevel", &m_inputController, &InputController::decreaseLuminanceLevel, "solar");
	m_actions.addAction(tr("Brightest solar level"), "maxLuminanceLevel", [this]() {
		m_inputController.setLuminanceLevel(10);
	}, "solar");
	m_actions.addAction(tr("Darkest solar level"), "minLuminanceLevel", [this]() {
		m_inputController.setLuminanceLevel(0);
	}, "solar");

	m_actions.addSeparator("solar");
	for (int i = 0; i <= 10; ++i) {
		m_actions.addAction(tr("Brightness %1").arg(QString::number(i)), QString("luminanceLevel.%1").arg(QString::number(i)), [this, i]() {
			m_inputController.setLuminanceLevel(i);
		}, "solar");
	}

#ifdef M_CORE_GB
	m_actions.addAction(tr("Load camera image..."), "loadCamImage", this, &Window::loadCamImage, "emu");

	Action* gbPrint = addGameAction(tr("Game Boy Printer..."), "gbPrint", [this]() {
		PrinterView* view = new PrinterView(m_controller);
		openView(view);
		m_controller->attachPrinter();
	}, "emu");
	m_platformActions.insert(mPLATFORM_GB, gbPrint);
#endif

#ifdef M_CORE_GBA
	Action* bcGate = addGameAction(tr("BattleChip Gate..."), "bcGate", openControllerTView<BattleChipView>(this), "emu");
	m_platformActions.insert(mPLATFORM_GBA, bcGate);
#endif

	m_actions.addMenu(tr("Audio/&Video"), "av");
	m_actions.addMenu(tr("Frame size"), "frame", "av");
	for (int i = 1; i <= 8; ++i) {
		Action* setSize = m_actions.addAction(tr("%1×").arg(QString::number(i)), QString("frame.%1x").arg(QString::number(i)), [this, i]() {
			Action* setSize = m_frameSizes[i];
			showNormal();
			QSize size(GBA_VIDEO_HORIZONTAL_PIXELS, GBA_VIDEO_VERTICAL_PIXELS);
			if (m_controller) {
				size = m_controller->screenDimensions();
			}
			size *= i;
			m_savedScale = i;
			m_config->setOption("scaleMultiplier", i); // TODO: Port to other
			resizeFrame(size);
			setSize->setActive(true);
		}, "frame");
		setSize->setExclusive(true);
		if (m_savedScale == i) {
			setSize->setActive(true);
		}
		m_frameSizes[i] = setSize;
	}
	QKeySequence fullscreenKeys;
#ifdef Q_OS_WIN
	fullscreenKeys = QKeySequence("Alt+Return");
#else
	fullscreenKeys = QKeySequence("Ctrl+F");
#endif
	m_actions.addAction(tr("Toggle fullscreen"), "fullscreen", this, &Window::toggleFullScreen, "frame", fullscreenKeys);

	ConfigOption* lockAspectRatio = m_config->addOption("lockAspectRatio");
	lockAspectRatio->addBoolean(tr("Lock aspect ratio"), &m_actions, "av");
	lockAspectRatio->connect([this](const QVariant& value) {
		if (m_display) {
			m_display->lockAspectRatio(value.toBool());
		}
		if (m_stateWindow) {
			m_stateWindow->setLockAspectRatio(value.toBool());
		}
	}, this);
	m_config->updateOption("lockAspectRatio");

	ConfigOption* lockIntegerScaling = m_config->addOption("lockIntegerScaling");
	lockIntegerScaling->addBoolean(tr("Force integer scaling"), &m_actions, "av");
	lockIntegerScaling->connect([this](const QVariant& value) {
		if (m_display) {
			m_display->lockIntegerScaling(value.toBool());
		}
		if (m_stateWindow) {
			m_stateWindow->setLockIntegerScaling(value.toBool());
		}
	}, this);
	m_config->updateOption("lockIntegerScaling");

	ConfigOption* interframeBlending = m_config->addOption("interframeBlending");
	interframeBlending->addBoolean(tr("Interframe blending"), &m_actions, "av");
	interframeBlending->connect([this](const QVariant& value) {
		if (m_display) {
			m_display->interframeBlending(value.toBool());
		}
	}, this);
	m_config->updateOption("interframeBlending");

	ConfigOption* resampleVideo = m_config->addOption("resampleVideo");
	resampleVideo->addBoolean(tr("Bilinear filtering"), &m_actions, "av");
	resampleVideo->connect([this](const QVariant& value) {
		if (m_display) {
			m_display->filter(value.toBool());
		}
	}, this);
	m_config->updateOption("resampleVideo");

	m_actions.addMenu(tr("Frame&skip"),"skip", "av");
	ConfigOption* skip = m_config->addOption("frameskip");
	skip->connect([this](const QVariant&) {
		reloadConfig();
	}, this);
	for (int i = 0; i <= 10; ++i) {
		skip->addValue(QString::number(i), i, &m_actions, "skip");
	}
	m_config->updateOption("frameskip");

	m_actions.addSeparator("av");

	ConfigOption* mute = m_config->addOption("mute");
	Action* muteAction = mute->addBoolean(tr("Mute"), &m_actions, "av");
	muteAction->setActive(m_config->getOption("mute").toInt());
	mute->connect([this](const QVariant& value) {
		m_config->setOption("fastForwardMute", static_cast<bool>(value.toInt()));
		reloadConfig();
	}, this);

	m_actions.addMenu(tr("FPS target"),"target", "av");
	ConfigOption* fpsTargetOption = m_config->addOption("fpsTarget");
	QMap<double, Action*> fpsTargets;
	for (int fps : {15, 30, 45, 60, 90, 120, 240}) {
		fpsTargets[fps] = fpsTargetOption->addValue(QString::number(fps), fps, &m_actions, "target");
	}
	m_actions.addSeparator("target");
	double nativeGB = double(GBA_ARM7TDMI_FREQUENCY) / double(VIDEO_TOTAL_LENGTH);
	fpsTargets[nativeGB] = fpsTargetOption->addValue(tr("Native (59.7275)"), nativeGB, &m_actions, "target");

	fpsTargetOption->connect([this, fpsTargets](const QVariant& value) {
		reloadConfig();
		for (auto iter = fpsTargets.begin(); iter != fpsTargets.end(); ++iter) {
			bool enableSignals = iter.value()->blockSignals(true);
			iter.value()->setActive(abs(iter.key() - value.toDouble()) < 0.001);
			iter.value()->blockSignals(enableSignals);
		}
	}, this);
	m_config->updateOption("fpsTarget");

	m_actions.addSeparator("av");

#ifdef USE_PNG
	addGameAction(tr("Take &screenshot"), "screenshot", [this]() {
		m_controller->screenshot();
	}, "av", tr("F12"));
#endif

#ifdef USE_FFMPEG
	addGameAction(tr("Record A/V..."), "recordOutput", openNamedControllerTView<VideoView>(&m_videoView), "av");
	addGameAction(tr("Record GIF/WebP/APNG..."), "recordGIF", openNamedControllerTView<GIFView>(&m_gifView), "av");
#endif

	m_actions.addSeparator("av");
	m_actions.addMenu(tr("Video layers"), "videoLayers", "av");
	m_actions.addMenu(tr("Audio channels"), "audioChannels", "av");

	addGameAction(tr("Adjust layer placement..."), "placementControl", openControllerTView<PlacementControl>(), "av");

	m_actions.addMenu(tr("&Tools"), "tools");
	m_actions.addAction(tr("View &logs..."), "viewLogs", static_cast<QWidget*>(m_logView), &QWidget::show, "tools");

	m_actions.addAction(tr("Game &overrides..."), "overrideWindow", [this]() {
		if (!m_overrideView) {
			m_overrideView = std::make_unique<OverrideView>(m_config);
			if (m_controller) {
				m_overrideView->setController(m_controller);
			}
			connect(this, &Window::shutdown, m_overrideView.get(), &QWidget::close);
		}
		m_overrideView->show();
		m_overrideView->recheck();
	}, "tools");

	m_actions.addAction(tr("Game Pak sensors..."), "sensorWindow",  openNamedControllerTView<SensorView>(&m_sensorView, &m_inputController), "tools");

	addGameAction(tr("&Cheats..."), "cheatsWindow", openControllerTView<CheatsView>(), "tools");
#ifdef ENABLE_SCRIPTING
	m_actions.addAction(tr("Scripting..."), "scripting", this, &Window::scriptingOpen, "tools");
#endif
	m_actions.addMenu(tr("Custom Features"), "customFeatures", "tools");
	m_audioKillswitchAction = m_actions.addBooleanAction(tr("Audio killswitch"), "customAudioKillswitch", [this](bool enable) {
		setAudioKillswitch(enable);
	}, "customFeatures");
	m_noRenderModeAction = m_actions.addBooleanAction(tr("No-render mode"), "customNoRenderMode", [this](bool enable) {
		setNoRenderMode(enable);
	}, "customFeatures");
	m_audioKillswitchAction->setActive(m_config->getOption("customAudioKillswitch", false).toInt());
	m_noRenderModeAction->setActive(m_config->getOption("customNoRenderMode", false).toInt());
	m_actions.addSeparator("customFeatures");
	m_actions.addAction(tr("Input tapes..."), "customInputTapes", this, &Window::openInputTapeView, "customFeatures");
	m_actions.addAction(tr("Virtual Pad"), "customVirtualPad", this, &Window::openVirtualPad, "customFeatures");
	m_actions.addAction(tr("Virtual Pad settings..."), "customVirtualPadSettings", this, &Window::openVirtualPadSettings, "customFeatures");
	m_actions.addSeparator("customFeatures");
	m_actions.addAction(tr("Worker instances..."), "customWorkerInstances", this, &Window::openWorkerInstanceDialog, "customFeatures");
	m_actions.addAction(tr("Savestate memory cache..."), "customSavestateCache", this, &Window::openSavestateMemoryDialog, "customFeatures");
	m_actions.addAction(tr("Spinda project..."), "customSpindaProject", this, &Window::openSpindaProjectView, "customFeatures");

	m_actions.addSeparator("tools");
	m_actions.addAction(tr("Settings..."), "settings", this, &Window::openSettingsWindow, "tools")->setRole(Action::Role::SETTINGS);
	m_actions.addAction(tr("Make portable"), "makePortable", this, &Window::tryMakePortable, "tools");

	m_actions.addSeparator("tools");
#ifdef USE_DEBUGGERS
	m_actions.addAction(tr("Open debugger console..."), "debuggerWindow", this, &Window::consoleOpen, "tools");
#ifdef USE_GDB_STUB
	Action* gdbWindow = addGameAction(tr("Start &GDB server..."), "gdbWindow", this, &Window::gdbOpen, "tools");
	m_platformActions.insert(mPLATFORM_GBA, gdbWindow);
#endif
#endif
#if defined(USE_DEBUGGERS) || defined(ENABLE_SCRIPTING)
	m_actions.addSeparator("tools");
#endif

	m_actions.addMenu(tr("Game state views"), "stateViews", "tools");
	addGameAction(tr("View &palette..."), "paletteWindow", openControllerTView<PaletteView>(), "stateViews");
	addGameAction(tr("View &sprites..."), "spriteWindow", openControllerTView<ObjView>(), "stateViews");
	addGameAction(tr("View &tiles..."), "tileWindow", openControllerTView<TileView>(), "stateViews");
	addGameAction(tr("View &map..."), "mapWindow", openControllerTView<MapView>(), "stateViews");

	addGameAction(tr("&Frame inspector..."), "frameWindow", [this]() {
		if (!m_frameView) {
			m_frameView = new FrameView(m_controller);
			connect(this, &Window::shutdown, this, [this]() {
				if (m_frameView) {
					m_frameView->close();
				}
			});
			connect(m_frameView, &QObject::destroyed, this, [this]() {
				m_frameView = nullptr;
			});
			m_frameView->setAttribute(Qt::WA_DeleteOnClose);
		}
		m_frameView->show();
	}, "stateViews");

	addGameAction(tr("View memory..."), "memoryView", openControllerTView<MemoryView>(), "stateViews");
	addGameAction(tr("Search memory..."), "memorySearch", openControllerTView<MemorySearch>(), "stateViews");
	addGameAction(tr("View &I/O registers..."), "ioViewer", openControllerTView<IOViewer>(), "stateViews");

#if defined(USE_FFMPEG) && defined(M_CORE_GBA)
	m_actions.addSeparator("tools");
	m_actions.addAction(tr("Convert e-Reader card image to raw..."), "parseCard", this, &Window::parseCard, "tools");
#endif

	m_actions.addSeparator("tools");
	addGameAction(tr("Record debug video log..."), "recordVL", this, &Window::startVideoLog, "tools");
	addGameAction(tr("Stop debug video log"), "stopVL", [this]() {
		m_controller->endVideoLog();
	}, "tools");

	m_actions.addHiddenAction(tr("Exit fullscreen"), "exitFullScreen", this, &Window::exitFullScreen, "frame", QKeySequence("Esc"));

	m_actions.addHeldAction(tr("GameShark Button (held)"), "holdGSButton", [this](bool held) {
		if (m_controller) {
			mCheatPressButton(m_controller->cheatDevice(), held);
		}
	}, "tools");

	m_actions.addHiddenMenu(tr("Autofire"), "autofire");
	m_actions.addHeldAction(tr("Autofire A"), "autofireA", [this](bool held) {
		if (m_controller) {
			m_controller->setAutofire(GBA_KEY_A, held);
		}
	}, "autofire");
	m_actions.addHeldAction(tr("Autofire B"), "autofireB", [this](bool held) {
		if (m_controller) {
			m_controller->setAutofire(GBA_KEY_B, held);
		}
	}, "autofire");
	m_actions.addHeldAction(tr("Autofire L"), "autofireL", [this](bool held) {
		if (m_controller) {
			m_controller->setAutofire(GBA_KEY_L, held);
		}
	}, "autofire");
	m_actions.addHeldAction(tr("Autofire R"), "autofireR", [this](bool held) {
		if (m_controller) {
			m_controller->setAutofire(GBA_KEY_R, held);
		}
	}, "autofire");
	m_actions.addHeldAction(tr("Autofire Start"), "autofireStart", [this](bool held) {
		if (m_controller) {
			m_controller->setAutofire(GBA_KEY_START, held);
		}
	}, "autofire");
	m_actions.addHeldAction(tr("Autofire Select"), "autofireSelect", [this](bool held) {
		if (m_controller) {
			m_controller->setAutofire(GBA_KEY_SELECT, held);
		}
	}, "autofire");
	m_actions.addHeldAction(tr("Autofire Up"), "autofireUp", [this](bool held) {
		if (m_controller) {
			m_controller->setAutofire(GBA_KEY_UP, held);
		}
	}, "autofire");
	m_actions.addHeldAction(tr("Autofire Right"), "autofireRight", [this](bool held) {
		if (m_controller) {
			m_controller->setAutofire(GBA_KEY_RIGHT, held);
		}
	}, "autofire");
	m_actions.addHeldAction(tr("Autofire Down"), "autofireDown", [this](bool held) {
		if (m_controller) {
			m_controller->setAutofire(GBA_KEY_DOWN, held);
		}
	}, "autofire");
	m_actions.addHeldAction(tr("Autofire Left"), "autofireLeft", [this](bool held) {
		if (m_controller) {
			m_controller->setAutofire(GBA_KEY_LEFT, held);
		}
	}, "autofire");

	for (Action* action : m_gameActions) {
		action->setEnabled(false);
	}

	m_shortcutController->rebuildItems();
	m_actions.rebuildMenu(menuBar(), this, *m_shortcutController);
}

void Window::setupOptions() {
	ConfigOption* videoSync = m_config->addOption("videoSync");
	videoSync->connect([this](const QVariant&) {
		reloadConfig();
	}, this);

	ConfigOption* audioSync = m_config->addOption("audioSync");
	audioSync->connect([this](const QVariant&) {
		reloadConfig();
	}, this);

	ConfigOption* skipBios = m_config->addOption("skipBios");
	skipBios->connect([this](const QVariant&) {
		reloadConfig();
	}, this);

	ConfigOption* useBios = m_config->addOption("useBios");
	useBios->connect([this](const QVariant&) {
		reloadConfig();
	}, this);

	ConfigOption* buffers = m_config->addOption("audioBuffers");
	buffers->connect([this](const QVariant&) {
		reloadConfig();
	}, this);

	ConfigOption* sampleRate = m_config->addOption("sampleRate");
	sampleRate->connect([this](const QVariant&) {
		reloadConfig();
	}, this);

	ConfigOption* customAudioKillswitch = m_config->addOption("customAudioKillswitch");
	customAudioKillswitch->connect([this](const QVariant& value) {
		setAudioKillswitch(value.toBool());
	}, this);

	ConfigOption* customNoRenderMode = m_config->addOption("customNoRenderMode");
	customNoRenderMode->connect([this](const QVariant& value) {
		setNoRenderMode(value.toBool());
	}, this);

	ConfigOption* customSavestateCacheEnabled = m_config->addOption("customSavestateCacheEnabled");
	customSavestateCacheEnabled->connect([this](const QVariant&) {
		reloadConfig();
	}, this);

	ConfigOption* customSavestateCacheMaxEntries = m_config->addOption("customSavestateCacheMaxEntries");
	customSavestateCacheMaxEntries->connect([this](const QVariant&) {
		reloadConfig();
	}, this);

	ConfigOption* customSavestateCacheMaxMB = m_config->addOption("customSavestateCacheMaxMB");
	customSavestateCacheMaxMB->connect([this](const QVariant&) {
		reloadConfig();
	}, this);

	ConfigOption* volume = m_config->addOption("volume");
	volume->connect([this](const QVariant&) {
		reloadConfig();
	}, this);

	ConfigOption* volumeFf = m_config->addOption("fastForwardVolume");
	volumeFf->connect([this](const QVariant&) {
		reloadConfig();
	}, this);

	ConfigOption* muteFf = m_config->addOption("fastForwardMute");
	muteFf->connect([this](const QVariant&) {
		reloadConfig();
	}, this);

	if (m_config->getOption("fastForward").isNull()) {
		m_config->setOption("fastForward", true);
	}
	if (m_config->getOption("customSavestateCacheMaxEntries").isNull()) {
		m_config->setOption("customSavestateCacheMaxEntries", 64);
	}
	if (m_config->getOption("customSavestateCacheMaxMB").isNull()) {
		m_config->setOption("customSavestateCacheMaxMB", 4096);
	}
	if (m_config->getOption("customVirtualPadAlwaysOnTop").isNull()) {
		m_config->setOption("customVirtualPadAlwaysOnTop", false);
	}
	if (m_config->getOption("customVirtualPadAutoboot").isNull()) {
		m_config->setOption("customVirtualPadAutoboot", false);
	}
	if (m_config->getOption("customVirtualPadSticky").isNull()) {
		m_config->setOption("customVirtualPadSticky", true);
	}
	if (m_config->getOption("customVirtualPadPressForFrames").isNull()) {
		m_config->setOption("customVirtualPadPressForFrames", false);
	}
	if (m_config->getOption("customVirtualPadPressFrameCount").isNull()) {
		m_config->setOption("customVirtualPadPressFrameCount", 1);
	}
	if (m_config->getOption("customVirtualPadClearAlsoClearsAnalog").isNull()) {
		m_config->setOption("customVirtualPadClearAlsoClearsAnalog", false);
	}

	ConfigOption* rewindEnable = m_config->addOption("rewindEnable");
	rewindEnable->connect([this](const QVariant&) {
		reloadConfig();
	}, this);

	ConfigOption* rewindBufferCapacity = m_config->addOption("rewindBufferCapacity");
	rewindBufferCapacity->connect([this](const QVariant&) {
		reloadConfig();
	}, this);

	ConfigOption* allowOpposingDirections = m_config->addOption("allowOpposingDirections");
	allowOpposingDirections->connect([this](const QVariant&) {
		reloadConfig();
	}, this);

	ConfigOption* saveStateExtdata = m_config->addOption("saveStateExtdata");
	saveStateExtdata->connect([this](const QVariant&) {
		reloadConfig();
	}, this);

	ConfigOption* loadStateExtdata = m_config->addOption("loadStateExtdata");
	loadStateExtdata->connect([this](const QVariant&) {
		reloadConfig();
	}, this);

	ConfigOption* preload = m_config->addOption("preload");
	preload->connect([this](const QVariant& value) {
		m_manager->setPreload(value.toBool());
	}, this);
	m_config->updateOption("preload");

	ConfigOption* showFps = m_config->addOption("showFps");
	showFps->connect([this](const QVariant& value) {
		if (!value.toInt()) {
			m_fpsTimer.stop();
			updateTitle();
		} else if (m_controller) {
			m_fpsTimer.start();
			m_frameTimer.start();
		}
	}, this);

	ConfigOption* showOSD = m_config->addOption("showOSD");
	showOSD->connect([this](const QVariant& value) {
		if (m_display && !value.isNull()) {
			m_display->showOSDMessages(value.toBool());
		}
	}, this);

	ConfigOption* showFrameCounter = m_config->addOption("showFrameCounter");
	showFrameCounter->connect([this](const QVariant& value) {
		if (m_display) {
			m_display->showFrameCounter(value.toBool());
		}
	}, this);

	ConfigOption* showInputDisplay = m_config->addOption("showInputDisplay");
	showInputDisplay->connect([this](const QVariant& value) {
		if (m_display) {
			m_display->showInputDisplay(value.toBool());
		}
	}, this);

	ConfigOption* showResetInfo = m_config->addOption("showResetInfo");
	showResetInfo->connect([this](const QVariant& value) {
		if (m_controller) {
			m_controller->showResetInfo(value.toBool());
		}
	}, this);

	ConfigOption* videoScale = m_config->addOption("videoScale");
	videoScale->connect([this](const QVariant& value) {
		if (m_display) {
			m_display->setVideoScale(value.toInt());
		}
	}, this);

	ConfigOption* dynamicTitle = m_config->addOption("dynamicTitle");
	dynamicTitle->connect([this](const QVariant&) {
		updateTitle();
	}, this);

}

void Window::attachWidget(QWidget* widget) {
	// Fix https://mgba.io/i/2885 -- seems like a Qt bug
	if (m_display && widget != m_display.get()) {
		m_display->hide();
	}
	takeCentralWidget();
	setCentralWidget(widget);
	if (m_display && widget == m_display.get()) {
		m_display->show();
	}
	updateNoRenderOverlay();
}

void Window::detachWidget() {
	m_config->updateOption("showLibrary");
}

void Window::appendMRU(const QString& fname) {
	int index = m_mruFiles.indexOf(fname);
	if (index >= 0) {
		m_mruFiles.removeAt(index);
	}
	m_mruFiles.prepend(fname);
	while (m_mruFiles.size() > ConfigController::MRU_LIST_SIZE) {
		m_mruFiles.removeLast();
	}
	updateMRU();
}

void Window::clearMRU() {
	m_mruFiles.clear();
	updateMRU();
}

void Window::updateMRU() {
	m_actions.clearMenu("mru");
	int i = 0;
	for (const QString& file : m_mruFiles) {
		QString displayName(QDir::toNativeSeparators(file).replace("&", "&&"));
		m_actions.addAction(displayName, QString("mru.%1").arg(QString::number(i)), [this, file]() {
			setController(m_manager->loadGame(file), file);
		}, "mru", QString("Ctrl+%1").arg(i));
		++i;
	}
	m_config->setMRU(m_mruFiles);
	m_config->write();
	m_actions.addSeparator("mru");
	m_actions.addAction(tr("Clear"), "resetMru", this, &Window::clearMRU, "mru");

	m_actions.rebuildMenu(menuBar(), this, *m_shortcutController);
}

Action* Window::addGameAction(const QString& visibleName, const QString& name, Action::Function function, const QString& menu, const QKeySequence& shortcut) {
	Action* action = m_actions.addAction(visibleName, name, [this, function]() {
		if (m_controller) {
			function();
		}
	}, menu, shortcut);
	m_gameActions.append(action);
	return action;
}

template<typename T, typename V>
Action* Window::addGameAction(const QString& visibleName, const QString& name, T* obj, V (T::*method)(), const QString& menu, const QKeySequence& shortcut) {
	return addGameAction(visibleName, name, [obj, method]() {
		(obj->*method)();
	}, menu, shortcut);
}

template<typename V>
Action* Window::addGameAction(const QString& visibleName, const QString& name, V (CoreController::*method)(), const QString& menu, const QKeySequence& shortcut) {
	return addGameAction(visibleName, name, [this, method]() {
		(m_controller.get()->*method)();
	}, menu, shortcut);
}

Action* Window::addGameAction(const QString& visibleName, const QString& name, Action::BooleanFunction function, const QString& menu, const QKeySequence& shortcut) {
	Action* action = m_actions.addBooleanAction(visibleName, name, [this, function](bool value) {
		if (m_controller) {
			function(value);
		}
	}, menu, shortcut);
	m_gameActions.append(action);
	return action;
}

void Window::focusCheck() {
	if (!m_controller) {
		return;
	}
	if (m_config->getOption("pauseOnFocusLost").toInt()) {
		if (QGuiApplication::focusWindow() && m_autoresume) {
			m_controller->setPaused(false);
			m_autoresume = false;
		} else if (!QGuiApplication::focusWindow() && !m_controller->isPaused()) {
			m_autoresume = true;
			m_controller->setPaused(true);
		}
	}
	if (m_config->getOption("muteOnFocusLost").toInt()) {
		if (QGuiApplication::focusWindow()) {
			m_inactiveMute = false;
		} else {
			m_inactiveMute = true;
		}
		updateMute();
	}
}

void Window::updateFrame() {
	if (!m_controller) {
		return;
	}
	QPixmap pixmap;
	pixmap.convertFromImage(m_controller->getPixels());
	m_screenWidget->setPixmap(pixmap);
}

void Window::setController(CoreController* controller, const QString& fname, bool startPaused) {
	if (!controller) {
		return;
	}
	if (m_pendingClose) {
		return;
	}

	if (m_controller) {
		m_controller->stop();
		QTimer::singleShot(0, this, [this, controller, fname, startPaused]() {
			setController(controller, fname, startPaused);
		});
		return;
	}
	if (!fname.isEmpty()) {
		setWindowFilePath(fname);
		appendMRU(fname);
	}

	if (!m_display) {
		reloadDisplayDriver();
	}

	m_controller = std::shared_ptr<CoreController>(controller);
	m_inputController.recalibrateAxes();
	m_controller->setInputController(&m_inputController);
	m_controller->setLogger(&m_log);
	if (m_virtualPad) {
		m_virtualPad->setController(m_controller);
	}
	if (m_inputTapeView) {
		m_inputTapeView->setController(m_controller);
	}
	if (m_spindaProjectView) {
		m_spindaProjectView->setController(m_controller);
	}

	connect(this, &Window::shutdown, [this]() {
		if (!m_controller) {
			return;
		}
		m_controller->stop();
		disconnect(m_controller.get(), &CoreController::started, this, &Window::gameStarted);
	});

	connect(m_controller.get(), &CoreController::started, this, &Window::gameStarted);
	connect(m_controller.get(), &CoreController::started, &m_inputController, &InputController::suspendScreensaver);
	connect(m_controller.get(), &CoreController::stopping, this, &Window::gameStopped);
	{
		// Bind these lambdas to the window so Qt disconnects them automatically
		// during teardown. Runtime Python can swap controllers and close the
		// window while the core is still unwinding; a context-less lambda here
		// can fire after Window destruction and crash the close path.
		connect(m_controller.get(), &CoreController::stopping, this, [this]() {
			m_controller.reset();
		});
	}
	connect(m_controller.get(), &CoreController::stopping, &m_inputController, &InputController::resumeScreensaver);
	connect(m_controller.get(), &CoreController::paused, this, &Window::updateFrame);

#ifndef Q_OS_MAC
	connect(m_controller.get(), &CoreController::paused, menuBar(), &QWidget::show);
	connect(m_controller.get(), &CoreController::unpaused, this, [this]() {
		if(isFullScreen()) {
			menuBar()->hide();
		}
	});
#endif

	connect(m_controller.get(), &CoreController::paused, &m_inputController, &InputController::resumeScreensaver);
	connect(m_controller.get(), &CoreController::paused, this, [this]() {
		emit paused(true);
	});
	connect(m_controller.get(), &CoreController::unpaused, this, [this]() {
		emit paused(false);
	});
	connect(m_controller.get(), &CoreController::unpaused, &m_inputController, &InputController::suspendScreensaver);
	connect(m_controller.get(), &CoreController::frameAvailable, this, &Window::recordFrame);
	connect(m_controller.get(), &CoreController::crashed, this, &Window::gameCrashed);
	connect(m_controller.get(), &CoreController::failed, this, &Window::gameFailed);
	connect(m_controller.get(), &CoreController::unimplementedBiosCall, this, &Window::unimplementedBiosCall);

#ifdef M_CORE_GBA
	if (m_controller->platform() == mPLATFORM_GBA) {
		QVariant mb = m_config->takeArgvOption(QString("mb"));
		if (mb.canConvert(QMetaType::QString)) {
			m_controller->replaceGame(mb.toString());
		}
	}
#endif

#ifdef USE_GDB_STUB
	if (m_gdbController) {
		m_gdbController->setController(m_controller);
	}
#endif

#ifdef USE_DEBUGGERS
	if (m_console) {
		m_console->setController(m_controller);
	}
#endif

#ifdef USE_FFMPEG
	if (m_gifView) {
		m_gifView->setController(m_controller);
	}

	if (m_videoView) {
		m_videoView->setController(m_controller);
	}
#endif

	if (m_sensorView) {
		m_sensorView->setController(m_controller);
	}

	if (m_overrideView) {
		m_overrideView->setController(m_controller);
	}

	if (!m_pendingPatch.isEmpty()) {
		m_controller->loadPatch(m_pendingPatch);
		m_pendingPatch = QString();
	}

#ifdef ENABLE_SCRIPTING
	if (!m_pendingScript.isEmpty() && !m_scripting) {
		// Startup scripts need the visible controller, but binding after the
		// core is already running can block while waiting for a core interrupt.
		// Create and bind the shared scripting controller before start(); the
		// core thread then starts with the script context already installed.
		m_scripting = new ScriptingController(this);
		connect(m_scripting, &QObject::destroyed, this, [this]() {
			m_scripting = nullptr;
		});
	}
	if (m_scripting) {
		m_scripting->setController(m_controller);
	}
#endif

	const bool startupScriptPending = !m_pendingScript.isEmpty();
	attachDisplay();
	m_controller->loadConfig(m_config);
	m_config->updateOption("showOSD");
	m_config->updateOption("showFrameCounter");
	m_config->updateOption("showInputDisplay");
	m_config->updateOption("showResetInfo");
	m_controller->start(startPaused || startupScriptPending);

	if (!m_pendingState.isEmpty()) {
		m_controller->loadState(m_pendingState);
		m_pendingState = QString();
	}

	if (m_pendingPause) {
		m_controller->setPaused(true);
		m_pendingPause = false;
	}
}

bool Window::loadScriptGame(const QString& path) {
	QFileInfo info(path);
	QString resolved = info.canonicalFilePath();
	if (resolved.isEmpty()) {
		resolved = info.absoluteFilePath();
	}
	if (!QFileInfo(resolved).isReadable()) {
		LOG(QT, ERROR) << tr("Failed to open game file: %1").arg(path);
		return false;
	}

	if (m_controller) {
		// Runtime Python scripts need to be able to swap to a known ROM without
		// tearing down the whole window. Reuse the active controller when
		// possible so the scripting bridge keeps talking to the visible core.
		m_controller->replaceGame(resolved);
		setWindowFilePath(resolved);
		appendMRU(resolved);
		return true;
	}

	// If the scripting window was opened before any ROM was loaded, bootstrap a
	// normal visible game session here so the rest of the Python bridge can keep
	// working against the same menu-bar Qt window.
	CoreController* controller = m_manager->loadGame(resolved);
	if (!controller) {
		return false;
	}
	// A script-opened first ROM has no existing controller to interrupt before
	// start(). Start it paused at the reset boundary so Python owns the first
	// real emulated frame after it loads the save/state it wants to test.
	setController(controller, resolved, true);
	return true;
}

void Window::attachDisplay() {
	m_display->attach(m_controller);
	connect(m_display.get(), &QGBA::Display::drawingStarted, this, &Window::changeRenderer);
	m_display->startDrawing(m_controller);
}

void Window::updateMute() {
	if (!m_controller) {
		return;
	}

	bool mute = m_inactiveMute;

	if (!mute) {
		QString multiplayerAudio = m_config->getQtOption("multiplayerAudio").toString();
		if (multiplayerAudio == QLatin1String("p1")) {
			MultiplayerController* multiplayer = m_controller->multiplayerController();
			mute = multiplayer && multiplayer->attached() > 1 && m_playerId;
		} else if (multiplayerAudio == QLatin1String("active")) {
			mute = !m_multiActive;
		}
	}

	m_controller->overrideMute(mute);
}

void Window::setAudioKillswitch(bool enable) {
	if (m_config->getOption("customAudioKillswitch").toInt() != enable) {
		m_config->setOption("customAudioKillswitch", enable);
		return;
	}
	if (!m_controller) {
		return;
	}

	m_controller->setAudioKillswitch(enable);
	updateMute();
	reloadAudioDriver();
	if (m_audioKillswitchAction) {
		m_audioKillswitchAction->setActive(enable);
	}
}

void Window::setNoRenderMode(bool enable) {
	if (m_config->getOption("customNoRenderMode").toInt() != enable) {
		m_config->setOption("customNoRenderMode", enable);
		return;
	}
	if (m_controller) {
		m_controller->setNoRenderMode(enable);
	}
	if (m_noRenderModeAction) {
		m_noRenderModeAction->setActive(enable);
	}
	syncCustomFeatureState();
}

void Window::syncCustomFeatureState() {
	const bool noRenderEnabled = m_controller && m_controller->noRenderModeEnabled();
	if (m_display) {
		m_display->setPresentationEnabled(!noRenderEnabled);
		if (!noRenderEnabled) {
			m_display->forceDraw();
		}
	}
	updateNoRenderOverlay();

	if (m_audioKillswitchAction && m_controller) {
		m_audioKillswitchAction->setActive(m_controller->audioKillswitchEnabled());
	}
	if (m_noRenderModeAction) {
		m_noRenderModeAction->setActive(noRenderEnabled);
	}
}

void Window::applyVirtualPadSettings() {
	if (!m_virtualPad) {
		return;
	}

	m_virtualPad->setSettings(customVirtualPadSettings(m_config));
}

void Window::ensureNoRenderOverlay() {
	if (!m_display || m_noRenderOverlay) {
		return;
	}

	m_noRenderOverlay = new QWidget(m_display.get());
	m_noRenderOverlay->setObjectName(QStringLiteral("customNoRenderOverlay"));
	m_noRenderOverlay->setStyleSheet(QStringLiteral(
		"QWidget#customNoRenderOverlay { background-color: #000000; }"
		"QPushButton { padding: 12px 20px; font-weight: 600; }"));
	m_noRenderOverlay->hide();
	connect(m_noRenderOverlay, &QObject::destroyed, this, [this]() {
		// The overlay is parented to the active display widget. When the display
		// is rebuilt or the window closes, clear the raw pointers immediately so
		// the next display does not inherit stale QWidget addresses.
		m_noRenderOverlay = nullptr;
		m_noRenderButton = nullptr;
	});

	QVBoxLayout* layout = new QVBoxLayout(m_noRenderOverlay);
	layout->setContentsMargins(24, 24, 24, 24);
	layout->addStretch();
	m_noRenderButton = new QPushButton(tr("Re-enable"), m_noRenderOverlay);
	m_noRenderButton->setSizePolicy(QSizePolicy::Fixed, QSizePolicy::Fixed);
	layout->addWidget(m_noRenderButton, 0, Qt::AlignCenter);
	layout->addStretch();
	connect(m_noRenderButton, &QPushButton::clicked, this, [this]() {
		setNoRenderMode(false);
	});
}

void Window::clearNoRenderOverlayState() {
	m_noRenderButton = nullptr;
	m_noRenderOverlay = nullptr;
}

void Window::updateNoRenderOverlay() {
	if (!m_display) {
		return;
	}
	ensureNoRenderOverlay();
	if (!m_noRenderOverlay) {
		return;
	}

	m_noRenderOverlay->setGeometry(m_display->rect());
	const bool shouldShow = m_controller && m_controller->noRenderModeEnabled() && centralWidget() == m_display.get();
	m_noRenderOverlay->setVisible(shouldShow);
	if (shouldShow) {
		m_noRenderOverlay->raise();
	}
}

void Window::setLogo() {
	m_screenWidget->setPixmap(m_logo);
	m_screenWidget->setDimensions(m_logo.width(), m_logo.height());
	centralWidget()->unsetCursor();
}

WindowBackground::WindowBackground(QWidget* parent)
	: QWidget(parent)
{
}

void WindowBackground::setPixmap(const QPixmap& pmap) {
	m_pixmap = pmap;
	update();
}

void WindowBackground::setSizeHint(const QSize& hint) {
	m_sizeHint = hint;
}

QSize WindowBackground::sizeHint() const {
	return m_sizeHint;
}

void WindowBackground::setDimensions(int width, int height) {
	m_aspectWidth = width;
	m_aspectHeight = height;
}

void WindowBackground::paintEvent(QPaintEvent* event) {
	QWidget::paintEvent(event);
	const QPixmap& logo = pixmap();
	QPainter painter(this);
	painter.setRenderHint(QPainter::SmoothPixmapTransform, true);
	painter.fillRect(QRect(QPoint(), size()), Qt::black);
	QRect full(clampSize(QSize(m_aspectWidth, m_aspectHeight), size(), true, false));
	painter.drawPixmap(full, logo);
}
