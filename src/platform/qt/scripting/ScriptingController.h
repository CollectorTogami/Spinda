/* Copyright (c) 2013-2022 Jeffrey Pfau
 *
 * Original mGBA source is credited to Jeffrey Pfau and contributors.
 * Local custom modifications in this fork were added for this workspace and
 * are not upstream mGBA work or authored by Jeffrey Pfau.
 *
 * This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */
#pragma once

#include <QHash>
#include <QObject>
#include <QSize>
#include <QStringList>

#include <mgba/script/context.h>
#include <mgba/core/scripting.h>

#include "VFileDevice.h"
#include "CoreController.h"

#include <functional>
#include <memory>

class QTextDocument;

namespace QGBA {

class CoreController;
class ScriptingTextBuffer;
class ScriptingTextBufferModel;

class ScriptingController : public QObject {
Q_OBJECT

public:
	ScriptingController(QObject* parent = nullptr);
	~ScriptingController();

	void setController(std::shared_ptr<CoreController> controller);
	QStringList availableLanguages() const;
	QString activeLanguage() const;
	bool requestAbortPythonScript();
	bool pythonAbortRequested() const;
	bool pythonScriptActive() const;
	void beginPythonScript();
	void finishPythonScript();
#ifdef ENABLE_PYTHON
	void setNextPythonSessionSkipsCoreInterrupt(bool skip);
#endif

	bool loadFile(const QString& path);
	bool load(VFileDevice& vf, const QString& name);

	mScriptContext* context() { return &m_scriptContext; }
	ScriptingTextBufferModel* textBufferModel() const { return m_bufferModel; }

signals:
	void log(const QString&);
	void warn(const QString&);
	void error(const QString&);
	void textBufferCreated(ScriptingTextBuffer*);
	void availableLanguagesChanged(const QStringList&);
	void activeLanguageChanged(const QString&);

public slots:
	void clearController();
	void reset();
	void runCode(const QString& code);
	void setActiveLanguage(const QString& language);
#ifdef ENABLE_PYTHON
	void appendPythonConsole(const QString& text);
	void setPythonTextBuffer(const QString& name, const QString& text, const QSize& size);
#endif

private:
	void init();
	void updateLanguageState(const QString& preferredLanguage = QString());
	mScriptEngineContext* engineForLanguage(const QString& language) const;

	static mScriptTextBuffer* createTextBuffer(void* context);

	struct Logger : mLogger {
		ScriptingController* p;
	} m_logger{};

	mScriptContext m_scriptContext;

	mScriptEngineContext* m_activeEngine = nullptr;
	QHash<QString, mScriptEngineContext*> m_engines;
	ScriptingTextBufferModel* m_bufferModel;
	QString m_activeLanguage;

	std::shared_ptr<CoreController> m_controller;
	bool m_controllerStopping = false;
	bool m_pythonScriptActive = false;
	bool m_pythonAbortRequested = false;
#ifdef ENABLE_PYTHON
	bool loadPythonFile(VFileDevice& vf, const QString& name);
	bool runPythonPrompt(const QString& code);
	bool runPythonSession(const QString& traceName, const std::function<bool()>& action);
	ScriptingTextBuffer* ensurePythonConsoleBuffer();
	void clearPythonConsole();

	ScriptingTextBuffer* m_pythonConsoleBuffer = nullptr;
	bool m_skipNextPythonSessionCoreInterrupt = false;
#endif
};

}
