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

#include <QJsonObject>
#include <QJsonValue>
#include <QMetaObject>
#include <QPointer>
#include <QStringList>
#include <QVector>
#include <QWidget>

#include <cstdint>
#include <functional>
#include <memory>

class QCheckBox;
class QComboBox;
class QLabel;
class QPlainTextEdit;
class QPushButton;
class QShowEvent;

namespace QGBA {

class ConfigController;
class CoreController;

class InputTapeView : public QWidget {
public:
	explicit InputTapeView(ConfigController* config, QWidget* parent = nullptr);
	virtual ~InputTapeView();

	void setController(const std::shared_ptr<CoreController>& controller);
	void setMappedInputSampler(std::function<uint32_t()> sampler);
	void setVirtualPadSampler(std::function<uint32_t()> sampler);
	void setAlwaysOnTop(bool enable);

protected:
	virtual void showEvent(QShowEvent* event) override;
	virtual void closeEvent(QCloseEvent* event) override;

private:
	struct InputRun {
		uint32_t mask = 0;
		int frames = 0;
	};

	enum class RecordSource {
		MappedQtInput,
		VirtualPadOnly,
	};

	void buildUi();
	void refreshUi();
	void refreshTapePreview();
	void setStatus(const QString& text);
	void appendFrame(uint32_t mask);
	void markTapePreviewDirty();
	uint64_t frameCount() const;
	RecordSource recordSource() const;
	uint32_t sampleRecordMask() const;

	void startRecording();
	void stopRecording();
	void clearTape();
	void loadTape();
	void saveTape();
	void editTape();
	void startReplay();
	void stopReplay();
	void advanceReplayFrame();
	void frameAdvance();
	void onFrameAvailable();
	void finishReplay(bool completed);
	void pauseCoreAtBoundary();
	void setReplayOverride(uint32_t mask);
	void clearReplayOverride();

	QJsonObject tapeToJson() const;
	bool loadTapeFromJson(const QJsonObject& root, QString* errorMessage);
	static QString formatMask(uint32_t mask);
	static QStringList buttonNames(uint32_t mask);
	static QString describeMask(uint32_t mask);
	static bool parseMask(const QJsonValue& value, uint32_t* mask, QString* errorMessage);
	static bool parseButtons(const QJsonValue& value, uint32_t* mask, QString* errorMessage);
	static bool parseFrames(const QJsonValue& value, int* frames, QString* errorMessage);
	static bool normalizeEditedTapeJson(QJsonObject* root, QString* errorMessage);

	ConfigController* m_config = nullptr;
	std::weak_ptr<CoreController> m_controller;
	std::function<uint32_t()> m_mappedInputSampler;
	std::function<uint32_t()> m_virtualPadSampler;
	QMetaObject::Connection m_frameConnection;

	QCheckBox* m_alwaysOnTop = nullptr;
	QComboBox* m_recordSource = nullptr;
	QPushButton* m_startRecording = nullptr;
	QPushButton* m_stopRecording = nullptr;
	QPushButton* m_loadTape = nullptr;
	QPushButton* m_saveTape = nullptr;
	QPushButton* m_editTape = nullptr;
	QPushButton* m_clearTape = nullptr;
	QPushButton* m_replayTape = nullptr;
	QPushButton* m_stopReplay = nullptr;
	QPushButton* m_frameAdvance = nullptr;
	QLabel* m_summary = nullptr;
	QLabel* m_status = nullptr;
	QLabel* m_fileLabel = nullptr;
	QPlainTextEdit* m_preview = nullptr;

	QVector<InputRun> m_runs;
	QString m_currentPath;
	QJsonObject m_startProbe;
	QJsonObject m_endProbe;
	// Keep the common per-frame UI path O(1). Long tapes may have many runs,
	// and recording/replay can emit frameAvailable thousands of times.
	uint64_t m_totalFrames = 0;
	bool m_recording = false;
	bool m_replaying = false;
	bool m_alwaysOnTopValue = false;
	bool m_replayOverrideActive = false;
	// The text preview is intentionally decoupled from the exact summary label.
	// Rebuilding QPlainTextEdit contents every frame is expensive on long runs.
	bool m_previewDirty = true;
	int m_replayRunIndex = 0;
	int m_replayFramesRemaining = 0;
	uint32_t m_replayMask = 0;
	uint64_t m_replayedFrames = 0;
};

}
