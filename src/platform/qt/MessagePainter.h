/* Copyright (c) 2013-2015 Jeffrey Pfau
 *
 * Original mGBA source is credited to Jeffrey Pfau and contributors.
 * Local custom modifications in this fork were added for this workspace and
 * are not upstream mGBA work or authored by Jeffrey Pfau.
 *
 * This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */
#pragma once

#include <QMutex>
#include <QObject>
#include <QPixmap>
#include <QStaticText>
#include <QTimer>

#include <mgba/core/core.h>

namespace QGBA {

class MessagePainter : public QObject {
Q_OBJECT

public:
	MessagePainter(QObject* parent = nullptr);

	void resize(const QSize& size, qreal scaleFactor);
	void paint(QPainter* painter);
	void setScaleFactor(qreal factor);

public slots:
	void showMessage(const QString& message);
	void clearMessage();

	void showFrameCounter(uint64_t);
	void clearFrameCounter();
	void showInputDisplay(uint32_t keys, mPlatform platform);
	void clearInputDisplay();

private:
	void redraw();

	QMutex m_mutex;
	QStaticText m_message;
	qreal m_scaleFactor = 1;
	uint64_t m_frameCounter;
	bool m_drawFrameCounter = false;
	uint32_t m_inputKeys = 0;
	mPlatform m_inputPlatform = mPLATFORM_NONE;
	bool m_drawInputDisplay = false;

	QPoint m_local;
	QPixmap m_pixmap;
	QPixmap m_pixmapBuffer;

	QPointF m_framePoint = QPointF(0, 0);
	QPointF m_inputPoint = QPointF(0, 0);
	QFont m_frameFont;

	QTimer m_messageTimer{this};
	QTransform m_world;
	QFont m_messageFont;
};

}
