/* Copyright (c) 2013-2026 Jeffrey Pfau
 *
 * Original mGBA source is credited to Jeffrey Pfau and contributors.
 * Local custom modifications in this fork were added for this workspace and
 * are not upstream mGBA work or authored by Jeffrey Pfau.
 *
 * This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */
#include "CustomFeatures.h"

#include "ConfigController.h"
#include "WindowsDarkChrome.h"

#include <QCoreApplication>
#include <QCheckBox>
#include <QDateTime>
#include <QDialog>
#include <QDialogButtonBox>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QFormLayout>
#include <QHBoxLayout>
#include <QJsonDocument>
#include <QJsonObject>
#include <QLabel>
#include <QLineEdit>
#include <QMessageBox>
#include <QObject>
#include <QProcess>
#include <QPushButton>
#include <QUuid>
#include <QVBoxLayout>
#include <QtGlobal>

using namespace QGBA;

namespace {

constexpr const char* CUSTOM_VIRTUAL_PAD_ALWAYS_ON_TOP = "customVirtualPadAlwaysOnTop";
constexpr const char* CUSTOM_VIRTUAL_PAD_AUTOBOOT = "customVirtualPadAutoboot";
constexpr const char* CUSTOM_VIRTUAL_PAD_STICKY = "customVirtualPadSticky";
constexpr const char* CUSTOM_VIRTUAL_PAD_PRESS_FOR_FRAMES = "customVirtualPadPressForFrames";
constexpr const char* CUSTOM_VIRTUAL_PAD_PRESS_FRAME_COUNT = "customVirtualPadPressFrameCount";
constexpr const char* CUSTOM_VIRTUAL_PAD_CLEAR_ALSO_CLEARS_ANALOG = "customVirtualPadClearAlsoClearsAnalog";
constexpr const char* WORKER_ENV_NAME = "MGBA_WORKER_INSTANCE";

QString workspaceRoot() {
	QDir appDir(QCoreApplication::applicationDirPath());
	if (appDir.dirName().startsWith(QStringLiteral("build-"))) {
		appDir.cdUp();
	}
	return appDir.absolutePath();
}

QString userdataPath(const QString& leaf) {
	return QDir::cleanPath(QDir(workspaceRoot()).filePath(QStringLiteral("userdata/%1").arg(leaf)));
}

bool ensureDirectory(const QString& path, QString* errorMessage) {
	if (QDir().mkpath(path)) {
		return true;
	}
	if (errorMessage) {
		*errorMessage = QObject::tr("Could not create directory: %1").arg(path);
	}
	return false;
}

bool ensureWorkerCoordinationTree(const WorkerCoordinationInfo& info, QString* errorMessage) {
	return ensureDirectory(info.heartbeatDir, errorMessage)
		&& ensureDirectory(info.pendingJobsDir, errorMessage)
		&& ensureDirectory(info.claimedJobsDir, errorMessage)
		&& ensureDirectory(info.doneJobsDir, errorMessage)
		&& ensureDirectory(info.failedJobsDir, errorMessage);
}

bool writeJsonFile(const QString& path, const QJsonObject& payload, QString* errorMessage) {
	QDir().mkpath(QFileInfo(path).absolutePath());
	QFile file(path);
	if (!file.open(QIODevice::WriteOnly | QIODevice::Truncate | QIODevice::Text)) {
		if (errorMessage) {
			*errorMessage = QObject::tr("Could not write %1: %2").arg(path, file.errorString());
		}
		return false;
	}
	file.write(QJsonDocument(payload).toJson(QJsonDocument::Indented));
	return true;
}

void showWorkerMessage(QWidget* owner, QMessageBox::Icon icon, const QString& title, const QString& text) {
	QMessageBox box(icon, title, text, QMessageBox::Ok, owner);
	applyWindowsDarkChrome(&box);
	box.exec();
}

}

VirtualPadSettings QGBA::customVirtualPadSettings(const ConfigController* config) {
	VirtualPadSettings settings;
	if (!config) {
		return settings;
	}
	settings.alwaysOnTop = config->getOption(CUSTOM_VIRTUAL_PAD_ALWAYS_ON_TOP, false).toInt();
	settings.autoboot = config->getOption(CUSTOM_VIRTUAL_PAD_AUTOBOOT, false).toInt();
	settings.sticky = config->getOption(CUSTOM_VIRTUAL_PAD_STICKY, true).toInt();
	settings.pressForFrames = config->getOption(CUSTOM_VIRTUAL_PAD_PRESS_FOR_FRAMES, false).toInt();
	settings.pressFrameCount = qMax(1, config->getOption(CUSTOM_VIRTUAL_PAD_PRESS_FRAME_COUNT, 1).toInt());
	settings.clearAlsoClearsAnalog = config->getOption(CUSTOM_VIRTUAL_PAD_CLEAR_ALSO_CLEARS_ANALOG, false).toInt();
	return settings;
}

void QGBA::saveCustomVirtualPadSettings(ConfigController* config, const VirtualPadSettings& settings) {
	if (!config) {
		return;
	}
	config->setOption(CUSTOM_VIRTUAL_PAD_ALWAYS_ON_TOP, settings.alwaysOnTop);
	config->setOption(CUSTOM_VIRTUAL_PAD_AUTOBOOT, settings.autoboot);
	config->setOption(CUSTOM_VIRTUAL_PAD_STICKY, settings.sticky);
	config->setOption(CUSTOM_VIRTUAL_PAD_PRESS_FOR_FRAMES, settings.pressForFrames);
	config->setOption(CUSTOM_VIRTUAL_PAD_PRESS_FRAME_COUNT, settings.pressFrameCount);
	config->setOption(CUSTOM_VIRTUAL_PAD_CLEAR_ALSO_CLEARS_ANALOG, settings.clearAlsoClearsAnalog);
}

bool QGBA::showVirtualPadSettingsDialog(QWidget* owner, const VirtualPadSettings& currentSettings, VirtualPadSettings* updatedSettings) {
	QDialog dialog(owner);
	dialog.setWindowTitle(QObject::tr("Virtual Pad Settings"));
	dialog.setModal(true);
	applyWindowsDarkChrome(&dialog);

	QVBoxLayout* layout = new QVBoxLayout(&dialog);

	QLabel* intro = new QLabel(QObject::tr(
		"Choose how the Custom Features Virtual Pad window behaves when it is open or when mGBA starts."));
	intro->setWordWrap(true);
	layout->addWidget(intro);

	QCheckBox* alwaysOnTop = new QCheckBox(QObject::tr("Keep Virtual Pad on top of other windows"));
	alwaysOnTop->setChecked(currentSettings.alwaysOnTop);
	layout->addWidget(alwaysOnTop);

	QCheckBox* autoboot = new QCheckBox(QObject::tr("Open Virtual Pad when mGBA starts"));
	autoboot->setChecked(currentSettings.autoboot);
	layout->addWidget(autoboot);

	QDialogButtonBox* buttons = new QDialogButtonBox(QDialogButtonBox::Ok | QDialogButtonBox::Cancel);
	QObject::connect(buttons, &QDialogButtonBox::accepted, &dialog, &QDialog::accept);
	QObject::connect(buttons, &QDialogButtonBox::rejected, &dialog, &QDialog::reject);
	layout->addWidget(buttons);

	if (dialog.exec() != QDialog::Accepted) {
		return false;
	}

	if (updatedSettings) {
		updatedSettings->alwaysOnTop = alwaysOnTop->isChecked();
		updatedSettings->autoboot = autoboot->isChecked();
	}
	return true;
}

QString QGBA::customSanitizeWorkerName(const QString& workerName) {
	QString trimmed = workerName.trimmed();
	QString cleaned;
	for (const QChar& ch : trimmed) {
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

QString QGBA::customWorkerSharedRoot() {
	return userdataPath(QStringLiteral("workers"));
}

QString QGBA::customWorkerCoordinationRoot() {
	return userdataPath(QStringLiteral("worker-coordination"));
}

WorkerInstanceInfo QGBA::customWorkerInstanceInfo(const QString& workerName) {
	WorkerInstanceInfo info;
	info.workerName = customSanitizeWorkerName(workerName.isNull()
		? QString::fromLocal8Bit(qgetenv(WORKER_ENV_NAME))
		: workerName);
	info.isWorker = !info.workerName.isEmpty();
	info.coordinationRoot = customWorkerCoordinationRoot();
	if (info.isWorker) {
		info.storageRoot = QDir(customWorkerSharedRoot()).filePath(info.workerName);
		info.configDir = QDir(info.storageRoot).filePath(QStringLiteral("config"));
		info.savegameDir = QDir(info.storageRoot).filePath(QStringLiteral("savegames"));
		info.savestateDir = QDir(info.storageRoot).filePath(QStringLiteral("savestates"));
	} else {
		info.storageRoot = userdataPath(QStringLiteral("main"));
		info.configDir = ConfigController::configDir();
		info.savegameDir = userdataPath(QStringLiteral("savegames"));
		info.savestateDir = userdataPath(QStringLiteral("savestates"));
	}
	return info;
}

WorkerCoordinationInfo QGBA::customWorkerCoordinationInfo() {
	WorkerCoordinationInfo info;
	info.root = customWorkerCoordinationRoot();
	QDir root(info.root);
	info.heartbeatDir = root.filePath(QStringLiteral("heartbeats"));
	info.pendingJobsDir = root.filePath(QStringLiteral("jobs/pending"));
	info.claimedJobsDir = root.filePath(QStringLiteral("jobs/claimed"));
	info.doneJobsDir = root.filePath(QStringLiteral("jobs/done"));
	info.failedJobsDir = root.filePath(QStringLiteral("jobs/failed"));
	return info;
}

bool QGBA::launchWorkerInstance(const QString& workerName, QString* errorMessage) {
	const QString sanitizedName = customSanitizeWorkerName(workerName);
	if (sanitizedName.isEmpty()) {
		if (errorMessage) {
			*errorMessage = QObject::tr("Worker name cannot be empty.");
		}
		return false;
	}

	WorkerInstanceInfo info = customWorkerInstanceInfo(sanitizedName);
	if (!ensureDirectory(info.configDir, errorMessage)
		|| !ensureDirectory(info.savegameDir, errorMessage)
		|| !ensureDirectory(info.savestateDir, errorMessage)
		|| !ensureWorkerCoordinationTree(customWorkerCoordinationInfo(), errorMessage)) {
		return false;
	}

	QProcess worker;
	QProcessEnvironment environment = QProcessEnvironment::systemEnvironment();
	environment.insert(QString::fromLatin1(WORKER_ENV_NAME), sanitizedName);
	worker.setProcessEnvironment(environment);
	worker.setProgram(QCoreApplication::applicationFilePath());
	worker.setWorkingDirectory(QCoreApplication::applicationDirPath());
	if (!worker.startDetached()) {
		if (errorMessage) {
			*errorMessage = QObject::tr("Could not launch worker instance.");
		}
		return false;
	}
	return true;
}

QString QGBA::customWriteWorkerHeartbeat(const WorkerInstanceInfo& info, QString* errorMessage) {
	WorkerCoordinationInfo coordination = customWorkerCoordinationInfo();
	if (!ensureWorkerCoordinationTree(coordination, errorMessage)) {
		return QString();
	}
	const QString workerName = info.workerName.isEmpty() ? QStringLiteral("main") : info.workerName;
	const QString path = QDir(coordination.heartbeatDir).filePath(QStringLiteral("%1-%2.json")
		.arg(workerName)
		.arg(QCoreApplication::applicationPid()));

	QJsonObject payload;
	payload.insert(QStringLiteral("workerName"), workerName);
	payload.insert(QStringLiteral("isWorker"), info.isWorker);
	payload.insert(QStringLiteral("pid"), static_cast<qint64>(QCoreApplication::applicationPid()));
	payload.insert(QStringLiteral("updatedAtUtc"), QDateTime::currentDateTimeUtc().toString(Qt::ISODateWithMs));
	payload.insert(QStringLiteral("storageRoot"), info.storageRoot);
	payload.insert(QStringLiteral("configDir"), info.configDir);
	payload.insert(QStringLiteral("savegameDir"), info.savegameDir);
	payload.insert(QStringLiteral("savestateDir"), info.savestateDir);
	if (!writeJsonFile(path, payload, errorMessage)) {
		return QString();
	}
	return path;
}

QString QGBA::customQueueWorkerJob(const QJsonObject& payload, QString* errorMessage) {
	WorkerCoordinationInfo coordination = customWorkerCoordinationInfo();
	if (!ensureWorkerCoordinationTree(coordination, errorMessage)) {
		return QString();
	}
	const QString jobId = QUuid::createUuid().toString(QUuid::WithoutBraces);
	const QString path = QDir(coordination.pendingJobsDir).filePath(QStringLiteral("%1.json").arg(jobId));
	QJsonObject job = payload;
	job.insert(QStringLiteral("jobId"), jobId);
	job.insert(QStringLiteral("queuedAtUtc"), QDateTime::currentDateTimeUtc().toString(Qt::ISODateWithMs));
	if (!writeJsonFile(path, job, errorMessage)) {
		return QString();
	}
	return path;
}

QString QGBA::customClaimNextWorkerJob(const WorkerInstanceInfo& info, QString* errorMessage) {
	WorkerCoordinationInfo coordination = customWorkerCoordinationInfo();
	if (!ensureWorkerCoordinationTree(coordination, errorMessage)) {
		return QString();
	}

	QDir pending(coordination.pendingJobsDir);
	const QStringList jobs = pending.entryList(QStringList() << QStringLiteral("*.json"), QDir::Files, QDir::Name);
	const QString workerName = info.workerName.isEmpty() ? QStringLiteral("main") : info.workerName;
	for (const QString& jobName : jobs) {
		const QString sourcePath = pending.filePath(jobName);
		const QString claimedName = QStringLiteral("%1-%2").arg(workerName, jobName);
		const QString claimedPath = QDir(coordination.claimedJobsDir).filePath(claimedName);
		if (QFile::rename(sourcePath, claimedPath)) {
			return claimedPath;
		}
	}
	return QString();
}

void QGBA::showWorkerInstanceDialog(QWidget* owner) {
	QDialog dialog(owner);
	dialog.setWindowTitle(QObject::tr("Worker Instances"));
	dialog.setModal(false);
	applyWindowsDarkChrome(&dialog);

	WorkerInstanceInfo currentInfo = customWorkerInstanceInfo();
	WorkerCoordinationInfo coordination = customWorkerCoordinationInfo();

	QVBoxLayout* layout = new QVBoxLayout(&dialog);
	QLabel* intro = new QLabel(QObject::tr(
		"Launch isolated mGBA worker processes with their own config, save, and savestate folders. "
		"Shared heartbeat and job files stay under userdata/worker-coordination."));
	intro->setWordWrap(true);
	layout->addWidget(intro);

	QFormLayout* form = new QFormLayout();
	form->addRow(QObject::tr("Current worker:"), new QLabel(currentInfo.isWorker ? currentInfo.workerName : QObject::tr("(main instance)")));
	form->addRow(QObject::tr("Worker root:"), new QLabel(customWorkerSharedRoot()));
	form->addRow(QObject::tr("Coordination root:"), new QLabel(coordination.root));
	layout->addLayout(form);

	QHBoxLayout* launchRow = new QHBoxLayout();
	QLineEdit* workerName = new QLineEdit(QStringLiteral("worker-1"));
	workerName->setPlaceholderText(QObject::tr("worker name"));
	QPushButton* launchButton = new QPushButton(QObject::tr("Launch worker"));
	launchRow->addWidget(workerName, 1);
	launchRow->addWidget(launchButton);
	layout->addLayout(launchRow);

	QHBoxLayout* actionRow = new QHBoxLayout();
	QPushButton* heartbeatButton = new QPushButton(QObject::tr("Write heartbeat"));
	QPushButton* queueButton = new QPushButton(QObject::tr("Queue sample job"));
	QPushButton* claimButton = new QPushButton(QObject::tr("Claim next job"));
	actionRow->addWidget(heartbeatButton);
	actionRow->addWidget(queueButton);
	actionRow->addWidget(claimButton);
	layout->addLayout(actionRow);

	QDialogButtonBox* buttons = new QDialogButtonBox(QDialogButtonBox::Close);
	QObject::connect(buttons, &QDialogButtonBox::rejected, &dialog, &QDialog::reject);
	layout->addWidget(buttons);

	QObject::connect(launchButton, &QPushButton::clicked, &dialog, [&dialog, workerName]() {
		QString error;
		const QString sanitizedName = customSanitizeWorkerName(workerName->text());
		if (!launchWorkerInstance(sanitizedName, &error)) {
			showWorkerMessage(&dialog, QMessageBox::Warning, QObject::tr("Worker launch failed"), error);
			return;
		}
		showWorkerMessage(&dialog, QMessageBox::Information, QObject::tr("Worker launched"),
			QObject::tr("Launched worker instance: %1").arg(sanitizedName));
	});

	QObject::connect(heartbeatButton, &QPushButton::clicked, &dialog, [&dialog]() {
		QString error;
		const QString path = customWriteWorkerHeartbeat(customWorkerInstanceInfo(), &error);
		if (path.isEmpty()) {
			showWorkerMessage(&dialog, QMessageBox::Warning, QObject::tr("Heartbeat failed"), error);
			return;
		}
		showWorkerMessage(&dialog, QMessageBox::Information, QObject::tr("Heartbeat written"), path);
	});

	QObject::connect(queueButton, &QPushButton::clicked, &dialog, [&dialog]() {
		QJsonObject payload;
		payload.insert(QStringLiteral("type"), QStringLiteral("manual-sample"));
		payload.insert(QStringLiteral("source"), QStringLiteral("Worker Instances dialog"));
		QString error;
		const QString path = customQueueWorkerJob(payload, &error);
		if (path.isEmpty()) {
			showWorkerMessage(&dialog, QMessageBox::Warning, QObject::tr("Queue failed"), error);
			return;
		}
		showWorkerMessage(&dialog, QMessageBox::Information, QObject::tr("Sample job queued"), path);
	});

	QObject::connect(claimButton, &QPushButton::clicked, &dialog, [&dialog]() {
		QString error;
		const QString path = customClaimNextWorkerJob(customWorkerInstanceInfo(), &error);
		if (!error.isEmpty()) {
			showWorkerMessage(&dialog, QMessageBox::Warning, QObject::tr("Claim failed"), error);
			return;
		}
		if (path.isEmpty()) {
			showWorkerMessage(&dialog, QMessageBox::Information, QObject::tr("No pending job"),
				QObject::tr("No pending worker job was available."));
			return;
		}
		showWorkerMessage(&dialog, QMessageBox::Information, QObject::tr("Job claimed"), path);
	});

	dialog.exec();
}
