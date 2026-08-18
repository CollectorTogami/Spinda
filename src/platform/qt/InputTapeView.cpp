/* Copyright (c) 2013-2026 Jeffrey Pfau
 *
 * Original mGBA source is credited to Jeffrey Pfau and contributors.
 * Local custom modifications in this fork were added for this workspace and
 * are not upstream mGBA work or authored by Jeffrey Pfau.
 *
 * This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */
#include "InputTapeView.h"

#include "ConfigController.h"
#include "CoreController.h"
#include "GBAApp.h"
#include "WindowsDarkChrome.h"

#include <QAbstractButton>
#include <QCheckBox>
#include <QCloseEvent>
#include <QComboBox>
#include <QDateTime>
#include <QDialog>
#include <QDialogButtonBox>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QHBoxLayout>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonParseError>
#include <QLabel>
#include <QMessageBox>
#include <QObject>
#include <QPlainTextEdit>
#include <QPushButton>
#include <QShowEvent>
#include <QTimer>
#include <QVBoxLayout>

#include <limits>

#include <mgba/internal/gba/input.h>

using namespace QGBA;

namespace {

constexpr const char* INPUT_TAPE_JSON_FORMAT = "mgba-input-tape-v1";
constexpr const char* INPUT_TAPE_ALWAYS_ON_TOP = "customInputTapeAlwaysOnTop";
constexpr uint32_t GBA_KNOWN_BUTTON_MASK = 0x03FFu;
constexpr int INPUT_TAPE_PREVIEW_RUN_LIMIT = 200;
constexpr uint64_t INPUT_TAPE_LIVE_PREVIEW_FRAME_STEP = 60;
constexpr uint64_t INPUT_TAPE_REPLAY_STATUS_FRAME_STEP = 60;

struct ButtonInfo {
	const char* name;
	int key;
};

constexpr ButtonInfo BUTTONS[] = {
	{ "A", GBA_KEY_A },
	{ "B", GBA_KEY_B },
	{ "SELECT", GBA_KEY_SELECT },
	{ "START", GBA_KEY_START },
	{ "RIGHT", GBA_KEY_RIGHT },
	{ "LEFT", GBA_KEY_LEFT },
	{ "UP", GBA_KEY_UP },
	{ "DOWN", GBA_KEY_DOWN },
	{ "R", GBA_KEY_R },
	{ "L", GBA_KEY_L },
};

bool validRun(int frames) {
	return frames > 0;
}

void showInputTapeWarning(QWidget* owner, const QString& message) {
	QMessageBox dialog(QMessageBox::Warning, QObject::tr("Input Tapes"), message, QMessageBox::Ok, owner);
	dialog.setWindowModality(owner ? Qt::WindowModal : Qt::ApplicationModal);
	applyWindowsDarkChrome(&dialog);
	dialog.exec();
}

}

InputTapeView::InputTapeView(ConfigController* config, QWidget* parent)
	: QWidget(parent, Qt::Tool)
	, m_config(config)
{
	setWindowTitle(tr("Input Tapes"));
	setMinimumSize(620, 460);
	buildUi();
	setAlwaysOnTop(m_config && m_config->getOption(INPUT_TAPE_ALWAYS_ON_TOP, false).toInt());
	refreshUi();
}

InputTapeView::~InputTapeView() {
	stopReplay();
	stopRecording();
}

void InputTapeView::setController(const std::shared_ptr<CoreController>& controller) {
	if (m_frameConnection) {
		disconnect(m_frameConnection);
		m_frameConnection = QMetaObject::Connection();
	}
	stopReplay();
	stopRecording();
	m_controller = controller;
	if (controller) {
		m_frameConnection = connect(controller.get(), &CoreController::frameAvailable, this, &InputTapeView::onFrameAvailable);
	}
	refreshUi();
}

void InputTapeView::setMappedInputSampler(std::function<uint32_t()> sampler) {
	m_mappedInputSampler = std::move(sampler);
}

void InputTapeView::setVirtualPadSampler(std::function<uint32_t()> sampler) {
	m_virtualPadSampler = std::move(sampler);
}

void InputTapeView::setAlwaysOnTop(bool enable) {
	if (m_alwaysOnTopValue == enable) {
		return;
	}
	m_alwaysOnTopValue = enable;
	const bool wasVisible = isVisible();
	Qt::WindowFlags flags = windowFlags();
	if (enable) {
		flags |= Qt::WindowStaysOnTopHint;
	} else {
		flags &= ~Qt::WindowStaysOnTopHint;
	}
	setWindowFlags(flags);
	if (m_alwaysOnTop) {
		m_alwaysOnTop->setChecked(enable);
	}
	if (wasVisible) {
		show();
		applyWindowsDarkChrome(this);
	}
}

void InputTapeView::showEvent(QShowEvent* event) {
	QWidget::showEvent(event);
	applyWindowsDarkChrome(this);
}

void InputTapeView::closeEvent(QCloseEvent* event) {
	stopReplay();
	stopRecording();
	QWidget::closeEvent(event);
}

void InputTapeView::buildUi() {
	QVBoxLayout* layout = new QVBoxLayout(this);
	layout->setContentsMargins(10, 10, 10, 10);
	layout->setSpacing(8);

	QLabel* intro = new QLabel(tr(
		"Record and replay anchor-agnostic input tapes. The tape stores only "
		"per-frame GBA button masks; ROMs, saves, and savestates remain the "
		"caller/user's responsibility."));
	intro->setWordWrap(true);
	layout->addWidget(intro);

	m_alwaysOnTop = new QCheckBox(tr("Keep Input Tapes on top of other windows"));
	connect(m_alwaysOnTop, &QCheckBox::toggled, this, [this](bool checked) {
		if (m_config) {
			m_config->setOption(INPUT_TAPE_ALWAYS_ON_TOP, checked);
		}
		setAlwaysOnTop(checked);
	});
	layout->addWidget(m_alwaysOnTop);

	QHBoxLayout* sourceLayout = new QHBoxLayout();
	sourceLayout->addWidget(new QLabel(tr("Record source:")));
	m_recordSource = new QComboBox();
	m_recordSource->addItem(tr("Mapped Qt input (keyboard/controllers/Virtual Pad)"), int(RecordSource::MappedQtInput));
	m_recordSource->addItem(tr("Virtual Pad only"), int(RecordSource::VirtualPadOnly));
	sourceLayout->addWidget(m_recordSource, 1);
	layout->addLayout(sourceLayout);

	QHBoxLayout* recordingLayout = new QHBoxLayout();
	m_startRecording = new QPushButton(tr("Start Recording"));
	m_stopRecording = new QPushButton(tr("Stop Recording"));
	m_frameAdvance = new QPushButton(tr("Frame Advance"));
	recordingLayout->addWidget(m_startRecording);
	recordingLayout->addWidget(m_stopRecording);
	recordingLayout->addWidget(m_frameAdvance);
	connect(m_startRecording, &QPushButton::clicked, this, &InputTapeView::startRecording);
	connect(m_stopRecording, &QPushButton::clicked, this, &InputTapeView::stopRecording);
	connect(m_frameAdvance, &QPushButton::clicked, this, &InputTapeView::frameAdvance);
	layout->addLayout(recordingLayout);

	QHBoxLayout* fileLayout = new QHBoxLayout();
	m_loadTape = new QPushButton(tr("Load..."));
	m_saveTape = new QPushButton(tr("Save..."));
	m_editTape = new QPushButton(tr("Edit..."));
	m_clearTape = new QPushButton(tr("Clear"));
	m_replayTape = new QPushButton(tr("Replay"));
	m_stopReplay = new QPushButton(tr("Stop Replay"));
	fileLayout->addWidget(m_loadTape);
	fileLayout->addWidget(m_saveTape);
	fileLayout->addWidget(m_editTape);
	fileLayout->addWidget(m_clearTape);
	fileLayout->addStretch();
	fileLayout->addWidget(m_replayTape);
	fileLayout->addWidget(m_stopReplay);
	connect(m_loadTape, &QPushButton::clicked, this, &InputTapeView::loadTape);
	connect(m_saveTape, &QPushButton::clicked, this, &InputTapeView::saveTape);
	connect(m_editTape, &QPushButton::clicked, this, &InputTapeView::editTape);
	connect(m_clearTape, &QPushButton::clicked, this, &InputTapeView::clearTape);
	connect(m_replayTape, &QPushButton::clicked, this, &InputTapeView::startReplay);
	connect(m_stopReplay, &QPushButton::clicked, this, &InputTapeView::stopReplay);
	layout->addLayout(fileLayout);

	m_summary = new QLabel(this);
	m_summary->setWordWrap(true);
	layout->addWidget(m_summary);

	m_fileLabel = new QLabel(this);
	m_fileLabel->setWordWrap(true);
	layout->addWidget(m_fileLabel);

	m_status = new QLabel(this);
	m_status->setWordWrap(true);
	layout->addWidget(m_status);

	m_preview = new QPlainTextEdit(this);
	m_preview->setReadOnly(true);
	m_preview->setPlaceholderText(tr("No tape loaded or recorded yet."));
	layout->addWidget(m_preview, 1);
}

void InputTapeView::refreshUi() {
	const bool hasController = !m_controller.expired();
	const bool hasTape = !m_runs.isEmpty();
	if (m_alwaysOnTop) {
		m_alwaysOnTop->setChecked(m_alwaysOnTopValue);
	}
	if (m_startRecording) {
		m_startRecording->setEnabled(hasController && !m_recording && !m_replaying);
	}
	if (m_stopRecording) {
		m_stopRecording->setEnabled(m_recording);
	}
	if (m_loadTape) {
		m_loadTape->setEnabled(!m_recording && !m_replaying);
	}
	if (m_saveTape) {
		m_saveTape->setEnabled(hasTape && !m_recording && !m_replaying);
	}
	if (m_editTape) {
		m_editTape->setEnabled(hasTape && !m_recording && !m_replaying);
	}
	if (m_clearTape) {
		m_clearTape->setEnabled(hasTape && !m_recording && !m_replaying);
	}
	if (m_replayTape) {
		m_replayTape->setEnabled(hasController && hasTape && !m_recording && !m_replaying);
	}
	if (m_stopReplay) {
		m_stopReplay->setEnabled(m_replaying);
	}
	if (m_frameAdvance) {
		m_frameAdvance->setEnabled(hasController && !m_replaying);
	}
	if (m_summary) {
		m_summary->setText(tr("Tape: %1 run(s), %2 frame(s)")
			.arg(m_runs.size())
			.arg(frameCount()));
	}
	if (m_fileLabel) {
		m_fileLabel->setText(m_currentPath.isEmpty()
			? tr("File: unsaved")
			: tr("File: %1").arg(QDir::toNativeSeparators(m_currentPath)));
	}
	if (m_previewDirty) {
		refreshTapePreview();
	}
}

void InputTapeView::refreshTapePreview() {
	if (!m_preview) {
		return;
	}
	QStringList lines;
	const int visibleRuns = qMin(m_runs.size(), INPUT_TAPE_PREVIEW_RUN_LIMIT);
	for (int i = 0; i < visibleRuns; ++i) {
		const InputRun& run = m_runs.at(i);
		lines.append(tr("%1: %2 for %3 frame(s)")
			.arg(i + 1, 4)
			.arg(describeMask(run.mask))
			.arg(run.frames));
	}
	if (visibleRuns < m_runs.size()) {
		lines.append(tr("... %1 more run(s) omitted from preview").arg(m_runs.size() - visibleRuns));
	}
	m_preview->setPlainText(lines.join(QLatin1Char('\n')));
	m_previewDirty = false;
}

void InputTapeView::setStatus(const QString& text) {
	if (m_status) {
		m_status->setText(text);
	}
}

void InputTapeView::appendFrame(uint32_t mask) {
	mask &= GBA_KNOWN_BUTTON_MASK;
	++m_totalFrames;
	if (!m_runs.isEmpty() && m_runs.last().mask == mask) {
		InputRun& run = m_runs.last();
		++run.frames;
		// Long held inputs can run for thousands of frames. The summary label
		// remains exact via m_totalFrames, while the expensive text preview is
		// refreshed periodically instead of being rebuilt on every frame.
		if (m_totalFrames % INPUT_TAPE_LIVE_PREVIEW_FRAME_STEP == 0) {
			markTapePreviewDirty();
		}
	} else {
		m_runs.append(InputRun{ mask, 1 });
		markTapePreviewDirty();
	}
}

void InputTapeView::markTapePreviewDirty() {
	m_previewDirty = true;
}

uint64_t InputTapeView::frameCount() const {
	return m_totalFrames;
}

InputTapeView::RecordSource InputTapeView::recordSource() const {
	return RecordSource(m_recordSource ? m_recordSource->currentData().toInt() : int(RecordSource::MappedQtInput));
}

uint32_t InputTapeView::sampleRecordMask() const {
	switch (recordSource()) {
	case RecordSource::VirtualPadOnly:
		return m_virtualPadSampler ? (m_virtualPadSampler() & GBA_KNOWN_BUTTON_MASK) : 0;
	case RecordSource::MappedQtInput:
	default:
		if (auto controller = m_controller.lock()) {
			// Use the mask that was actually installed for the completed frame.
			// Sampling pendingKeys() here would be one frame ahead after
			// CoreController::finishFrame() refreshes host input.
			return controller->lastFrameKeys() & GBA_KNOWN_BUTTON_MASK;
		}
		return m_mappedInputSampler ? (m_mappedInputSampler() & GBA_KNOWN_BUTTON_MASK) : 0;
	}
}

void InputTapeView::startRecording() {
	if (m_recording || m_replaying) {
		return;
	}
	if (m_controller.expired()) {
		showInputTapeWarning(this, tr("Load a game before recording an input tape."));
		return;
	}
	pauseCoreAtBoundary();
	m_runs.clear();
	m_totalFrames = 0;
	markTapePreviewDirty();
	m_currentPath.clear();
	m_startProbe = QJsonObject();
	m_endProbe = QJsonObject();
	if (auto controller = m_controller.lock()) {
		m_startProbe.insert(QStringLiteral("frame_counter"), QString::number(controller->frameCounter()));
		m_startProbe.insert(QStringLiteral("pending_keys"), formatMask(controller->pendingKeys() & GBA_KNOWN_BUTTON_MASK));
	}
	m_recording = true;
	setStatus(tr("Recording. The core is paused at the start boundary; unpause or frame-advance to add frames."));
	refreshUi();
}

void InputTapeView::stopRecording() {
	if (!m_recording) {
		return;
	}
	pauseCoreAtBoundary();
	if (auto controller = m_controller.lock()) {
		m_endProbe.insert(QStringLiteral("frame_counter"), QString::number(controller->frameCounter()));
		m_endProbe.insert(QStringLiteral("pending_keys"), formatMask(controller->pendingKeys() & GBA_KNOWN_BUTTON_MASK));
	}
	markTapePreviewDirty();
	m_recording = false;
	setStatus(tr("Recording stopped at a paused boundary."));
	refreshUi();
}

void InputTapeView::clearTape() {
	if (m_recording || m_replaying) {
		return;
	}
	m_runs.clear();
	m_totalFrames = 0;
	markTapePreviewDirty();
	m_currentPath.clear();
	m_startProbe = QJsonObject();
	m_endProbe = QJsonObject();
	setStatus(tr("Tape cleared."));
	refreshUi();
}

void InputTapeView::loadTape() {
	if (m_recording || m_replaying) {
		return;
	}
	const QString path = GBAApp::app()->getOpenFileName(
		this,
		tr("Load input tape"),
		tr("Input tapes (*.inputtape.json *.json);;All files (*)"));
	if (path.isEmpty()) {
		return;
	}

	QFile file(path);
	if (!file.open(QIODevice::ReadOnly | QIODevice::Text)) {
		showInputTapeWarning(this, tr("Could not open %1 for reading.").arg(path));
		return;
	}
	QJsonParseError parseError;
	const QJsonDocument document = QJsonDocument::fromJson(file.readAll(), &parseError);
	if (parseError.error != QJsonParseError::NoError || !document.isObject()) {
		showInputTapeWarning(this, tr("Invalid input tape JSON: %1").arg(parseError.errorString()));
		return;
	}

	QString errorMessage;
	if (!loadTapeFromJson(document.object(), &errorMessage)) {
		showInputTapeWarning(this, errorMessage);
		return;
	}
	m_currentPath = path;
	setStatus(tr("Loaded input tape."));
	refreshUi();
}

void InputTapeView::saveTape() {
	if (m_recording || m_replaying || m_runs.isEmpty()) {
		return;
	}
	const QString suggested = m_currentPath.isEmpty() ? QStringLiteral("input-tape.inputtape.json") : m_currentPath;
	const QString path = GBAApp::app()->getSaveFileName(
		this,
		tr("Save input tape"),
		tr("Input tapes (*.inputtape.json *.json);;All files (*)"),
		suggested);
	if (path.isEmpty()) {
		return;
	}

	QFile file(path);
	if (!file.open(QIODevice::WriteOnly | QIODevice::Truncate | QIODevice::Text)) {
		showInputTapeWarning(this, tr("Could not open %1 for writing.").arg(path));
		return;
	}
	file.write(QJsonDocument(tapeToJson()).toJson(QJsonDocument::Indented));
	m_currentPath = path;
	setStatus(tr("Saved input tape."));
	refreshUi();
}

void InputTapeView::editTape() {
	if (m_recording || m_replaying || m_runs.isEmpty()) {
		return;
	}

	QDialog dialog(this);
	dialog.setWindowTitle(tr("Edit Input Tape"));
	dialog.resize(760, 620);
	applyWindowsDarkChrome(&dialog);

	QVBoxLayout* layout = new QVBoxLayout(&dialog);
	QLabel* help = new QLabel(tr(
		"Edit the tape JSON as plaintext. Change run `frames`, `mask`, or "
		"`buttons` values, then Apply. `frame_count` is recalculated on Apply "
		"so frame-count edits do not require updating the header manually."));
	help->setWordWrap(true);
	layout->addWidget(help);

	QPlainTextEdit* editor = new QPlainTextEdit(&dialog);
	editor->setPlainText(QString::fromUtf8(QJsonDocument(tapeToJson()).toJson(QJsonDocument::Indented)));
	editor->setLineWrapMode(QPlainTextEdit::NoWrap);
	layout->addWidget(editor, 1);

	QDialogButtonBox* buttons = new QDialogButtonBox(QDialogButtonBox::Ok | QDialogButtonBox::Cancel, &dialog);
	buttons->button(QDialogButtonBox::Ok)->setText(tr("Apply"));
	layout->addWidget(buttons);

	connect(buttons, &QDialogButtonBox::accepted, &dialog, &QDialog::accept);
	connect(buttons, &QDialogButtonBox::rejected, &dialog, &QDialog::reject);

	while (dialog.exec() == QDialog::Accepted) {
		QJsonParseError parseError;
		QJsonDocument document = QJsonDocument::fromJson(editor->toPlainText().toUtf8(), &parseError);
		if (parseError.error != QJsonParseError::NoError || !document.isObject()) {
			showInputTapeWarning(this, tr("Invalid input tape JSON: %1").arg(parseError.errorString()));
			continue;
		}

		QJsonObject root = document.object();
		QString errorMessage;
		if (!normalizeEditedTapeJson(&root, &errorMessage) || !loadTapeFromJson(root, &errorMessage)) {
			showInputTapeWarning(this, errorMessage);
			continue;
		}

		setStatus(tr("Edited input tape. Use Save to write the plaintext changes to disk."));
		refreshUi();
		break;
	}
}

void InputTapeView::startReplay() {
	if (m_recording || m_replaying || m_runs.isEmpty()) {
		return;
	}
	if (m_controller.expired()) {
		showInputTapeWarning(this, tr("Load a game before replaying an input tape."));
		return;
	}
	pauseCoreAtBoundary();
	m_replaying = true;
	m_replayRunIndex = 0;
	m_replayFramesRemaining = 0;
	m_replayMask = 0;
	m_replayedFrames = 0;
	setReplayOverride(0);
	setStatus(tr("Replaying input tape with live frontend input suppressed."));
	refreshUi();
	QTimer::singleShot(0, this, &InputTapeView::advanceReplayFrame);
}

void InputTapeView::stopReplay() {
	if (!m_replaying && !m_replayOverrideActive) {
		return;
	}
	finishReplay(false);
}

void InputTapeView::advanceReplayFrame() {
	if (!m_replaying) {
		return;
	}
	auto controller = m_controller.lock();
	if (!controller) {
		finishReplay(false);
		return;
	}
	if (m_replayFramesRemaining <= 0) {
		if (m_replayRunIndex >= m_runs.size()) {
			finishReplay(true);
			return;
		}
		const InputRun& run = m_runs.at(m_replayRunIndex++);
		m_replayMask = run.mask & GBA_KNOWN_BUTTON_MASK;
		m_replayFramesRemaining = run.frames;
	}

	setReplayOverride(m_replayMask);
	--m_replayFramesRemaining;
	++m_replayedFrames;
	controller->frameAdvance();
	if (m_replayedFrames % INPUT_TAPE_REPLAY_STATUS_FRAME_STEP == 0 || m_replayedFrames == frameCount()) {
		// Replays should spend time advancing the core, not repainting the
		// control panel. Status is throttled but still updated at completion.
		setStatus(tr("Replaying: %1 / %2 frame(s)").arg(m_replayedFrames).arg(frameCount()));
	}
}

void InputTapeView::frameAdvance() {
	if (auto controller = m_controller.lock()) {
		controller->frameAdvance();
	}
}

void InputTapeView::onFrameAvailable() {
	if (m_recording) {
		appendFrame(sampleRecordMask());
		setStatus(tr("Recording: %1 frame(s), %2 run(s)").arg(frameCount()).arg(m_runs.size()));
		refreshUi();
		return;
	}
	if (m_replaying) {
		QTimer::singleShot(0, this, &InputTapeView::advanceReplayFrame);
	}
}

void InputTapeView::finishReplay(bool completed) {
	const uint64_t replayedFrames = m_replayedFrames;
	m_replaying = false;
	m_replayRunIndex = 0;
	m_replayFramesRemaining = 0;
	m_replayMask = 0;
	m_replayedFrames = 0;
	clearReplayOverride();
	pauseCoreAtBoundary();
	setStatus(completed
		? tr("Replay finished: %1 frame(s).").arg(replayedFrames)
		: tr("Replay stopped after %1 frame(s).").arg(replayedFrames));
	refreshUi();
}

void InputTapeView::pauseCoreAtBoundary() {
	if (auto controller = m_controller.lock()) {
		if (!controller->isPaused()) {
			controller->setPaused(true);
		}
	}
}

void InputTapeView::setReplayOverride(uint32_t mask) {
	if (auto controller = m_controller.lock()) {
		controller->setInputTapeOverride(true, mask & GBA_KNOWN_BUTTON_MASK);
		m_replayOverrideActive = true;
	}
}

void InputTapeView::clearReplayOverride() {
	if (auto controller = m_controller.lock()) {
		controller->setInputTapeOverride(false, 0);
	}
	m_replayOverrideActive = false;
}

QJsonObject InputTapeView::tapeToJson() const {
	QJsonObject root;
	root.insert(QStringLiteral("format"), QString::fromLatin1(INPUT_TAPE_JSON_FORMAT));
	root.insert(QStringLiteral("frame_count"), QString::number(frameCount()));

	QJsonObject buttonBits;
	for (const ButtonInfo& button : BUTTONS) {
		buttonBits.insert(QString::fromLatin1(button.name), button.key);
	}
	root.insert(QStringLiteral("button_bits"), buttonBits);

	QJsonObject metadata;
	metadata.insert(QStringLiteral("created_by"), QStringLiteral("native-qt-input-tape-view"));
	metadata.insert(QStringLiteral("created_at_utc"), QDateTime::currentDateTimeUtc().toString(Qt::ISODate));
	metadata.insert(QStringLiteral("format_note"), QStringLiteral("Anchor-agnostic tape: no ROM, save, or savestate path is stored."));
	root.insert(QStringLiteral("metadata"), metadata);

	root.insert(QStringLiteral("start_probe"), m_startProbe);
	root.insert(QStringLiteral("end_probe"), m_endProbe);

	QJsonArray runs;
	for (const InputRun& run : m_runs) {
		QJsonObject item;
		item.insert(QStringLiteral("mask"), formatMask(run.mask));
		QJsonArray buttons;
		for (const QString& name : buttonNames(run.mask)) {
			buttons.append(name);
		}
		item.insert(QStringLiteral("buttons"), buttons);
		item.insert(QStringLiteral("frames"), run.frames);
		runs.append(item);
	}
	root.insert(QStringLiteral("runs"), runs);
	return root;
}

bool InputTapeView::loadTapeFromJson(const QJsonObject& root, QString* errorMessage) {
	if (root.value(QStringLiteral("format")).toString() != QString::fromLatin1(INPUT_TAPE_JSON_FORMAT)) {
		if (errorMessage) {
			*errorMessage = tr("Unsupported input tape format.");
		}
		return false;
	}
	const QJsonArray runs = root.value(QStringLiteral("runs")).toArray();
	if (runs.isEmpty()) {
		if (errorMessage) {
			*errorMessage = tr("Input tape has no runs.");
		}
		return false;
	}

	QVector<InputRun> parsedRuns;
	uint64_t parsedFrameCount = 0;
	for (const QJsonValue& value : runs) {
		const QJsonObject item = value.toObject();
		uint32_t mask = 0;
		QString parseError;
		if (item.contains(QStringLiteral("mask"))) {
			if (!parseMask(item.value(QStringLiteral("mask")), &mask, &parseError)) {
				if (errorMessage) {
					*errorMessage = parseError;
				}
				return false;
			}
		} else if (!parseButtons(item.value(QStringLiteral("buttons")), &mask, &parseError)) {
			if (errorMessage) {
				*errorMessage = parseError;
			}
			return false;
		}

		int frames = 0;
		if (!parseFrames(item.value(QStringLiteral("frames")), &frames, errorMessage)) {
			return false;
		}
		parsedRuns.append(InputRun{ mask & GBA_KNOWN_BUTTON_MASK, frames });
		parsedFrameCount += uint64_t(frames);
	}

	const QJsonValue expectedFrames = root.value(QStringLiteral("frame_count"));
	if (!expectedFrames.isUndefined()) {
		const uint64_t expected = expectedFrames.isString()
			? expectedFrames.toString().toULongLong()
			: uint64_t(expectedFrames.toDouble());
		if (expected != parsedFrameCount) {
			if (errorMessage) {
				*errorMessage = tr("Input tape frame_count mismatch: header=%1 actual=%2.")
					.arg(expected)
					.arg(parsedFrameCount);
			}
			return false;
		}
	}

	m_runs = parsedRuns;
	m_totalFrames = parsedFrameCount;
	markTapePreviewDirty();
	m_startProbe = root.value(QStringLiteral("start_probe")).toObject();
	m_endProbe = root.value(QStringLiteral("end_probe")).toObject();
	return true;
}

QString InputTapeView::formatMask(uint32_t mask) {
	return QStringLiteral("0x%1").arg(mask, 8, 16, QLatin1Char('0')).toUpper();
}

QStringList InputTapeView::buttonNames(uint32_t mask) {
	QStringList names;
	for (const ButtonInfo& button : BUTTONS) {
		if (mask & (1u << button.key)) {
			names.append(QString::fromLatin1(button.name));
		}
	}
	if (names.isEmpty()) {
		names.append(QStringLiteral("NONE"));
	}
	return names;
}

QString InputTapeView::describeMask(uint32_t mask) {
	const QStringList names = buttonNames(mask);
	if (names.size() == 1 && names.first() == QStringLiteral("NONE")) {
		return QStringLiteral("None (%1)").arg(formatMask(mask));
	}
	return QStringLiteral("%1 (%2)").arg(names.join(QStringLiteral(" + "))).arg(formatMask(mask));
}

bool InputTapeView::parseMask(const QJsonValue& value, uint32_t* mask, QString* errorMessage) {
	bool ok = false;
	uint parsed = 0;
	if (value.isString()) {
		QString text = value.toString().trimmed();
		int base = 10;
		if (text.startsWith(QStringLiteral("0x"), Qt::CaseInsensitive)) {
			text = text.mid(2);
			base = 16;
		}
		parsed = text.toUInt(&ok, base);
	} else if (value.isDouble()) {
		const double number = value.toDouble();
		if (number >= 0 && number <= 0xFFFFFFFFu) {
			parsed = uint(number);
			ok = true;
		}
	}
	if (!ok) {
		if (errorMessage) {
			*errorMessage = QObject::tr("Could not parse input tape mask.");
		}
		return false;
	}
	if (mask) {
		*mask = uint32_t(parsed);
	}
	return true;
}

bool InputTapeView::parseButtons(const QJsonValue& value, uint32_t* mask, QString* errorMessage) {
	QJsonArray array;
	if (value.isArray()) {
		array = value.toArray();
	} else if (value.isString()) {
		for (const QString& part : value.toString().split(QLatin1Char('+'), Qt::SkipEmptyParts)) {
			array.append(part.trimmed());
		}
	}

	if (array.isEmpty()) {
		if (mask) {
			*mask = 0;
		}
		return true;
	}

	uint32_t parsed = 0;
	for (const QJsonValue& raw : array) {
		const QString name = raw.toString().trimmed().toUpper();
		if (name == QStringLiteral("NONE") || name == QStringLiteral("NEUTRAL")) {
			continue;
		}
		bool found = false;
		for (const ButtonInfo& button : BUTTONS) {
			if (name == QString::fromLatin1(button.name)) {
				parsed |= 1u << button.key;
				found = true;
				break;
			}
		}
		if (!found) {
			if (errorMessage) {
				*errorMessage = QObject::tr("Unknown input tape button: %1").arg(name);
			}
			return false;
		}
	}
	if (mask) {
		*mask = parsed;
	}
	return true;
}

bool InputTapeView::parseFrames(const QJsonValue& value, int* frames, QString* errorMessage) {
	bool ok = false;
	int parsed = 0;
	if (value.isString()) {
		parsed = value.toString().trimmed().toInt(&ok, 10);
	} else if (value.isDouble()) {
		const double number = value.toDouble();
		if (number >= 1 && number <= double(std::numeric_limits<int>::max())) {
			const int asInt = int(number);
			if (number != double(asInt)) {
				ok = false;
			} else {
				parsed = asInt;
				ok = true;
			}
		}
	}

	if (!ok || !validRun(parsed)) {
		if (errorMessage) {
			*errorMessage = QObject::tr("Every input tape run must have a positive integer frame count.");
		}
		return false;
	}
	if (frames) {
		*frames = parsed;
	}
	return true;
}

bool InputTapeView::normalizeEditedTapeJson(QJsonObject* root, QString* errorMessage) {
	if (!root) {
		if (errorMessage) {
			*errorMessage = QObject::tr("No input tape JSON was provided.");
		}
		return false;
	}

	const QJsonArray runs = root->value(QStringLiteral("runs")).toArray();
	if (runs.isEmpty()) {
		if (errorMessage) {
			*errorMessage = QObject::tr("Input tape has no runs.");
		}
		return false;
	}

	uint64_t totalFrames = 0;
	for (const QJsonValue& value : runs) {
		if (!value.isObject()) {
			if (errorMessage) {
				*errorMessage = QObject::tr("Every input tape run must be a JSON object.");
			}
			return false;
		}
		int frames = 0;
		if (!parseFrames(value.toObject().value(QStringLiteral("frames")), &frames, errorMessage)) {
			return false;
		}
		totalFrames += uint64_t(frames);
	}

	// Manual edits usually change run lengths. Recalculate the header before
	// handing the JSON to the strict loader so users can edit the frames in one
	// place without creating a false frame_count mismatch.
	root->insert(QStringLiteral("frame_count"), QString::number(totalFrames));
	return true;
}
