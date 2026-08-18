/* Copyright (c) 2013-2015 Jeffrey Pfau
 *
 * Original mGBA source is credited to Jeffrey Pfau and contributors.
 * Local custom modifications in this fork were added for this workspace and
 * are not upstream mGBA work or authored by Jeffrey Pfau.
 *
 * This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */
#include "Display.h"

#include "CoreController.h"
#include "ConfigController.h"
#include "DisplayGL.h"
#include "DisplayQt.h"
#include "LogController.h"

#include <mgba-util/vfs.h>

using namespace QGBA;

#if defined(BUILD_GL) || defined(BUILD_GLES2) || defined(BUILD_GLES3) || defined(USE_EPOXY)
QGBA::Display::Driver Display::s_driver = QGBA::Display::Driver::OPENGL;
#else
QGBA::Display::Driver Display::s_driver = QGBA::Display::Driver::QT;
#endif

QGBA::Display* QGBA::Display::create(QWidget* parent) {
#if defined(BUILD_GL) || defined(BUILD_GLES2) || defined(BUILD_GLES3) || defined(USE_EPOXY)
	QSurfaceFormat format;
	format.setSwapInterval(1);
	format.setSwapBehavior(QSurfaceFormat::DoubleBuffer);
#endif

	switch (s_driver) {
#if defined(BUILD_GL) || defined(BUILD_GLES2) || defined(BUILD_GLES3) || defined(USE_EPOXY)
	case Driver::OPENGL:
#if defined(BUILD_GLES2) || defined(BUILD_GLES3) || defined(USE_EPOXY)
		if (QOpenGLContext::openGLModuleType() == QOpenGLContext::LibGLES) {
			format.setVersion(2, 0);
		} else {
			format.setVersion(3, 3);
		}
		format.setProfile(QSurfaceFormat::CoreProfile);
		if (DisplayGL::supportsFormat(format)) {
			QSurfaceFormat::setDefaultFormat(format);
		} else {
#ifdef BUILD_GL
			LOG(QT, WARN) << ("Failed to create an OpenGL Core context, trying old-style...");
			format.setVersion(1, 4);
			format.setOption(QSurfaceFormat::DeprecatedFunctions);
			if (!DisplayGL::supportsFormat(format)) {
				return nullptr;
			}
#else
			return nullptr;
#endif
		}
		return new DisplayGL(format, parent);
#endif
#endif
#ifdef BUILD_GL
	case Driver::OPENGL1:
		format.setVersion(1, 4);
		if (!DisplayGL::supportsFormat(format)) {
			return nullptr;
		}
		return new DisplayGL(format, parent);
#endif

	case Driver::QT:
		return new DisplayQt(parent);

	default:
#if defined(BUILD_GL) || defined(BUILD_GLES2) || defined(BUILD_GLES3) || defined(USE_EPOXY)
		return new DisplayGL(format, parent);
#else
		return new DisplayQt(parent);
#endif
	}
}

QGBA::Display::Display(QWidget* parent)
	: QWidget(parent)
{
	setSizePolicy(QSizePolicy::MinimumExpanding, QSizePolicy::MinimumExpanding);
	connect(&m_mouseTimer, &QTimer::timeout, this, &Display::hideCursor);
	m_mouseTimer.setSingleShot(true);
	m_mouseTimer.setInterval(MOUSE_DISAPPEAR_TIMER);
	setMouseTracking(true);
}

void QGBA::Display::attach(std::shared_ptr<CoreController> controller) {
	CoreController* controllerP = controller.get();
	connect(controllerP, &CoreController::stateLoaded, this, &Display::resizeContext);
	connect(controllerP, &CoreController::stateLoaded, this, &Display::forceDraw);
	connect(controllerP, &CoreController::rewound, this, &Display::forceDraw);
	connect(controllerP, &CoreController::paused, this, &Display::pauseDrawing);
	connect(controllerP, &CoreController::unpaused, this, &Display::unpauseDrawing);
	connect(controllerP, &CoreController::frameAvailable, this, &Display::framePosted);
	connect(controllerP, &CoreController::frameAvailable, this, [controllerP, this]() {
		// Most frames do not need OSD work. Skip the painter updates entirely when
		// both persistent overlays are disabled so the GUI path stays lighter.
		if (!m_showFrameCounter && !m_showInputDisplay) {
			return;
		}
		if (m_showFrameCounter) {
			m_messagePainter.showFrameCounter(controllerP->frameCounter());
		}
		if (m_showInputDisplay) {
			// The controller caches the merged active key mask at frame boundaries
			// so the overlay can mirror the scripted/manual input stream without
			// touching the core from the UI thread.
			m_messagePainter.showInputDisplay(controllerP->currentKeys(), controllerP->platform());
		}
	});
	connect(controllerP, &CoreController::statusPosted, this, &Display::showMessage);
	connect(controllerP, &CoreController::didReset, this, &Display::resizeContext);
}

void QGBA::Display::configure(ConfigController* config) {
	const mCoreOptions* opts = config->options();
	lockAspectRatio(opts->lockAspectRatio);
	lockIntegerScaling(opts->lockIntegerScaling);
	interframeBlending(opts->interframeBlending);
	filter(opts->resampleVideo);
	config->updateOption("showOSD");
	config->updateOption("showFrameCounter");
	config->updateOption("showInputDisplay");
	config->updateOption("videoSync");
#if defined(BUILD_GL) || defined(BUILD_GLES2) || defined(BUILD_GLES3)
	if (opts->shader && supportsShaders()) {
		struct VDir* shader = VDirOpen(opts->shader);
		if (shader) {
			setShaders(shader);
			shader->close(shader);
		}
	}
#endif
}

void QGBA::Display::resizeEvent(QResizeEvent*) {
#if (QT_VERSION >= QT_VERSION_CHECK(5, 6, 0))
	m_messagePainter.resize(size(), devicePixelRatioF());
#else
	m_messagePainter.resize(size(), devicePixelRatio());
#endif
}

void QGBA::Display::lockAspectRatio(bool lock) {
	m_lockAspectRatio = lock;
}

void QGBA::Display::lockIntegerScaling(bool lock) {
	m_lockIntegerScaling = lock;
}

void QGBA::Display::interframeBlending(bool lock) {
	m_interframeBlending = lock;
}

void QGBA::Display::showOSDMessages(bool enable) {
	m_showOSD = enable;
}

void QGBA::Display::showFrameCounter(bool enable) {
	m_showFrameCounter = enable;
	if (!enable) {
		m_messagePainter.clearFrameCounter();
	}
}

void QGBA::Display::showInputDisplay(bool enable) {
	m_showInputDisplay = enable;
	if (!enable) {
		m_messagePainter.clearInputDisplay();
	}
}

void QGBA::Display::filter(bool filter) {
	m_filter = filter;
}

void QGBA::Display::showMessage(const QString& message) {
	m_messagePainter.showMessage(message);
	if (!isDrawing()) {
		forceDraw();
	}
}

void QGBA::Display::mouseMoveEvent(QMouseEvent*) {
	emit showCursor();
	m_mouseTimer.stop();
	m_mouseTimer.start();
}
