/* Copyright (c) 2013-2015 Jeffrey Pfau
 *
 * Original mGBA source is credited to Jeffrey Pfau and contributors.
 * Local custom modifications in this fork were added for this workspace and
 * are not upstream mGBA work or authored by Jeffrey Pfau.
 *
 * This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */
#include "ConfigController.h"

#include "ActionMapper.h"
#include "CoreController.h"

#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QMenu>
#include <QString>

#include <mgba/feature/commandline.h>
#ifdef M_CORE_GB
#include <mgba/internal/gb/overrides.h>
#endif

static const mOption s_frontendOptions[] = {
	{ "ecard", true, '\0' },
	{ "mb", true, '\0' },
	// Local workspace addition: queue one startup script for the visible Qt
	// window after the main UI and core have finished coming up.
	{ "script", true, '\0' },
	{ 0 }
};

using namespace QGBA;

namespace {

constexpr const char* WORKER_ENV_NAME = "MGBA_WORKER_INSTANCE";

QString rawConfigDir() {
	char path[PATH_MAX];
	mCoreConfigDirectory(path, sizeof(path));
	return QDir::cleanPath(QString::fromUtf8(path));
}

QString sanitizeWorkerName(const QString& workerName) {
	QString cleaned;
	for (const QChar& ch : workerName.trimmed()) {
		if (ch.isLetterOrNumber() || ch == QLatin1Char('_') || ch == QLatin1Char('-')) {
			cleaned.append(ch);
		} else if (ch.isSpace() || ch == QLatin1Char('.') || ch == QLatin1Char('/') || ch == QLatin1Char('\\')) {
			cleaned.append(QLatin1Char('_'));
		}
	}
	cleaned = cleaned.left(64);
	while (cleaned.contains(QStringLiteral("__"))) {
		cleaned.replace(QStringLiteral("__"), QStringLiteral("_"));
	}
	if (cleaned == QLatin1String("_")) {
		cleaned.clear();
	}
	return cleaned;
}

QString workerInstanceName() {
	return sanitizeWorkerName(QString::fromLocal8Bit(qgetenv(WORKER_ENV_NAME)));
}

bool shouldRedirectMaintainedPortableConfig() {
	if (!ConfigController::isPortable()) {
		return false;
	}
	QDir configRoot(rawConfigDir());
	return configRoot.dirName().startsWith(QStringLiteral("build-"));
}

QString portableWorkspaceRoot() {
	QDir configRoot(rawConfigDir());
	// The maintained workspace build runs in portable mode out of
	// build-mingw64-python-qt. Keep the mutable runtime data under the project
	// root instead of the build output so a rebuild/clean does not wipe active
	// game progress or frontend settings.
	if (configRoot.dirName().startsWith(QStringLiteral("build-"))) {
		configRoot.cdUp();
	}
	return configRoot.absolutePath();
}

QString portableDataPath(const QString& leaf) {
	QDir configRoot(portableWorkspaceRoot());
	return QDir::cleanPath(configRoot.filePath(QStringLiteral("userdata/%1").arg(leaf)));
}

QString portableWorkerDataPath(const QString& workerName, const QString& leaf) {
	return portableDataPath(QStringLiteral("workers/%1/%2").arg(workerName, leaf));
}

QString configFilePath(const QString& fileName) {
	return QDir(ConfigController::configDir()).filePath(fileName);
}

void migratePortableConfigFileIfNeeded(const QString& fileName) {
	if (!shouldRedirectMaintainedPortableConfig()) {
		return;
	}

	const QString sourcePath = QDir(rawConfigDir()).filePath(fileName);
	const QString destPath = configFilePath(fileName);
	if (QFileInfo::exists(destPath) || !QFileInfo::exists(sourcePath)) {
		return;
	}

	QDir().mkpath(QFileInfo(destPath).absolutePath());
	QFile::copy(sourcePath, destPath);
}

void copyDirectoryContentsIfNeeded(const QString& sourcePath, const QString& destPath) {
	QDir source(sourcePath);
	if (!source.exists()) {
		return;
	}

	QDir().mkpath(destPath);
	const QFileInfoList entries = source.entryInfoList(QDir::NoDotAndDotDot | QDir::AllEntries | QDir::Hidden | QDir::System);
	for (const QFileInfo& entry : entries) {
		const QString destEntryPath = QDir(destPath).filePath(entry.fileName());
		if (entry.isDir()) {
			copyDirectoryContentsIfNeeded(entry.absoluteFilePath(), destEntryPath);
			continue;
		}
		if (!QFileInfo::exists(destEntryPath)) {
			// Migration is one-way and non-destructive: keep any newer userdata
			// created by the redirected build instead of replacing it.
			QDir().mkpath(QFileInfo(destEntryPath).absolutePath());
			QFile::copy(entry.absoluteFilePath(), destEntryPath);
		}
	}
}

void migratePortableDataDirectoryIfNeeded(const QString& directoryName) {
	if (!shouldRedirectMaintainedPortableConfig()) {
		return;
	}

	const QString sourcePath = QDir(rawConfigDir()).filePath(directoryName);
	const QString destPath = portableDataPath(directoryName);
	if (QDir::cleanPath(sourcePath) == QDir::cleanPath(destPath)) {
		return;
	}
	copyDirectoryContentsIfNeeded(sourcePath, destPath);
}

}

ConfigOption::ConfigOption(const QString& name, QObject* parent)
	: QObject(parent)
	, m_name(name)
{
}

void ConfigOption::connect(std::function<void(const QVariant&)> slot, QObject* parent) {
	m_slots[parent] = slot;
	QObject::connect(parent, &QObject::destroyed, this, [this, parent]() {
		m_slots.remove(parent);
	});
}

Action* ConfigOption::addValue(const QString& text, const QVariant& value, ActionMapper* actions, const QString& menu) {
	Action* action;
	auto function = [this, value]() {
		emit valueChanged(value);
	};
	QString name = QString("%1.%2").arg(m_name).arg(value.toString());
	if (actions) {
		action = actions->addAction(text, name, function, menu);
	} else {
		action = new Action(function, name, text, this);
	}
	action->setExclusive();
	QObject::connect(action, &QObject::destroyed, this, [this, action, value]() {
		m_actions.removeAll(std::make_pair(action, value));
	});
	m_actions.append(std::make_pair(action, value));
	return action;
}

Action* ConfigOption::addValue(const QString& text, const char* value, ActionMapper* actions, const QString& menu) {
	return addValue(text, QString(value), actions, menu);
}

Action* ConfigOption::addBoolean(const QString& text, ActionMapper* actions, const QString& menu) {
	Action* action;
	auto function = [this](bool value) {
		emit valueChanged(value);
	};
	if (actions) {
		action = actions->addBooleanAction(text, m_name, function, menu);
	} else {
		action = new Action(function, m_name, text, this);
	}

	QObject::connect(action, &QObject::destroyed, this, [this, action]() {
		m_actions.removeAll(std::make_pair(action, QVariant(1)));
	});
	m_actions.append(std::make_pair(action, QVariant(1)));

	return action;
}

void ConfigOption::setValue(bool value) {
	setValue(QVariant(value));
}

void ConfigOption::setValue(int value) {
	setValue(QVariant(value));
}

void ConfigOption::setValue(unsigned value) {
	setValue(QVariant(value));
}

void ConfigOption::setValue(const char* value) {
	setValue(QVariant(QString(value)));
}

void ConfigOption::setValue(const QVariant& value) {
	for (std::pair<Action*, QVariant>& action : m_actions) {
		action.first->setActive(value == action.second);
	}
	for (std::function<void(const QVariant&)>& slot : m_slots.values()) {
		slot(value);
	}
}

QString ConfigController::s_configDir;

ConfigController::ConfigController(QObject* parent)
	: QObject(parent)
{
	const QString configRoot = configDir();
	QDir().mkpath(configRoot);
	const QString workerName = workerInstanceName();
	if (workerName.isEmpty()) {
		migratePortableConfigFileIfNeeded(QStringLiteral("qt.ini"));
		migratePortableConfigFileIfNeeded(QStringLiteral("config.ini"));
		migratePortableDataDirectoryIfNeeded(QStringLiteral("savegames"));
		migratePortableDataDirectoryIfNeeded(QStringLiteral("savestates"));
	} else {
		QDir().mkpath(portableWorkerDataPath(workerName, QStringLiteral("savegames")));
		QDir().mkpath(portableWorkerDataPath(workerName, QStringLiteral("savestates")));
	}

	QString fileName = configFilePath(QStringLiteral("qt.ini"));
	m_settings = std::make_unique<QSettings>(fileName, QSettings::IniFormat);

	mCoreConfigInit(&m_config, PORT);

	m_opts.audioSync = CoreController::AUDIO_SYNC;
	m_opts.videoSync = CoreController::VIDEO_SYNC;
	m_opts.fpsTarget = 60;
	m_opts.audioBuffers = 1536;
	m_opts.sampleRate = 44100;
	m_opts.volume = 0x100;
	m_opts.logLevel = mLOG_WARN | mLOG_ERROR | mLOG_FATAL;
	m_opts.rewindEnable = false;
	m_opts.rewindBufferCapacity = 300;
	m_opts.useBios = true;
	m_opts.suspendScreensaver = true;
	m_opts.lockAspectRatio = true;
	m_opts.interframeBlending = false;
	const QByteArray configIniPath = configFilePath(QStringLiteral("config.ini")).toUtf8();
	mCoreConfigLoadPath(&m_config, configIniPath.constData());
	mCoreConfigLoadDefaults(&m_config, &m_opts);
	if (isPortable()) {
		const QByteArray savePath = (workerName.isEmpty()
		    ? portableDataPath(QStringLiteral("savegames"))
		    : portableWorkerDataPath(workerName, QStringLiteral("savegames"))).toUtf8();
		const QByteArray statePath = (workerName.isEmpty()
		    ? portableDataPath(QStringLiteral("savestates"))
		    : portableWorkerDataPath(workerName, QStringLiteral("savestates"))).toUtf8();
		QDir().mkpath(QString::fromUtf8(savePath));
		QDir().mkpath(QString::fromUtf8(statePath));
		mCoreConfigSetDefaultValue(&m_config, "savegamePath", savePath.constData());
		mCoreConfigSetDefaultValue(&m_config, "savestatePath", statePath.constData());
	}
#ifdef M_CORE_GB
	mCoreConfigSetDefaultIntValue(&m_config, "sgb.borders", 1);
	mCoreConfigSetDefaultIntValue(&m_config, "gb.colors", GB_COLORS_CGB);
#endif
	mCoreConfigMap(&m_config, &m_opts);

	mSubParserGraphicsInit(&m_subparsers[0], &m_graphicsOpts);

	m_subparsers[1].usage = "Frontend options:\n"
	    "  --ecard FILE  Scan an e-Reader card in the first loaded game\n"
	    "                Can be passed multiple times for multiple cards\n"
	    "  --mb FILE     Boot a multiboot image with FILE inserted into the ROM slot\n"
	    "  --script FILE Load a scripting file after the main window becomes visible";
	m_subparsers[1].parse = nullptr;
	m_subparsers[1].parseLong = [](struct mSubParser* parser, const char* option, const char* arg) {
		ConfigController* self = static_cast<ConfigController*>(parser->opts);
		QString optionName(QString::fromUtf8(option));
		if (optionName == QLatin1String("ecard")) {
			QStringList ecards;
			if (self->m_argvOptions.contains(optionName)) {
				ecards = self->m_argvOptions[optionName].toStringList();
			}
			ecards.append(QString::fromUtf8(arg));
			self->m_argvOptions[optionName] = ecards;
			return true;
		}
		if (optionName == QLatin1String("mb")) {
			self->m_argvOptions[optionName] = QString::fromUtf8(arg);
			return true;
		}
		if (optionName == QLatin1String("script")) {
			// Store the path for Window::argumentsPassed(); the actual load is
			// delayed until the user-facing Qt window is alive.
			self->m_argvOptions[optionName] = QString::fromUtf8(arg);
			return true;
		}
		return false;
	};
	m_subparsers[1].apply = nullptr;
	m_subparsers[1].extraOptions = nullptr;
	m_subparsers[1].longOptions = s_frontendOptions;
	m_subparsers[1].opts = this;
}

ConfigController::~ConfigController() {
	mCoreConfigDeinit(&m_config);
	mCoreConfigFreeOpts(&m_opts);

	if (m_parsed) {
		mArgumentsDeinit(&m_args);
	}
}

bool ConfigController::parseArguments(int argc, char* argv[]) {
	if (m_parsed) {
		return false;
	}

	if (mArgumentsParse(&m_args, argc, argv, m_subparsers.data(), m_subparsers.size())) {
		mCoreConfigFreeOpts(&m_opts);
		mArgumentsApply(&m_args, m_subparsers.data(), m_subparsers.size(), &m_config);
		mCoreConfigMap(&m_config, &m_opts);
		m_parsed = true;
		return true;
	}
	return false;
}

ConfigOption* ConfigController::addOption(const char* key) {
	QString optionName(key);

	if (m_optionSet.contains(optionName)) {
		return m_optionSet[optionName];
	}
	ConfigOption* newOption = new ConfigOption(optionName, this);
	m_optionSet[optionName] = newOption;
	connect(newOption, &ConfigOption::valueChanged, this, [this, key](const QVariant& value) {
		setOption(key, value);
	});
	return newOption;
}

void ConfigController::updateOption(const char* key) {
	if (!key) {
		return;
	}

	QString optionName(key);

	if (!m_optionSet.contains(optionName)) {
		return;
	}
	m_optionSet[optionName]->setValue(mCoreConfigGetValue(&m_config, key));
}

QString ConfigController::getOption(const char* key, const QVariant& defaultVal) const {
	const char* val = mCoreConfigGetValue(&m_config, key);
	if (val) {
		return QString(val);
	}
	return defaultVal.toString();
}

QString ConfigController::getOption(const QString& key, const QVariant& defaultVal) const {
	return getOption(key.toUtf8().constData(), defaultVal);
}

QVariant ConfigController::getQtOption(const QString& key, const QString& group) const {
	if (!group.isNull()) {
		m_settings->beginGroup(group);
	}
	QVariant value = m_settings->value(key);
	if (!group.isNull()) {
		m_settings->endGroup();
	}
	return value;
}

QVariant ConfigController::getArgvOption(const QString& key) const {
	return m_argvOptions.value(key);
}

QVariant ConfigController::takeArgvOption(const QString& key) {
		return m_argvOptions.take(key);
}

void ConfigController::saveOverride(const Override& override) {
	override.save(overrides());
	write();
}

void ConfigController::setOption(const char* key, bool value) {
	mCoreConfigSetIntValue(&m_config, key, value);
	QString optionName(key);
	if (m_optionSet.contains(optionName)) {
		m_optionSet[optionName]->setValue(value);
	}
}

void ConfigController::setOption(const char* key, int value) {
	mCoreConfigSetIntValue(&m_config, key, value);
	QString optionName(key);
	if (m_optionSet.contains(optionName)) {
		m_optionSet[optionName]->setValue(value);
	}
}

void ConfigController::setOption(const char* key, unsigned value) {
	mCoreConfigSetUIntValue(&m_config, key, value);
	QString optionName(key);
	if (m_optionSet.contains(optionName)) {
		m_optionSet[optionName]->setValue(value);
	}
}

void ConfigController::setOption(const char* key, const char* value) {
	mCoreConfigSetValue(&m_config, key, value);
	QString optionName(key);
	if (m_optionSet.contains(optionName)) {
		m_optionSet[optionName]->setValue(value);
	}
}

void ConfigController::setOption(const char* key, const QVariant& value) {
	if (value.type() == QVariant::Bool) {
		setOption(key, value.toBool());
		return;
	}
	QString stringValue(value.toString());
	setOption(key, stringValue.toUtf8().constData());
}

void ConfigController::setQtOption(const QString& key, const QVariant& value, const QString& group) {
	if (!group.isNull()) {
		m_settings->beginGroup(group);
	}
	m_settings->setValue(key, value);
	if (!group.isNull()) {
		m_settings->endGroup();
	}
}

QStringList ConfigController::getMRU(ConfigController::MRU mruType) const {
	QStringList mru;
	m_settings->beginGroup(mruName(mruType));
	for (int i = 0; i < MRU_LIST_SIZE; ++i) {
		QString item = m_settings->value(QString::number(i)).toString();
		if (item.isNull()) {
			continue;
		}
		mru.append(item);
	}
	m_settings->endGroup();
	return mru;
}

void ConfigController::setMRU(const QStringList& mru, ConfigController::MRU mruType) {
	int i = 0;
	m_settings->beginGroup(mruName(mruType));
	for (const QString& item : mru) {
		m_settings->setValue(QString::number(i), item);
		++i;
		if (i >= MRU_LIST_SIZE) {
			break;
		}
	}
	for (; i < MRU_LIST_SIZE; ++i) {
		m_settings->remove(QString::number(i));
	}
	m_settings->endGroup();
}

constexpr const char* ConfigController::mruName(ConfigController::MRU mru) {
	switch (mru) {
	case MRU::ROM:
		return "mru";
	case MRU::Script:
		return "recentScripts";
	}
	return "mru";
}

void ConfigController::write() {
	const QByteArray configIniPath = configFilePath(QStringLiteral("config.ini")).toUtf8();
	mCoreConfigSavePath(&m_config, configIniPath.constData());
	m_settings->sync();

	mCoreConfigFreeOpts(&m_opts);
	mCoreConfigMap(&m_config, &m_opts);
}

void ConfigController::makePortable() {
	mCoreConfigMakePortable(&m_config);
	s_configDir.clear();
	QDir().mkpath(configDir());

	QString fileName(configFilePath(QStringLiteral("qt.ini")));
	auto settings2 = std::make_unique<QSettings>(fileName, QSettings::IniFormat);
	for (const auto& key : m_settings->allKeys()) {
		settings2->setValue(key, m_settings->value(key));
	}
	m_settings = std::move(settings2);
	const QByteArray configIniPath = configFilePath(QStringLiteral("config.ini")).toUtf8();
	mCoreConfigSavePath(&m_config, configIniPath.constData());
}

void ConfigController::usage(const char* arg0) const {
	::usage(arg0, nullptr, nullptr, m_subparsers.data(), m_subparsers.size());
}

bool ConfigController::isPortable() {
	return mCoreConfigIsPortable();
}

const QString& ConfigController::configDir() {
	if (s_configDir.isNull()) {
		const QString workerName = workerInstanceName();
		s_configDir = !workerName.isEmpty()
		    ? portableWorkerDataPath(workerName, QStringLiteral("config"))
		    : shouldRedirectMaintainedPortableConfig()
		    ? portableDataPath(QStringLiteral("config"))
		    : rawConfigDir();
	}
	return s_configDir;
}
