/* Copyright (c) 2013-2026 Jeffrey Pfau
 *
 * Original mGBA source is credited to Jeffrey Pfau and contributors.
 * Local custom modifications in this fork were added for this workspace and
 * are not upstream mGBA work or authored by Jeffrey Pfau.
 *
 * This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */
#pragma once

#include <QString>

class QJsonObject;
class QWidget;

namespace QGBA {

class ConfigController;

struct VirtualPadSettings {
	bool alwaysOnTop = false;
	bool autoboot = false;
	bool sticky = true;
	bool pressForFrames = false;
	int pressFrameCount = 1;
	bool clearAlsoClearsAnalog = false;
};

VirtualPadSettings customVirtualPadSettings(const ConfigController* config);
void saveCustomVirtualPadSettings(ConfigController* config, const VirtualPadSettings& settings);
bool showVirtualPadSettingsDialog(QWidget* owner, const VirtualPadSettings& currentSettings, VirtualPadSettings* updatedSettings);

struct WorkerInstanceInfo {
	bool isWorker = false;
	QString workerName;
	QString storageRoot;
	QString configDir;
	QString savegameDir;
	QString savestateDir;
	QString coordinationRoot;
};

struct WorkerCoordinationInfo {
	QString root;
	QString heartbeatDir;
	QString pendingJobsDir;
	QString claimedJobsDir;
	QString doneJobsDir;
	QString failedJobsDir;
};

QString customSanitizeWorkerName(const QString& workerName);
QString customWorkerSharedRoot();
QString customWorkerCoordinationRoot();
WorkerInstanceInfo customWorkerInstanceInfo(const QString& workerName = QString());
WorkerCoordinationInfo customWorkerCoordinationInfo();
bool launchWorkerInstance(const QString& workerName, QString* errorMessage = nullptr);
QString customWriteWorkerHeartbeat(const WorkerInstanceInfo& info, QString* errorMessage = nullptr);
QString customQueueWorkerJob(const QJsonObject& payload, QString* errorMessage = nullptr);
QString customClaimNextWorkerJob(const WorkerInstanceInfo& info, QString* errorMessage = nullptr);
void showWorkerInstanceDialog(QWidget* owner);

}
