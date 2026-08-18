/* Copyright (c) 2013-2026 Jeffrey Pfau
 *
 * Original mGBA source is credited to Jeffrey Pfau and contributors.
 * Local custom modifications in this fork were added for this workspace and
 * are not upstream mGBA work or authored by Jeffrey Pfau.
 *
 * This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */
#include "WindowsDarkChrome.h"

#include "GBAApp.h"

#include <QWidget>

#ifdef Q_OS_WIN
#include <dwmapi.h>

#ifndef DWMWA_BORDER_COLOR
#define DWMWA_BORDER_COLOR 34
#endif
#ifndef DWMWA_CAPTION_COLOR
#define DWMWA_CAPTION_COLOR 35
#endif
#ifndef DWMWA_TEXT_COLOR
#define DWMWA_TEXT_COLOR 36
#endif
#ifndef DWMWA_WINDOW_CORNER_PREFERENCE
#define DWMWA_WINDOW_CORNER_PREFERENCE 33
#endif

namespace {

constexpr COLORREF DWM_DARK_CAPTION_COLOR = 0x00303030;
constexpr COLORREF DWM_DARK_TEXT_COLOR = 0x00F0F0F0;
constexpr COLORREF DWM_DARK_BORDER_COLOR = 0x00000000;
constexpr DWORD DWM_DARK_CORNER_PREFERENCE = 1;

}
#endif

void QGBA::applyWindowsDarkChrome(QWidget* widget) {
#ifndef Q_OS_WIN
	(void) widget;
#else
	if (!widget || !GBAApp::app() || !GBAApp::app()->darkMode()) {
		return;
	}

	HWND hwnd = reinterpret_cast<HWND>(widget->winId());
	if (!hwnd) {
		return;
	}

	BOOL useDark = TRUE;
	const DWORD immersiveDarkAttrs[] = { 20, 19 };
	for (DWORD attr : immersiveDarkAttrs) {
		if (SUCCEEDED(DwmSetWindowAttribute(hwnd, attr, &useDark, sizeof(useDark)))) {
			break;
		}
	}

	const COLORREF captionColor = DWM_DARK_CAPTION_COLOR;
	const COLORREF textColor = DWM_DARK_TEXT_COLOR;
	const COLORREF borderColor = DWM_DARK_BORDER_COLOR;
	DwmSetWindowAttribute(hwnd, DWMWA_CAPTION_COLOR, &captionColor, sizeof(captionColor));
	DwmSetWindowAttribute(hwnd, DWMWA_TEXT_COLOR, &textColor, sizeof(textColor));
	DwmSetWindowAttribute(hwnd, DWMWA_BORDER_COLOR, &borderColor, sizeof(borderColor));
	DwmSetWindowAttribute(hwnd, DWMWA_WINDOW_CORNER_PREFERENCE, &DWM_DARK_CORNER_PREFERENCE, sizeof(DWM_DARK_CORNER_PREFERENCE));
#endif
}
