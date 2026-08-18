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

#include <QAbstractListModel>

#include <mgba/script/context.h>

struct mScriptTextBuffer;

namespace QGBA {

class ScriptingTextBuffer;

class ScriptingTextBufferModel : public QAbstractListModel {
Q_OBJECT

public:
	enum ItemDataRole {
		DocumentRole = Qt::UserRole + 1,
	};

	ScriptingTextBufferModel(QObject* parent = nullptr);

	void attachToContext(mScriptContext* context);
	ScriptingTextBuffer* ensureNamedBuffer(const QString& name);

	int rowCount(const QModelIndex& parent = QModelIndex()) const;
	QVariant data(const QModelIndex& index, int role = Qt::DisplayRole) const;

signals:
	void textBufferCreated(ScriptingTextBuffer*);

public slots:
	void reset();

private slots:
	void bufferNameChanged(const QString&);

private:
	static mScriptTextBuffer* createTextBuffer(void* context);
	ScriptingTextBuffer* createManagedBuffer(const QString& name = QString());

	QList<ScriptingTextBuffer*> m_buffers;
};

}
