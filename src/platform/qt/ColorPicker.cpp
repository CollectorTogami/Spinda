/* Copyright (c) 2013-2017 Jeffrey Pfau
 *
 * Original mGBA source is credited to Jeffrey Pfau and contributors.
 * Local custom modifications in this fork were added for this workspace and
 * are not upstream mGBA work or authored by Jeffrey Pfau.
 *
 * This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */
#include "ColorPicker.h"

#include "GBAApp.h"

#include <QColorDialog>
#include <QEvent>

using namespace QGBA;

ColorPicker::ColorPicker() {
}

ColorPicker::ColorPicker(QWidget* parent, const QColor& defaultColor)
	: m_parent(parent)
{
	setColor(defaultColor);
	parent->installEventFilter(this);
}

ColorPicker& ColorPicker::operator=(const ColorPicker& other) {
	if (m_parent) {
		m_parent->removeEventFilter(this);
	}
	m_parent = other.m_parent;
	m_defaultColor = other.m_defaultColor;
	m_parent->installEventFilter(this);

	return *this;
}

void ColorPicker::setColor(const QColor& color) {
	m_defaultColor = color;
	m_parent->setStyleSheet(QString("background-color: %1;").arg(color.name()));
}

bool ColorPicker::eventFilter(QObject* obj, QEvent* event) {
	if (event->type() != QEvent::MouseButtonRelease) {
		return false;
	}
	if (obj != m_parent) {
		return false;
	}

	QWidget* swatch = static_cast<QWidget*>(obj);

	QColorDialog* colorPicker = new QColorDialog;
	colorPicker->setAttribute(Qt::WA_DeleteOnClose);
	// Dark mode uses the Qt dialog here on purpose so the picker follows the
	// same palette/style as the rest of the custom mGBA UI.
	colorPicker->setOption(QColorDialog::DontUseNativeDialog, GBAApp::app() && GBAApp::app()->darkMode());
	colorPicker->setCurrentColor(m_defaultColor);
	colorPicker->open();
	connect(colorPicker, &QColorDialog::colorSelected, [this, swatch](const QColor& color) {
		setColor(color);
		emit colorChanged(color);
	});
	return true;
}
