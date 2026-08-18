/* Copyright (c) 2013-2026 Jeffrey Pfau
 *
 * Original mGBA source is credited to Jeffrey Pfau and contributors.
 * Local custom modifications in this fork were added for this workspace and
 * are not upstream mGBA work or authored by Jeffrey Pfau.
 *
 * This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */
#pragma once

#include "CustomFeatures.h"

#include <QHash>
#include <QList>
#include <QMetaObject>
#include <QSet>
#include <QTimer>
#include <QWidget>

#include <cstdint>
#include <functional>
#include <memory>

class QCloseEvent;
class QEvent;
class QGridLayout;
class QHideEvent;
class QShowEvent;
class QCheckBox;
class QLabel;
class QPushButton;
class QSpinBox;

namespace QGBA {

class CoreController;

class VirtualPad : public QWidget {
public:
	explicit VirtualPad(QWidget* parent = nullptr);

	void setController(const std::shared_ptr<CoreController>& controller);
	void setSettings(const VirtualPadSettings& settings);
	void setSettingsChangedHandler(std::function<void(const VirtualPadSettings&)> handler);
	void setAlwaysOnTop(bool enable);
	bool alwaysOnTop() const { return m_alwaysOnTop; }
	void setSettingsHandler(std::function<void()> handler);
	bool setHeld(int key, bool held);
	bool setAutofire(int key, bool enable);
	bool pressForFrames(int key, int frames);
	uint32_t keyMask() const;
	void clearAll();

protected:
	virtual void showEvent(QShowEvent* event) override;
	virtual void closeEvent(QCloseEvent* event) override;
	virtual void hideEvent(QHideEvent* event) override;
	virtual void changeEvent(QEvent* event) override;

private:
	QPushButton* createInputButton(const QString& label, int key);
	void handleInputMouse(int key, Qt::MouseButton button, bool pressed);
	bool setKeyHeld(int key, bool held);
	bool setKeyAutofire(int key, bool enable);
	void clearHeldKeys();
	void clearTimedKeys();
	void clearAutofireKeys();
	void startTimedPress(int key);
	void onTimedFrame();
	void updateTimedFrameConnection();
	void updatePadEnabled();
	void refreshButtonStates();
	QString describeKeyMask(uint32_t mask) const;
	void updateInputStatus();
	void frameAdvance();
	void notifySettingsChanged();
	void flushSettingsChanged();
	bool hasUsableController() const;

	std::weak_ptr<CoreController> m_controller;
	QList<QPushButton*> m_inputButtons;
	QHash<int, QPushButton*> m_buttonsByKey;
	QHash<int, QString> m_buttonLabels;
	QSet<int> m_heldKeys;
	QSet<int> m_timedKeys;
	QSet<int> m_autofireKeys;
	QHash<int, int> m_timedFramesRemaining;
	QMetaObject::Connection m_timedFrameConnection;
	QMetaObject::Connection m_inputStatusConnection;
	QCheckBox* m_stickyCheckbox = nullptr;
	QCheckBox* m_pressForFramesCheckbox = nullptr;
	QSpinBox* m_pressForFramesSpin = nullptr;
	QCheckBox* m_clearAnalogCheckbox = nullptr;
	QPushButton* m_powerButton = nullptr;
	QPushButton* m_frameAdvanceButton = nullptr;
	QLabel* m_pressedButtonsLabel = nullptr;
	QLabel* m_nextButtonsLabel = nullptr;
	QLabel* m_statusLabel = nullptr;
	QTimer m_settingsChangedTimer;
	QTimer m_inputStatusTimer;
	VirtualPadSettings m_settings;
	uint32_t m_lastPressedMask = 0xFFFFFFFFu;
	uint32_t m_lastNextMask = 0xFFFFFFFFu;
	bool m_powerOn = true;
	bool m_alwaysOnTop = false;
	bool m_loadingSettings = false;
	std::function<void()> m_settingsHandler;
	std::function<void(const VirtualPadSettings&)> m_settingsChangedHandler;
};

}
