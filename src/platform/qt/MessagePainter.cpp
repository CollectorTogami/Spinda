/* Copyright (c) 2013-2015 Jeffrey Pfau
 *
 * Original mGBA source is credited to Jeffrey Pfau and contributors.
 * Local custom modifications in this fork were added for this workspace and
 * are not upstream mGBA work or authored by Jeffrey Pfau.
 *
 * This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */
#include "MessagePainter.h"

#include "GBAApp.h"

#include <QPainter>
#include <QVector>

#include <mgba/gba/interface.h>
#include <mgba/internal/gb/input.h>
#include <mgba/internal/gba/input.h>

using namespace QGBA;

namespace {

struct InputLabel {
	const char* label;
	uint32_t mask;
};

int textAdvance(const QFontMetrics& metrics, const QString& text) {
#if (QT_VERSION >= QT_VERSION_CHECK(5, 11, 0))
	return metrics.horizontalAdvance(text);
#else
	return metrics.width(text);
#endif
}

struct InputRows {
	const InputLabel* top = nullptr;
	qsizetype topCount = 0;
	const InputLabel* bottom = nullptr;
	qsizetype bottomCount = 0;
};

const InputLabel GBA_TOP_ROW[] = {
	{ "Up", 1u << GBA_KEY_UP },
	{ "Dn", 1u << GBA_KEY_DOWN },
	{ "Lt", 1u << GBA_KEY_LEFT },
	{ "Rt", 1u << GBA_KEY_RIGHT },
};

const InputLabel GBA_BOTTOM_ROW[] = {
	{ "A", 1u << GBA_KEY_A },
	{ "B", 1u << GBA_KEY_B },
	{ "Sel", 1u << GBA_KEY_SELECT },
	{ "Sta", 1u << GBA_KEY_START },
	{ "L", 1u << GBA_KEY_L },
	{ "R", 1u << GBA_KEY_R },
};

const InputLabel GB_TOP_ROW[] = {
	{ "Up", 1u << GB_KEY_UP },
	{ "Dn", 1u << GB_KEY_DOWN },
	{ "Lt", 1u << GB_KEY_LEFT },
	{ "Rt", 1u << GB_KEY_RIGHT },
};

const InputLabel GB_BOTTOM_ROW[] = {
	{ "A", 1u << GB_KEY_A },
	{ "B", 1u << GB_KEY_B },
	{ "Sel", 1u << GB_KEY_SELECT },
	{ "Sta", 1u << GB_KEY_START },
};

InputRows inputRowsForPlatform(mPlatform platform) {
	switch (platform) {
	case mPLATFORM_GBA:
		// Match the classic TAS-style viewer idea: directions grouped separately
		// from the action buttons so a whole frame's input state is easy to scan.
		return {GBA_TOP_ROW, static_cast<qsizetype>(sizeof(GBA_TOP_ROW) / sizeof(GBA_TOP_ROW[0])), GBA_BOTTOM_ROW, static_cast<qsizetype>(sizeof(GBA_BOTTOM_ROW) / sizeof(GBA_BOTTOM_ROW[0]))};
	case mPLATFORM_GB:
		return {GB_TOP_ROW, static_cast<qsizetype>(sizeof(GB_TOP_ROW) / sizeof(GB_TOP_ROW[0])), GB_BOTTOM_ROW, static_cast<qsizetype>(sizeof(GB_BOTTOM_ROW) / sizeof(GB_BOTTOM_ROW[0]))};
	case mPLATFORM_NONE:
	default:
		return {};
	}
}

void drawRealtimeOverlayText(QPainter* painter, const QPointF& point, const QString& text, const QColor& color) {
	// The message OSD is cached in a pixmap, but the frame counter and input
	// viewer redraw every frame. Use a smaller fixed shadow kernel here instead
	// of the heavier 11-step radial outline to keep the always-on overlays cheap.
	static const QPointF OUTLINE_OFFSETS[] = {
		QPointF(-0.7, 0.0),
		QPointF(0.7, 0.0),
		QPointF(0.0, -0.7),
		QPointF(0.0, 0.7),
	};
	painter->setPen(Qt::black);
	for (const QPointF& offset : OUTLINE_OFFSETS) {
		painter->save();
		painter->translate(offset);
		painter->drawText(point, text);
		painter->restore();
	}
	painter->setPen(color);
	painter->drawText(point, text);
}

void drawInputRow(QPainter* painter, const QFontMetrics& metrics, const QPointF& start, const InputLabel* row, qsizetype count, uint32_t keys, const QColor& inactiveColor, int gap) {
	qreal x = start.x();
	for (qsizetype i = 0; i < count; ++i) {
		const QString label = QString::fromLatin1(row[i].label);
		drawRealtimeOverlayText(painter, QPointF(x, start.y()), label, (keys & row[i].mask) ? Qt::white : inactiveColor);
		x += textAdvance(metrics, label) + gap;
	}
}

}

MessagePainter::MessagePainter(QObject* parent)
	: QObject(parent)
{
	m_messageFont = GBAApp::app()->monospaceFont();
	m_messageFont.setPixelSize(13);
	m_frameFont = GBAApp::app()->monospaceFont();
	m_frameFont.setPixelSize(10);
	connect(&m_messageTimer, &QTimer::timeout, this, &MessagePainter::clearMessage);
	m_messageTimer.setSingleShot(true);
	m_messageTimer.setInterval(5000);

	clearMessage();
}

void MessagePainter::resize(const QSize& size, qreal scaleFactor) {
	double drawW = size.width();
	double drawH = size.height();
	double area = pow(drawW * drawW * drawW * drawH * drawH, 0.185);
	m_scaleFactor = scaleFactor;
	m_world.reset();
	m_world.scale(area / 170., area / 170.);
	m_local = QPoint(area / 80., drawH - m_messageFont.pixelSize() * m_world.m22() * 1.3);

	QFontMetrics metrics(m_frameFont);
	m_framePoint = QPoint(drawW / m_world.m11() - metrics.height() * 0.1, metrics.height() * 0.75);
	m_inputPoint = QPointF(metrics.height() * 0.4, metrics.height() * 0.75);

	m_mutex.lock();
	redraw();
	m_mutex.unlock();
}

void MessagePainter::redraw() {
	if (m_message.text().isEmpty()) {
		m_pixmapBuffer.fill(Qt::transparent);
		m_pixmap = m_pixmapBuffer;
		return;
	}
	m_message.prepare(m_world, m_messageFont);
	QSizeF sizef = m_message.size() * m_scaleFactor;
	m_pixmapBuffer = QPixmap(sizef.width() * m_world.m11(), sizef.height() * m_world.m22());
	m_pixmapBuffer.setDevicePixelRatio(m_scaleFactor);
	m_pixmapBuffer.fill(Qt::transparent);

	QPainter painter(&m_pixmapBuffer);
	painter.setWorldTransform(m_world);
	painter.setRenderHint(QPainter::Antialiasing);
	painter.setFont(m_messageFont);
	painter.setPen(Qt::black);
	const static int ITERATIONS = 11;
	for (int i = 0; i < ITERATIONS; ++i) {
		painter.save();
		painter.translate(cos(i * 2.0 * M_PI / ITERATIONS) * 0.8, sin(i * 2.0 * M_PI / ITERATIONS) * 0.8);
		painter.drawStaticText(0, 0, m_message);
		painter.restore();
	}
	painter.setPen(Qt::white);
	painter.drawStaticText(0, 0, m_message);
	m_pixmap = m_pixmapBuffer;
}

void MessagePainter::paint(QPainter* painter) {
	if (!m_message.text().isEmpty()) {
		painter->drawPixmap(m_local, m_pixmap);
	}
	if (m_drawFrameCounter) {
		QString frame(tr("Frame %1").arg(m_frameCounter));
		QFontMetrics metrics(m_frameFont);
		painter->setWorldTransform(m_world);
		painter->setRenderHint(QPainter::Antialiasing);
		painter->setFont(m_frameFont);
		painter->save();
		painter->translate(-textAdvance(metrics, frame), 0);
		drawRealtimeOverlayText(painter, m_framePoint, frame, Qt::white);
		painter->restore();
	}
	if (m_drawInputDisplay) {
		const InputRows rows = inputRowsForPlatform(m_inputPlatform);
		if (rows.top && rows.bottom) {
			QFontMetrics metrics(m_frameFont);
			const QColor inactiveColor(160, 160, 160);
			const int gap = textAdvance(metrics, QStringLiteral(" "));
			const qreal lineHeight = metrics.height() * 1.15;
			painter->setWorldTransform(m_world);
			painter->setRenderHint(QPainter::Antialiasing);
			painter->setFont(m_frameFont);
			drawInputRow(painter, metrics, m_inputPoint, rows.top, rows.topCount, m_inputKeys, inactiveColor, gap);
			drawInputRow(painter, metrics, QPointF(m_inputPoint.x(), m_inputPoint.y() + lineHeight), rows.bottom, rows.bottomCount, m_inputKeys, inactiveColor, gap);
		}
	}
}

void MessagePainter::showMessage(const QString& message) {
	m_mutex.lock();
	m_message.setText(message);
	redraw();
	m_mutex.unlock();
	m_messageTimer.stop();
	m_messageTimer.start();
}

void MessagePainter::clearMessage() {
	m_mutex.lock();
	m_message.setText(QString());
	redraw();
	m_mutex.unlock();
	m_messageTimer.stop();
}

void MessagePainter::showFrameCounter(uint64_t frameCounter) {
	m_mutex.lock();
	m_frameCounter = frameCounter;
	m_drawFrameCounter = true;
	m_mutex.unlock();
}

void MessagePainter::clearFrameCounter() {
	m_mutex.lock();
	m_drawFrameCounter = false;
	m_mutex.unlock();
}

void MessagePainter::showInputDisplay(uint32_t keys, mPlatform platform) {
	m_mutex.lock();
	if (m_drawInputDisplay && m_inputKeys == keys && m_inputPlatform == platform) {
		m_mutex.unlock();
		return;
	}
	m_inputKeys = keys;
	m_inputPlatform = platform;
	m_drawInputDisplay = true;
	m_mutex.unlock();
}

void MessagePainter::clearInputDisplay() {
	m_mutex.lock();
	m_drawInputDisplay = false;
	m_mutex.unlock();
}
