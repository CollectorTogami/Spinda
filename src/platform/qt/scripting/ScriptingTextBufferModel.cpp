/* Copyright (c) 2013-2022 Jeffrey Pfau
 *
 * Original mGBA source is credited to Jeffrey Pfau and contributors.
 * Local custom modifications in this fork were added for this workspace and
 * are not upstream mGBA work or authored by Jeffrey Pfau.
 *
 * This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */
#include "ScriptingTextBufferModel.h"

#include "ScriptingTextBuffer.h"

#include <QTextDocument>

using namespace QGBA;

ScriptingTextBufferModel::ScriptingTextBufferModel(QObject* parent)
	: QAbstractListModel(parent)
{
	// initializers only
}

void ScriptingTextBufferModel::attachToContext(mScriptContext* context)
{
	mScriptContextSetTextBufferFactory(context, &ScriptingTextBufferModel::createTextBuffer, this);
}

ScriptingTextBuffer* ScriptingTextBufferModel::ensureNamedBuffer(const QString& name) {
	for (ScriptingTextBuffer* buffer : m_buffers) {
		if (buffer->document()->metaInformation(QTextDocument::DocumentTitle) == name) {
			return buffer;
		}
	}
	return createManagedBuffer(name);
}

void ScriptingTextBufferModel::reset() {
	beginResetModel();
	QList<ScriptingTextBuffer*> toDelete = m_buffers;
	m_buffers.clear();
	endResetModel();
	for (ScriptingTextBuffer* buffer : toDelete) {
		delete buffer;
	}
}

mScriptTextBuffer* ScriptingTextBufferModel::createTextBuffer(void* context) {
	ScriptingTextBufferModel* self = static_cast<ScriptingTextBufferModel*>(context);
	return self->createManagedBuffer()->textBuffer();
}

ScriptingTextBuffer* ScriptingTextBufferModel::createManagedBuffer(const QString& name) {
	beginInsertRows(QModelIndex(), m_buffers.size(), m_buffers.size());
	ScriptingTextBuffer* buffer = new ScriptingTextBuffer;
	if (buffer->thread() != thread()) {
		buffer->moveToThread(thread());
	}
	buffer->setParent(this);
	QObject::connect(buffer, &ScriptingTextBuffer::bufferNameChanged, this, &ScriptingTextBufferModel::bufferNameChanged);
	if (!name.isEmpty()) {
		buffer->setBufferName(name);
	}
	m_buffers.append(buffer);
	emit textBufferCreated(buffer);
	endInsertRows();
	return buffer;
}

void ScriptingTextBufferModel::bufferNameChanged(const QString&) {
	ScriptingTextBuffer* buffer = qobject_cast<ScriptingTextBuffer*>(sender());
	int row = m_buffers.indexOf(buffer);
	if (row < 0) {
		return;
	}
	QModelIndex idx = index(row, 0);
	emit dataChanged(idx, idx, { Qt::DisplayRole });
}

int ScriptingTextBufferModel::rowCount(const QModelIndex& parent) const {
	if (parent.isValid()) {
		return 0;
	}
	return m_buffers.size();
}

QVariant ScriptingTextBufferModel::data(const QModelIndex& index, int role) const {
	if (index.parent().isValid() || index.row() < 0 || index.row() >= m_buffers.size() || index.column() != 0) {
		return QVariant();
	}
	if (role == Qt::DisplayRole) {
		return m_buffers[index.row()]->document()->metaInformation(QTextDocument::DocumentTitle);
	} else if (role == ScriptingTextBufferModel::DocumentRole) {
		return QVariant::fromValue<QTextDocument*>(m_buffers[index.row()]->document());
	}
	return QVariant();
}
