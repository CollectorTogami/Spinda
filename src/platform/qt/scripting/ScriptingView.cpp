/* Copyright (c) 2013-2022 Jeffrey Pfau
 *
 * Original mGBA source is credited to Jeffrey Pfau and contributors.
 * Local custom modifications in this fork were added for this workspace and
 * are not upstream mGBA work or authored by Jeffrey Pfau.
 *
 * This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */
#include "scripting/ScriptingView.h"

#include "GBAApp.h"
#include "ConfigController.h"
#include "scripting/ScriptingController.h"
#include "scripting/ScriptingTextBuffer.h"
#include "scripting/ScriptingTextBufferModel.h"

#include <QTimer>

using namespace QGBA;

namespace {

QString displayLanguageName(const QString& language) {
	if (language.isEmpty()) {
		return language;
	}
	QString display = language;
	display[0] = display[0].toUpper();
	return display;
}

constexpr const char* kPythonLanguage = "python";

}

ScriptingView::ScriptingView(ScriptingController* controller, ConfigController* config, QWidget* parent)
	: QMainWindow(parent)
	, m_config(config)
	, m_controller(controller)
{
	m_ui.setupUi(this);

	ScriptingTextBufferModel* bufferModel = controller->textBufferModel();
	m_ui.prompt->setFont(GBAApp::app()->monospaceFont());
	m_ui.log->setNewlineTerminated(true);
	m_ui.buffers->setModel(bufferModel);

	connect(m_ui.prompt, &QLineEdit::returnPressed, this, &ScriptingView::submitRepl);
	connect(m_ui.runButton, &QAbstractButton::clicked, this, &ScriptingView::submitRepl);

	connect(bufferModel, &QAbstractItemModel::modelAboutToBeReset, this, &ScriptingView::controllerReset);
	connect(bufferModel, &QAbstractItemModel::rowsInserted, this, [this, bufferModel](const QModelIndex&, int row, int) {
		m_ui.buffers->setCurrentIndex(bufferModel->index(row, 0));
	});

	connect(m_controller, &ScriptingController::log, m_ui.log, &LogWidget::log);
	connect(m_controller, &ScriptingController::warn, m_ui.log, &LogWidget::warn);
	connect(m_controller, &ScriptingController::error, m_ui.log, &LogWidget::error);
	connect(m_controller, &ScriptingController::availableLanguagesChanged, this, &ScriptingView::refreshLanguages);
	connect(m_controller, &ScriptingController::activeLanguageChanged, this, &ScriptingView::syncActiveLanguage);

	connect(m_ui.buffers->selectionModel(), &QItemSelectionModel::currentChanged, this, &ScriptingView::selectBuffer);
	connect(m_ui.load, &QAction::triggered, this, &ScriptingView::load);
	connect(m_ui.reset, &QAction::triggered, controller, &ScriptingController::reset);
	connect(m_ui.language, &QComboBox::currentTextChanged, this, [this](const QString&) {
		m_controller->setActiveLanguage(m_ui.language->currentData().toString());
	});

	m_mruFiles = m_config->getMRU(ConfigController::MRU::Script);
	updateMRU();

	m_blankDocument = new QTextDocument(this);
	m_blankDocument->setDocumentLayout(new QPlainTextDocumentLayout(m_blankDocument));

	refreshLanguages(m_controller->availableLanguages());
	syncActiveLanguage(m_controller->activeLanguage());
	m_ui.buffers->setCurrentIndex(bufferModel->index(0, 0));

	if (qEnvironmentVariableIsSet("MGBA_QT_SCRIPTING_AUTOTEST_LANGUAGE")
	 || qEnvironmentVariableIsSet("MGBA_QT_SCRIPTING_AUTOTEST_LOAD")
	 || qEnvironmentVariableIsSet("MGBA_QT_SCRIPTING_AUTOTEST_PROMPT")
	 || qEnvironmentVariableIsSet("MGBA_QT_SCRIPTING_AUTOTEST_RESET")
	 || qEnvironmentVariableIsSet("MGBA_QT_SCRIPTING_AUTOTEST_PROMPT_AFTER_RESET")) {
		// Local regression hook: let the deployment tests drive the real
		// scripting window without adding a separate test-only frontend.
		QTimer::singleShot(0, this, [this]() {
			const QString language = qEnvironmentVariable("MGBA_QT_SCRIPTING_AUTOTEST_LANGUAGE");
			const QString loadPath = qEnvironmentVariable("MGBA_QT_SCRIPTING_AUTOTEST_LOAD");
			const QString prompt = qEnvironmentVariable("MGBA_QT_SCRIPTING_AUTOTEST_PROMPT");
			const bool shouldReset = qEnvironmentVariableIntValue("MGBA_QT_SCRIPTING_AUTOTEST_RESET") != 0;
			const QString promptAfterReset = qEnvironmentVariable("MGBA_QT_SCRIPTING_AUTOTEST_PROMPT_AFTER_RESET");
			if (!language.isEmpty()) {
				m_controller->setActiveLanguage(language);
			}
			if (!loadPath.isEmpty()) {
				m_controller->loadFile(loadPath);
			}
			if (!prompt.isEmpty()) {
				m_ui.prompt->setText(prompt);
				submitRepl();
			}
			if (shouldReset) {
				// Keep the scripted-window regression path close to the real UI:
				// reset through the controller, then optionally run another prompt
				// in the fresh session. This lets the deployment tests verify that
				// Python resets behave like Lua resets instead of reusing stale
				// globals from the previous file load.
				m_controller->reset();
			}
			if (!promptAfterReset.isEmpty()) {
				m_ui.prompt->setText(promptAfterReset);
				submitRepl();
			}
		});
	}
}

void ScriptingView::submitRepl() {
	m_ui.log->echo(m_ui.prompt->text());
	m_controller->runCode(m_ui.prompt->text());
	m_ui.prompt->clear();
}

void ScriptingView::load() {
	QString filename = GBAApp::app()->getOpenFileName(this, tr("Select script to load"), getFilters());
	if (!filename.isEmpty()) {
		if (!m_controller->loadFile(filename)) {
			return;
		}
		appendMRU(filename);
	}
}

void ScriptingView::syncActiveLanguage(const QString& language) {
	for (int i = 0; i < m_ui.language->count(); ++i) {
		if (m_ui.language->itemData(i).toString() == language) {
			m_ui.language->setCurrentIndex(i);
			return;
		}
	}
}

void ScriptingView::refreshLanguages(const QStringList& languages) {
	const QString previous = m_ui.language->currentData().toString();
	m_ui.language->blockSignals(true);
	m_ui.language->clear();
	for (const QString& language : languages) {
		m_ui.language->addItem(displayLanguageName(language), language);
	}
	m_ui.language->blockSignals(false);
	// The scripting loader auto-selects the engine based on file extension,
	// so forcing a manual language choice is redundant and confusing.
	// Keep the selector hidden even when multiple languages are available.
	m_ui.language->setVisible(false);
	m_ui.language->setEnabled(false);
	if (!previous.isEmpty()) {
		syncActiveLanguage(previous);
	}
}

void ScriptingView::controllerReset() {
	selectBuffer(QModelIndex());
}

void ScriptingView::selectBuffer(const QModelIndex& current, const QModelIndex&) {
	if (current.isValid()) {
		m_ui.buffer->setDocument(current.data(ScriptingTextBufferModel::DocumentRole).value<QTextDocument*>());
	} else {
		// If there is no selected buffer, use the blank document.
		m_ui.buffer->setDocument(m_blankDocument);
	}
}

QString ScriptingView::getFilters() const {
	QStringList filters;
	QStringList extensions;
	const QStringList languages = m_controller ? m_controller->availableLanguages() : QStringList();
	for (const QString& language : languages) {
		if (language == QLatin1String("lua")) {
			extensions.append(QStringLiteral("*.lua"));
		} else if (language == QLatin1String(kPythonLanguage)) {
			extensions.append(QStringLiteral("*.py"));
		}
	}
	if (!extensions.isEmpty()) {
		filters.append(tr("Scripts (%1)").arg(extensions.join(QLatin1String(" "))));
	}
	filters.append(tr("All files (*.*)"));
	return filters.join(";;");
}

void ScriptingView::appendMRU(const QString& fname) {
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

void ScriptingView::updateMRU() {
	m_config->setMRU(m_mruFiles, ConfigController::MRU::Script);
	m_ui.mru->clear();
	for (const auto& fname : m_mruFiles) {
		m_ui.mru->addAction(fname, [this, fname]() {
			m_controller->loadFile(fname);
		});
	}
}
