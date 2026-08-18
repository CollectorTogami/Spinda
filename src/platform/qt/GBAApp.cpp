/* Copyright (c) 2013-2014 Jeffrey Pfau
 *
 * Original mGBA source is credited to Jeffrey Pfau and contributors.
 * Local custom modifications in this fork were added for this workspace and
 * are not upstream mGBA work or authored by Jeffrey Pfau.
 *
 * This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */
#include "GBAApp.h"

#include "AudioProcessor.h"
#include "CoreController.h"
#include "CoreManager.h"
#include "ConfigController.h"
#include "Display.h"
#include "LogController.h"
#include "VFileDevice.h"
#include "Window.h"

#include <QColor>
#include <QFileInfo>
#include <QFileOpenEvent>
#include <QFontDatabase>
#include <QIcon>
#include <QStyleFactory>

#include <mgba/feature/updater.h>
#include <mgba-util/socket.h>
#include <mgba-util/vfs.h>

#ifdef USE_SQLITE3
#include "feature/sqlite3/no-intro.h"
#endif

#ifdef USE_DISCORD_RPC
#include "DiscordCoordinator.h"
#endif

using namespace QGBA;

static GBAApp* g_app = nullptr;

namespace {

QPalette darkPalette() {
	// Keep the dark palette centralized so the new Interface checkbox and early
	// app startup use the exact same colors.
	QPalette palette;
	const QColor window(45, 45, 48);
	const QColor base(30, 30, 30);
	const QColor altBase(37, 37, 38);
	const QColor text(240, 240, 240);
	const QColor disabledText(127, 127, 127);
	const QColor button(53, 53, 53);
	const QColor highlight(42, 130, 218);
	const QColor highlightedText(255, 255, 255);

	palette.setColor(QPalette::Window, window);
	palette.setColor(QPalette::WindowText, text);
	palette.setColor(QPalette::Base, base);
	palette.setColor(QPalette::AlternateBase, altBase);
	palette.setColor(QPalette::ToolTipBase, altBase);
	palette.setColor(QPalette::ToolTipText, text);
	palette.setColor(QPalette::Text, text);
	palette.setColor(QPalette::Button, button);
	palette.setColor(QPalette::ButtonText, text);
	palette.setColor(QPalette::BrightText, Qt::red);
	palette.setColor(QPalette::Link, highlight);
	palette.setColor(QPalette::Highlight, highlight);
	palette.setColor(QPalette::HighlightedText, highlightedText);
	palette.setColor(QPalette::Light, button.lighter(130));
	palette.setColor(QPalette::Midlight, button.lighter(115));
	palette.setColor(QPalette::Mid, button.darker(125));
	palette.setColor(QPalette::Dark, base.darker(150));
	palette.setColor(QPalette::Shadow, Qt::black);

	palette.setColor(QPalette::Disabled, QPalette::WindowText, disabledText);
	palette.setColor(QPalette::Disabled, QPalette::Text, disabledText);
	palette.setColor(QPalette::Disabled, QPalette::ButtonText, disabledText);
	palette.setColor(QPalette::Disabled, QPalette::Highlight, QColor(80, 80, 80));
	palette.setColor(QPalette::Disabled, QPalette::HighlightedText, disabledText);

	return palette;
}

QString darkStyleSheet() {
	// Fusion + palette gets most of the app dark, but Windows-native dialogs and
	// a few container widgets still keep light surfaces. Keep those selectors in
	// one place so the Interface toggle applies a consistent dark look everywhere.
	return QStringLiteral(R"(
QToolTip {
	color: #f0f0f0;
	background-color: #2d2d30;
	border: 1px solid #5a5a5a;
}
QMenuBar {
	background-color: #2d2d30;
	color: #f0f0f0;
}
QMenuBar::item {
	background: transparent;
	padding: 4px 8px;
}
QMenuBar::item:selected {
	background-color: #3a3d41;
}
QMenu {
	background-color: #2d2d30;
	color: #f0f0f0;
	border: 1px solid #5a5a5a;
}
QMenu::item:selected {
	background-color: #2a82da;
	color: #ffffff;
}
QMenu::separator {
	height: 1px;
	background-color: #5a5a5a;
	margin: 4px 8px;
}
QTabWidget::pane {
	border: 1px solid #5a5a5a;
	background-color: #2d2d30;
}
QTabBar::tab {
	background-color: #353535;
	color: #f0f0f0;
	border: 1px solid #5a5a5a;
	border-bottom: none;
	padding: 6px 10px;
}
QTabBar::tab:selected {
	background-color: #2d2d30;
}
QHeaderView::section {
	background-color: #353535;
	color: #f0f0f0;
	border: 1px solid #4a4a4a;
	padding: 4px;
}
QListView,
QTreeView,
QTableView {
	background-color: #1e1e1e;
	color: #f0f0f0;
	alternate-background-color: #252526;
	border: 1px solid #5a5a5a;
}
QListView::item:selected,
QTreeView::item:selected,
QTableView::item:selected {
	background-color: #2a82da;
	color: #ffffff;
}
QComboBox QAbstractItemView {
	background-color: #1e1e1e;
	color: #f0f0f0;
	selection-background-color: #2a82da;
	selection-color: #ffffff;
}
QAbstractSpinBox,
QLineEdit,
QTextEdit,
QPlainTextEdit {
	background-color: #1e1e1e;
	color: #f0f0f0;
	border: 1px solid #5a5a5a;
	selection-background-color: #2a82da;
	selection-color: #ffffff;
}
)");
}

QFileDialog::Options fileDialogOptions() {
	// Prefer the native Windows Explorer dialogs for file picking. They do not
	// follow the custom Qt dark palette perfectly, but they match the rest of
	// the OS and avoid the larger custom Qt picker UI.
	return QFileDialog::Options();
}

}

mLOG_DEFINE_CATEGORY(QT, "Qt", "platform.qt");

GBAApp::GBAApp(int& argc, char* argv[], ConfigController* config)
	: QApplication(argc, argv)
	, m_configController(config)
	, m_updater(config)
	, m_monospace(QFontDatabase::systemFont(QFontDatabase::FixedFont))
{
	g_app = this;
	// Remember the light theme state before applying the custom dark mode so the
	// user can toggle back without restarting.
	m_lightPalette = palette();
	m_lightStyleName = style()->objectName();
	setDarkMode(m_configController->getQtOption("darkMode").toBool());

#ifdef BUILD_SDL
	SDL_Init(SDL_INIT_NOPARACHUTE);
#endif

	SocketSubsystemInit();
	qRegisterMetaType<const uint32_t*>("const uint32_t*");
	qRegisterMetaType<mCoreThread*>("mCoreThread*");

	if (!m_configController->getQtOption("displayDriver").isNull()) {
		Display::setDriver(static_cast<Display::Driver>(m_configController->getQtOption("displayDriver").toInt()));
	}

	reloadGameDB();

	m_manager.setConfig(m_configController->config());
	m_manager.setMultiplayerController(&m_multiplayer);

	if (!m_configController->getQtOption("audioDriver").isNull()) {
		AudioProcessor::setDriver(static_cast<AudioProcessor::Driver>(m_configController->getQtOption("audioDriver").toInt()));
	}

	LogController::global()->load(m_configController);

#ifdef USE_DISCORD_RPC
	ConfigOption* useDiscordPresence = m_configController->addOption("useDiscordPresence");
	useDiscordPresence->addBoolean(tr("Enable Discord Rich Presence"));
	useDiscordPresence->connect([](const QVariant& value) {
		if (value.toBool()) {
			DiscordCoordinator::init();
		} else {
			DiscordCoordinator::deinit();
		}
	}, this);
	m_configController->updateOption("useDiscordPresence");
#endif

	cleanupAfterUpdate();

	connect(this, &GBAApp::aboutToQuit, this, &GBAApp::cleanup);
	if (m_configController->getOption("updateAutoCheck", 0).toInt()) {
		QMetaObject::invokeMethod(&m_updater, "checkUpdate", Qt::QueuedConnection);
	}
}

void GBAApp::setDarkMode(bool enable) {
	m_darkMode = enable;
	if (enable) {
		// Fusion gives predictable widget colors across platforms, which makes
		// the custom dark palette look the same in the local Qt build.
		setStyle(QStyleFactory::create(QStringLiteral("Fusion")));
		setPalette(darkPalette());
		setStyleSheet(darkStyleSheet());
	} else {
		if (!m_lightStyleName.isEmpty()) {
			QStyle* style = QStyleFactory::create(m_lightStyleName);
			if (style) {
				setStyle(style);
			}
		}
		setPalette(m_lightPalette);
		setStyleSheet(QString());
	}

	for (Window* window : m_windows) {
		if (window) {
			window->syncThemeChrome();
		}
	}
}

void GBAApp::cleanup() {
	m_workerThreads.waitForDone();

	while (!m_workerJobs.isEmpty()) {
		finishJob(m_workerJobs.firstKey());
	}

#ifdef USE_SQLITE3
	if (m_db) {
		NoIntroDBDestroy(m_db);
	}
#endif

#ifdef USE_DISCORD_RPC
	DiscordCoordinator::deinit();
#endif
}

bool GBAApp::event(QEvent* event) {
	if (event->type() == QEvent::FileOpen) {
		CoreController* core = m_manager.loadGame(static_cast<QFileOpenEvent*>(event)->file());
		m_windows[0]->setController(core, static_cast<QFileOpenEvent*>(event)->file());
		return true;
	}
	return QApplication::event(event);
}

Window* GBAApp::newWindow() {
	if (m_windows.count() >= MAX_GBAS) {
		return nullptr;
	}
	Window* w = new Window(&m_manager, m_configController, m_windows.count());
	connect(w, &Window::destroyed, [this, w]() {
		m_windows.removeAll(w);
		for (Window* w : m_windows) {
			w->updateMultiplayerStatus(m_windows.count() < MAX_GBAS);
		}
	});
	m_windows.append(w);
	w->setAttribute(Qt::WA_DeleteOnClose);
	w->loadConfig();
	w->show();
	w->multiplayerChanged();
	for (Window* w : m_windows) {
		w->updateMultiplayerStatus(m_windows.count() < MAX_GBAS);
	}
	return w;
}

GBAApp* GBAApp::app() {
	return g_app;
}

void GBAApp::pauseAll(QList<Window*>* paused) {
	for (auto& window : m_windows) {
		if (!window->controller() || window->controller()->isPaused()) {
			continue;
		}
		window->controller()->setPaused(true);
		paused->append(window);
	}
}

void GBAApp::continueAll(const QList<Window*>& paused) {
	for (auto& window : paused) {
		if (window->controller()) {
			window->controller()->setPaused(false);
		}
	}
}

QString GBAApp::getOpenFileName(QWidget* owner, const QString& title, const QString& filter, const QString& path) {
	QList<Window*> paused;
	pauseAll(&paused);
	const QString startPath = path.isEmpty() ? m_configController->getOption("lastDirectory") : path;
	QString filename = QFileDialog::getOpenFileName(owner, title, startPath, filter, nullptr, fileDialogOptions());
	continueAll(paused);
	if (!filename.isEmpty()) {
		m_configController->setOption("lastDirectory", QFileInfo(filename).dir().canonicalPath());
	}
	return filename;
}

QStringList GBAApp::getOpenFileNames(QWidget* owner, const QString& title, const QString& filter) {
	QList<Window*> paused;
	pauseAll(&paused);
	QStringList filenames = QFileDialog::getOpenFileNames(owner, title, m_configController->getOption("lastDirectory"), filter, nullptr, fileDialogOptions());
	continueAll(paused);
	if (!filenames.isEmpty()) {
		m_configController->setOption("lastDirectory", QFileInfo(filenames.at(0)).dir().canonicalPath());
	}
	return filenames;
}

QString GBAApp::getSaveFileName(QWidget* owner, const QString& title, const QString& filter, const QString& path) {
	QList<Window*> paused;
	pauseAll(&paused);
	const QString startPath = path.isEmpty() ? m_configController->getOption("lastDirectory") : path;
	QString filename = QFileDialog::getSaveFileName(owner, title, startPath, filter, nullptr, fileDialogOptions());
	continueAll(paused);
	if (!filename.isEmpty()) {
		m_configController->setOption("lastDirectory", QFileInfo(filename).dir().canonicalPath());
	}
	return filename;
}

QString GBAApp::getOpenDirectoryName(QWidget* owner, const QString& title, const QString& path) {
	QList<Window*> paused;
	pauseAll(&paused);
	QString filename = QFileDialog::getExistingDirectory(owner, title, !path.isNull() ? path : m_configController->getOption("lastDirectory"), fileDialogOptions());
	continueAll(paused);
	if (path.isNull() && !filename.isEmpty()) {
		m_configController->setOption("lastDirectory", QFileInfo(filename).dir().canonicalPath());
	}
	return filename;
}

QString GBAApp::dataDir() {
#ifdef DATADIR
	QString path = QString::fromUtf8(DATADIR);
	if (path.startsWith("./") || path.startsWith("../")) {
		path = QCoreApplication::applicationDirPath() + "/" + path;
	}
#else
	QString path = QCoreApplication::applicationDirPath();
#ifdef Q_OS_MAC
	path += QLatin1String("/../Resources");
#endif
#endif
	return path;
}

#ifdef USE_SQLITE3
bool GBAApp::reloadGameDB() {
	NoIntroDB* db = nullptr;
	db = NoIntroDBLoad((ConfigController::configDir() + "/nointro.sqlite3").toUtf8().constData());
	if (db && m_db) {
		NoIntroDBDestroy(m_db);
	}
	if (db) {
		std::shared_ptr<GameDBParser> parser = std::make_shared<GameDBParser>(db);
		submitWorkerJob(std::bind(&GameDBParser::parseNoIntroDB, parser));
		m_db = db;
		return true;
	}
	return false;
}
#else
bool GBAApp::reloadGameDB() {
	return false;
}
#endif

qint64 GBAApp::submitWorkerJob(std::function<void ()> job, std::function<void ()> callback) {
	return submitWorkerJob(job, nullptr, callback);
}

qint64 GBAApp::submitWorkerJob(std::function<void ()> job, QObject* context, std::function<void ()> callback) {
	qint64 jobId = m_nextJob;
	++m_nextJob;
	WorkerJob* jobRunnable = new WorkerJob(jobId, job, this);
	m_workerJobs.insert(jobId, jobRunnable);
	if (callback) {
		waitOnJob(jobId, context, callback);
	}
	m_workerThreads.start(jobRunnable);
	return jobId;
}

bool GBAApp::removeWorkerJob(qint64 jobId) {
	for (auto& job : m_workerJobCallbacks.values(jobId)) {
		disconnect(job);
	}
	m_workerJobCallbacks.remove(jobId);
	if (!m_workerJobs.contains(jobId)) {
		return true;
	}
	bool success = false;
#if (QT_VERSION >= QT_VERSION_CHECK(5, 9, 0))
	success = m_workerThreads.tryTake(m_workerJobs[jobId]);
#endif
	if (success) {
		m_workerJobs.remove(jobId);
	}
	return success;
}

bool GBAApp::waitOnJob(qint64 jobId, QObject* context, std::function<void ()> callback) {
	if (!m_workerJobs.contains(jobId)) {
		return false;
	}
	if (!context) {
		context = this;
	}
	QMetaObject::Connection connection = connect(this, &GBAApp::jobFinished, context, [jobId, callback](qint64 testedJobId) {
		if (jobId != testedJobId) {
			return;
		}
		callback();
	});
	m_workerJobCallbacks.insert(m_nextJob, connection);
	return true;
}

void GBAApp::cleanupAfterUpdate() {
	// Remove leftover updater if there's one present
	QDir configDir(ConfigController::configDir());
	QString extractedPath = configDir.filePath(QLatin1String("updater"));
#ifdef Q_OS_WIN
	extractedPath += ".exe";
#endif
	QFile updater(extractedPath);
	if (updater.exists()) {
		updater.remove();
	}

#ifdef Q_OS_WIN
	// Remove the installer exe if we downloaded that too
	extractedPath = configDir.filePath(QLatin1String("update.exe"));
	QFile update(extractedPath);
	if (update.exists()) {
		update.remove();
	}
#endif
}

void GBAApp::restartForUpdate() {
	QFileInfo updaterPath(m_updater.updateInfo().url.path());
	QDir configDir(ConfigController::configDir());
	if (updaterPath.suffix() == "exe") {
		m_invokeOnExit = configDir.filePath(QLatin1String("update.exe"));
	} else {
		QFile updater(":/updater");
		QString extractedPath = configDir.filePath(QLatin1String("updater"));
	#ifdef Q_OS_WIN
		extractedPath += ".exe";
	#endif
		updater.copy(extractedPath);
	#ifndef Q_OS_WIN
		QFile(extractedPath).setPermissions(QFileDevice::ReadOwner | QFileDevice::WriteOwner | QFileDevice::ExeOwner);
	#endif
		m_invokeOnExit = extractedPath;
	}

	for (auto& window : m_windows) {
		window->deleteLater();
	}
	QMetaObject::invokeMethod(this, "quit", Qt::QueuedConnection);
}

void GBAApp::finishJob(qint64 jobId) {
	m_workerJobs.remove(jobId);
	emit jobFinished(jobId);
	m_workerJobCallbacks.remove(jobId);
}

GBAApp::WorkerJob::WorkerJob(qint64 id, std::function<void ()> job, GBAApp* owner)
	: m_id(id)
	, m_job(job)
	, m_owner(owner)
{
	setAutoDelete(true);
}

void GBAApp::WorkerJob::run() {
	m_job();
	QMetaObject::invokeMethod(m_owner, "finishJob", Q_ARG(qint64, m_id));
}

#ifdef USE_SQLITE3
GameDBParser::GameDBParser(NoIntroDB* db, QObject* parent)
	: QObject(parent)
	, m_db(db)
{
	// Nothing to do
}

void GameDBParser::parseNoIntroDB() {
	VFile* vf = VFileDevice::open(GBAApp::dataDir() + "/nointro.dat", O_RDONLY);
	if (vf) {
		NoIntroDBLoadClrMamePro(m_db, vf);
		vf->close(vf);
	}
}

#endif
