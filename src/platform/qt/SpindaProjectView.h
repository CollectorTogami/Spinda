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

#include <QDialog>
#include <QPointer>
#include <QString>

#include <memory>

namespace QGBA {

class CoreController;

class SpindaProjectView : public QDialog {
Q_OBJECT

public:
	explicit SpindaProjectView(QWidget* parent = nullptr);
	~SpindaProjectView() override;

	void setController(std::shared_ptr<CoreController> controller);
	bool configureFromEnvironment(QString* errorMessage = nullptr);
	void startPhase3LaneFromEnvironment();

protected:
	void closeEvent(QCloseEvent* event) override;

private slots:
	void browsePhase2State();
	void browseSecondHalfCsv();
	void browseOutputDir();
	void browseCacheDir();
	void startPhase3Lane();
	void cancelRun();

private:
	struct Ui;

	QString defaultWorkspaceRoot() const;
	void appendLog(const QString& message);
	void setRunning(bool running);
	void startPhase3LaneWithOptions(bool suppressDialogs, bool exitOnComplete);
	bool runNativePhase3(QString* errorMessage);

	std::unique_ptr<Ui> m_ui;
	std::shared_ptr<CoreController> m_controller;
	bool m_running = false;
	bool m_cancelRequested = false;
};

}
