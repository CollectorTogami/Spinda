/* Copyright (c) 2013-2026 Jeffrey Pfau
 *
 * Original mGBA source is credited to Jeffrey Pfau and contributors.
 * Local custom modifications in this fork were added for this workspace and
 * are not upstream mGBA work or authored by Jeffrey Pfau.
 *
 * This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */
#include "VirtualPad.h"

#include "CoreController.h"
#include "WindowsDarkChrome.h"

#include <QCheckBox>
#include <QCloseEvent>
#include <QEvent>
#include <QGridLayout>
#include <QHBoxLayout>
#include <QHideEvent>
#include <QLabel>
#include <QMouseEvent>
#include <QPushButton>
#include <QSizePolicy>
#include <QShowEvent>
#include <QSpinBox>
#include <QStringList>
#include <QVBoxLayout>

#include <mgba/internal/gba/input.h>

#include <utility>

using namespace QGBA;

namespace {

class PadButton : public QPushButton {
public:
	PadButton(const QString& label, int key, QWidget* parent = nullptr)
		: QPushButton(label, parent)
		, m_key(key)
	{
		setMinimumSize(54, 42);
		setSizePolicy(QSizePolicy::Fixed, QSizePolicy::Fixed);
		setFocusPolicy(Qt::NoFocus);
	}

	std::function<void(int, Qt::MouseButton, bool)> mouseHandler;

protected:
	virtual void mousePressEvent(QMouseEvent* event) override {
		if (event->button() == Qt::LeftButton || event->button() == Qt::RightButton) {
			setDown(true);
			if (mouseHandler) {
				mouseHandler(m_key, event->button(), true);
			}
			event->accept();
			return;
		}
		QPushButton::mousePressEvent(event);
	}

	virtual void mouseReleaseEvent(QMouseEvent* event) override {
		if (event->button() == Qt::LeftButton || event->button() == Qt::RightButton) {
			setDown(false);
			if (mouseHandler) {
				mouseHandler(m_key, event->button(), false);
			}
			event->accept();
			return;
		}
		QPushButton::mouseReleaseEvent(event);
	}

private:
	int m_key;
};

bool validGbaKey(int key) {
	return key >= 0 && key < GBA_KEY_MAX;
}

}

VirtualPad::VirtualPad(QWidget* parent)
	: QWidget(parent, Qt::Tool)
{
	setWindowTitle(tr("Virtual Pad"));
	setMinimumWidth(380);
	m_settingsChangedTimer.setSingleShot(true);
	m_settingsChangedTimer.setInterval(250);
	connect(&m_settingsChangedTimer, &QTimer::timeout, this, &VirtualPad::flushSettingsChanged);
	m_inputStatusTimer.setInterval(50);
	connect(&m_inputStatusTimer, &QTimer::timeout, this, &VirtualPad::updateInputStatus);

	QVBoxLayout* layout = new QVBoxLayout(this);
	layout->setContentsMargins(10, 10, 10, 10);
	layout->setSpacing(8);

	QHBoxLayout* shoulderLayout = new QHBoxLayout();
	shoulderLayout->addWidget(createInputButton(tr("L"), GBA_KEY_L));
	shoulderLayout->addStretch();
	shoulderLayout->addWidget(createInputButton(tr("R"), GBA_KEY_R));
	layout->addLayout(shoulderLayout);

	QHBoxLayout* padLayout = new QHBoxLayout();
	padLayout->setSpacing(18);

	QGridLayout* dpadLayout = new QGridLayout();
	dpadLayout->setSpacing(4);
	dpadLayout->addWidget(createInputButton(tr("Up"), GBA_KEY_UP), 0, 1);
	dpadLayout->addWidget(createInputButton(tr("Left"), GBA_KEY_LEFT), 1, 0);
	dpadLayout->addWidget(createInputButton(tr("Right"), GBA_KEY_RIGHT), 1, 2);
	dpadLayout->addWidget(createInputButton(tr("Down"), GBA_KEY_DOWN), 2, 1);
	padLayout->addLayout(dpadLayout);

	QVBoxLayout* menuLayout = new QVBoxLayout();
	menuLayout->setSpacing(6);
	menuLayout->addStretch();
	menuLayout->addWidget(createInputButton(tr("Select"), GBA_KEY_SELECT));
	menuLayout->addWidget(createInputButton(tr("Start"), GBA_KEY_START));
	menuLayout->addStretch();
	padLayout->addLayout(menuLayout);

	QGridLayout* faceLayout = new QGridLayout();
	faceLayout->setSpacing(4);
	faceLayout->addWidget(createInputButton(tr("B"), GBA_KEY_B), 1, 0);
	faceLayout->addWidget(createInputButton(tr("A"), GBA_KEY_A), 0, 1);
	padLayout->addLayout(faceLayout);

	layout->addLayout(padLayout);

	QGridLayout* optionLayout = new QGridLayout();
	optionLayout->setColumnStretch(3, 1);

	m_stickyCheckbox = new QCheckBox(tr("Sticky"));
	m_stickyCheckbox->setToolTip(tr("When enabled, left-click toggles button holds instead of only holding while the mouse is down."));
	connect(m_stickyCheckbox, &QCheckBox::toggled, this, [this](bool checked) {
		m_settings.sticky = checked;
		if (!checked) {
			clearHeldKeys();
		}
		updatePadEnabled();
		refreshButtonStates();
		notifySettingsChanged();
	});
	optionLayout->addWidget(m_stickyCheckbox, 0, 0);

	m_pressForFramesCheckbox = new QCheckBox(tr("Press for"));
	m_pressForFramesCheckbox->setToolTip(tr("Left-click presses a button for a fixed number of emulated frames, releases it, then pauses."));
	connect(m_pressForFramesCheckbox, &QCheckBox::toggled, this, [this](bool checked) {
		m_settings.pressForFrames = checked;
		if (m_pressForFramesSpin) {
			m_pressForFramesSpin->setEnabled(checked);
		}
		updatePadEnabled();
		notifySettingsChanged();
	});
	optionLayout->addWidget(m_pressForFramesCheckbox, 0, 1);

	m_pressForFramesSpin = new QSpinBox();
	m_pressForFramesSpin->setRange(1, 1000000);
	m_pressForFramesSpin->setValue(1);
	m_pressForFramesSpin->setSuffix(tr(" frames"));
	m_pressForFramesSpin->setEnabled(false);
	connect(m_pressForFramesSpin, qOverload<int>(&QSpinBox::valueChanged), this, [this](int value) {
		m_settings.pressFrameCount = value;
		updatePadEnabled();
		notifySettingsChanged();
	});
	optionLayout->addWidget(m_pressForFramesSpin, 0, 2);

	m_clearAnalogCheckbox = new QCheckBox(tr("Clear also clears analog input"));
	m_clearAnalogCheckbox->setEnabled(false);
	m_clearAnalogCheckbox->setToolTip(tr("This mirrors EmuHawk's option; the GBA virtual pad has no analog controls to clear."));
	optionLayout->addWidget(m_clearAnalogCheckbox, 1, 0, 1, 3);

	layout->addLayout(optionLayout);

	QHBoxLayout* utilityLayout = new QHBoxLayout();
	m_powerButton = new QPushButton(tr("Power: On"));
	m_powerButton->setCheckable(true);
	m_powerButton->setChecked(true);
	m_powerButton->setFocusPolicy(Qt::NoFocus);
	connect(m_powerButton, &QPushButton::toggled, this, [this](bool checked) {
		m_powerOn = checked;
		if (!m_powerOn) {
			clearAll();
		}
		updatePadEnabled();
	});
	utilityLayout->addWidget(m_powerButton);

	QPushButton* clearButton = new QPushButton(tr("Clear All"));
	clearButton->setFocusPolicy(Qt::NoFocus);
	connect(clearButton, &QPushButton::clicked, this, &VirtualPad::clearAll);
	utilityLayout->addWidget(clearButton);

	m_frameAdvanceButton = new QPushButton(tr("Frame Advance"));
	m_frameAdvanceButton->setFocusPolicy(Qt::NoFocus);
	connect(m_frameAdvanceButton, &QPushButton::clicked, this, &VirtualPad::frameAdvance);
	utilityLayout->addWidget(m_frameAdvanceButton);

	QPushButton* settingsButton = new QPushButton(tr("Settings..."));
	settingsButton->setFocusPolicy(Qt::NoFocus);
	connect(settingsButton, &QPushButton::clicked, this, [this]() {
		if (m_settingsHandler) {
			m_settingsHandler();
		}
	});
	utilityLayout->addWidget(settingsButton);
	layout->addLayout(utilityLayout);

	m_pressedButtonsLabel = new QLabel(this);
	m_pressedButtonsLabel->setWordWrap(true);
	layout->addWidget(m_pressedButtonsLabel);

	m_nextButtonsLabel = new QLabel(this);
	m_nextButtonsLabel->setWordWrap(true);
	layout->addWidget(m_nextButtonsLabel);

	m_statusLabel = new QLabel(this);
	m_statusLabel->setWordWrap(true);
	layout->addWidget(m_statusLabel);

	setSettings(m_settings);
	updatePadEnabled();
	updateInputStatus();
}

void VirtualPad::setController(const std::shared_ptr<CoreController>& controller) {
	clearAll();
	if (m_inputStatusConnection) {
		disconnect(m_inputStatusConnection);
		m_inputStatusConnection = QMetaObject::Connection();
	}
	m_controller = controller;
	if (controller) {
		m_inputStatusConnection = connect(controller.get(), &CoreController::frameAvailable, this, &VirtualPad::updateInputStatus);
	}
	updatePadEnabled();
	refreshButtonStates();
	updateInputStatus();
}

void VirtualPad::setSettings(const VirtualPadSettings& settings) {
	m_loadingSettings = true;
	m_settings = settings;
	m_settings.pressFrameCount = qMax(1, m_settings.pressFrameCount);
	if (m_stickyCheckbox) {
		m_stickyCheckbox->setChecked(m_settings.sticky);
	}
	if (m_pressForFramesCheckbox) {
		m_pressForFramesCheckbox->setChecked(m_settings.pressForFrames);
	}
	if (m_pressForFramesSpin) {
		m_pressForFramesSpin->setValue(m_settings.pressFrameCount);
		m_pressForFramesSpin->setEnabled(m_settings.pressForFrames);
	}
	if (m_clearAnalogCheckbox) {
		m_clearAnalogCheckbox->setChecked(m_settings.clearAlsoClearsAnalog);
	}
	m_loadingSettings = false;

	setAlwaysOnTop(m_settings.alwaysOnTop);
	updatePadEnabled();
	refreshButtonStates();
}

void VirtualPad::setSettingsChangedHandler(std::function<void(const VirtualPadSettings&)> handler) {
	m_settingsChangedHandler = std::move(handler);
}

void VirtualPad::setAlwaysOnTop(bool enable) {
	if (m_alwaysOnTop == enable) {
		return;
	}

	m_alwaysOnTop = enable;
	m_settings.alwaysOnTop = enable;
	const bool wasVisible = isVisible();
	Qt::WindowFlags flags = windowFlags();
	if (enable) {
		flags |= Qt::WindowStaysOnTopHint;
	} else {
		flags &= ~Qt::WindowStaysOnTopHint;
	}
	setWindowFlags(flags);
	if (wasVisible) {
		show();
		applyWindowsDarkChrome(this);
	}
}

void VirtualPad::setSettingsHandler(std::function<void()> handler) {
	m_settingsHandler = std::move(handler);
}

bool VirtualPad::setHeld(int key, bool held) {
	return setKeyHeld(key, held);
}

bool VirtualPad::setAutofire(int key, bool enable) {
	return setKeyAutofire(key, enable);
}

bool VirtualPad::pressForFrames(int key, int frames) {
	if (!validGbaKey(key) || frames < 1 || !m_powerOn) {
		return false;
	}

	std::shared_ptr<CoreController> controller = m_controller.lock();
	if (!controller || controller->platform() != mPLATFORM_GBA) {
		return false;
	}

	setKeyAutofire(key, false);
	setKeyHeld(key, false);
	m_timedKeys.insert(key);
	m_timedFramesRemaining[key] = frames;
	controller->pressKeyForFrames(key, frames);
	updateTimedFrameConnection();
	refreshButtonStates();
	return true;
}

uint32_t VirtualPad::keyMask() const {
	uint32_t mask = 0;
	const auto addKeyToMask = [&mask](int key) {
		if (validGbaKey(key)) {
			mask |= 1u << key;
		}
	};
	for (int key : m_heldKeys) {
		addKeyToMask(key);
	}
	for (int key : m_timedKeys) {
		addKeyToMask(key);
	}
	for (int key : m_autofireKeys) {
		// For tape capture, expose the visible Virtual Pad button state. The
		// recorder samples this into exact per-frame masks; avoid autofire in a
		// captured route if you need the alternating waveform itself.
		addKeyToMask(key);
	}
	return mask;
}

void VirtualPad::clearAll() {
	clearTimedKeys();
	clearHeldKeys();
	clearAutofireKeys();
	refreshButtonStates();
	updatePadEnabled();
}

void VirtualPad::showEvent(QShowEvent* event) {
	m_inputStatusTimer.start();
	updateInputStatus();
	QWidget::showEvent(event);
	applyWindowsDarkChrome(this);
}

void VirtualPad::closeEvent(QCloseEvent* event) {
	flushSettingsChanged();
	clearAll();
	m_inputStatusTimer.stop();
	QWidget::closeEvent(event);
}

void VirtualPad::hideEvent(QHideEvent* event) {
	flushSettingsChanged();
	clearAll();
	m_inputStatusTimer.stop();
	QWidget::hideEvent(event);
}

void VirtualPad::changeEvent(QEvent* event) {
	if ((event->type() == QEvent::ActivationChange || event->type() == QEvent::WindowDeactivate) && !m_settings.sticky && !m_settings.pressForFrames) {
		clearHeldKeys();
		refreshButtonStates();
	}
	QWidget::changeEvent(event);
}

QPushButton* VirtualPad::createInputButton(const QString& label, int key) {
	PadButton* button = new PadButton(label, key, this);
	button->mouseHandler = [this](int key, Qt::MouseButton button, bool pressed) {
		handleInputMouse(key, button, pressed);
	};
	m_inputButtons.append(button);
	m_buttonsByKey.insert(key, button);
	m_buttonLabels.insert(key, label);
	return button;
}

void VirtualPad::handleInputMouse(int key, Qt::MouseButton button, bool pressed) {
	if (!m_powerOn || !hasUsableController()) {
		return;
	}

	if (button == Qt::RightButton) {
		if (pressed) {
			setKeyAutofire(key, !m_autofireKeys.contains(key));
		}
		return;
	}

	if (button != Qt::LeftButton) {
		return;
	}

	if (m_settings.pressForFrames) {
		if (pressed) {
			startTimedPress(key);
		}
		return;
	}

	if (m_settings.sticky) {
		if (pressed) {
			setKeyHeld(key, !m_heldKeys.contains(key));
		}
		return;
	}

	setKeyHeld(key, pressed);
}

bool VirtualPad::setKeyHeld(int key, bool held) {
	if (!validGbaKey(key)) {
		return false;
	}
	if (held && !m_powerOn) {
		return false;
	}

	std::shared_ptr<CoreController> controller = m_controller.lock();
	if (!controller || controller->platform() != mPLATFORM_GBA) {
		return false;
	}

	if (held) {
		setKeyAutofire(key, false);
		if (m_heldKeys.contains(key)) {
			return true;
		}
		m_timedKeys.remove(key);
		m_timedFramesRemaining.remove(key);
		updateTimedFrameConnection();
		m_heldKeys.insert(key);
		controller->addKey(key);
		refreshButtonStates();
		return true;
	}

	if (!m_heldKeys.remove(key)) {
		return true;
	}
	controller->clearKey(key);
	refreshButtonStates();
	return true;
}

bool VirtualPad::setKeyAutofire(int key, bool enable) {
	if (!validGbaKey(key)) {
		return false;
	}
	if (enable && !m_powerOn) {
		return false;
	}

	std::shared_ptr<CoreController> controller = m_controller.lock();
	if (!controller || controller->platform() != mPLATFORM_GBA) {
		return false;
	}

	if (enable) {
		// Power, held, timed, and autofire are mutually exclusive control
		// modes for a key. Keep the controller-facing state unambiguous before
		// enabling the repeating press.
		setKeyHeld(key, false);
		m_timedKeys.remove(key);
		m_timedFramesRemaining.remove(key);
		updateTimedFrameConnection();
		m_autofireKeys.insert(key);
	} else {
		m_autofireKeys.remove(key);
	}
	controller->setAutofire(key, enable);
	refreshButtonStates();
	return true;
}

void VirtualPad::clearHeldKeys() {
	const QSet<int> heldKeys = m_heldKeys;
	m_heldKeys.clear();

	std::shared_ptr<CoreController> controller = m_controller.lock();
	if (!controller) {
		return;
	}

	for (int key : heldKeys) {
		controller->clearKey(key);
	}
}

void VirtualPad::clearTimedKeys() {
	const QSet<int> timedKeys = m_timedKeys;
	m_timedKeys.clear();
	m_timedFramesRemaining.clear();
	updateTimedFrameConnection();

	std::shared_ptr<CoreController> controller = m_controller.lock();
	if (!controller) {
		return;
	}

	for (int key : timedKeys) {
		controller->clearKey(key);
	}
}

void VirtualPad::clearAutofireKeys() {
	const QSet<int> autofireKeys = m_autofireKeys;
	m_autofireKeys.clear();

	std::shared_ptr<CoreController> controller = m_controller.lock();
	if (!controller) {
		return;
	}

	for (int key : autofireKeys) {
		controller->setAutofire(key, false);
	}
}

void VirtualPad::startTimedPress(int key) {
	pressForFrames(key, m_pressForFramesSpin ? m_pressForFramesSpin->value() : m_settings.pressFrameCount);
}

void VirtualPad::onTimedFrame() {
	QList<int> finishedKeys;
	for (auto iter = m_timedFramesRemaining.begin(); iter != m_timedFramesRemaining.end(); ++iter) {
		iter.value() -= 1;
		if (iter.value() <= 0) {
			finishedKeys.append(iter.key());
		}
	}

	for (int key : finishedKeys) {
		m_timedFramesRemaining.remove(key);
		m_timedKeys.remove(key);
	}

	updateTimedFrameConnection();
	refreshButtonStates();
	updatePadEnabled();
}

void VirtualPad::updateTimedFrameConnection() {
	if (m_timedKeys.isEmpty()) {
		if (m_timedFrameConnection) {
			disconnect(m_timedFrameConnection);
			m_timedFrameConnection = QMetaObject::Connection();
		}
		return;
	}

	if (m_timedFrameConnection) {
		return;
	}

	std::shared_ptr<CoreController> controller = m_controller.lock();
	if (!controller) {
		return;
	}
	m_timedFrameConnection = connect(controller.get(), &CoreController::frameAvailable, this, &VirtualPad::onTimedFrame);
}

void VirtualPad::updatePadEnabled() {
	const bool usable = hasUsableController();
	for (QPushButton* button : m_inputButtons) {
		button->setEnabled(m_powerOn && usable);
	}

	if (m_powerButton) {
		m_powerButton->setText(m_powerOn ? tr("Power: On") : tr("Power: Off"));
	}
	if (m_frameAdvanceButton) {
		m_frameAdvanceButton->setEnabled(usable);
	}
	if (!m_statusLabel) {
		return;
	}
	if (!m_powerOn) {
		m_statusLabel->setText(tr("Virtual pad input is powered off."));
	} else if (!usable) {
		m_statusLabel->setText(tr("Load a GBA game to use the virtual pad."));
	} else if (m_settings.pressForFrames) {
		m_statusLabel->setText(tr("Timed press: left-click presses for %1 frame(s), releases, then pauses. Right-click toggles autofire.").arg(m_settings.pressFrameCount));
	} else if (m_settings.sticky) {
		m_statusLabel->setText(tr("Sticky mode: left-click toggles holds. Right-click toggles autofire."));
	} else {
		m_statusLabel->setText(tr("Momentary mode: hold left mouse to hold buttons. Right-click toggles autofire."));
	}
}

void VirtualPad::refreshButtonStates() {
	for (auto iter = m_buttonsByKey.begin(); iter != m_buttonsByKey.end(); ++iter) {
		const int key = iter.key();
		QPushButton* button = iter.value();
		if (!button) {
			continue;
		}

		const bool held = m_heldKeys.contains(key);
		const bool timed = m_timedKeys.contains(key);
		const bool autofire = m_autofireKeys.contains(key);
		button->setDown(held || timed || autofire);
		QString label = m_buttonLabels.value(key);
		if (autofire) {
			label += tr(" *");
		}
		button->setText(label);
		button->setToolTip(autofire ? tr("Autofire is enabled. Right-click to disable it.") : tr("Right-click to toggle autofire."));
	}
	updateInputStatus();
}

QString VirtualPad::describeKeyMask(uint32_t mask) const {
	static constexpr int keys[] = {
		GBA_KEY_A,
		GBA_KEY_B,
		GBA_KEY_SELECT,
		GBA_KEY_START,
		GBA_KEY_UP,
		GBA_KEY_DOWN,
		GBA_KEY_LEFT,
		GBA_KEY_RIGHT,
		GBA_KEY_L,
		GBA_KEY_R,
	};
	QStringList names;
	for (int key : keys) {
		if (mask & (1u << key)) {
			names.append(m_buttonLabels.value(key, QString::number(key)));
		}
	}
	return names.isEmpty() ? tr("None") : names.join(tr(" + "));
}

void VirtualPad::updateInputStatus() {
	const std::shared_ptr<CoreController> controller = m_controller.lock();
	const uint32_t pressedMask = controller ? controller->currentKeys() : 0;
	const uint32_t nextMask = controller ? controller->pendingKeys() : 0;
	if (pressedMask == m_lastPressedMask && nextMask == m_lastNextMask) {
		return;
	}
	m_lastPressedMask = pressedMask;
	m_lastNextMask = nextMask;

	if (m_pressedButtonsLabel) {
		m_pressedButtonsLabel->setText(tr("Pressed now: %1").arg(describeKeyMask(pressedMask)));
	}
	if (m_nextButtonsLabel) {
		// "Next frame" includes keyboard keys after Qt mapping, Virtual Pad
		// holds/timed presses, and any scripted key mask that is queued on the
		// live controller. It intentionally reports GBA buttons only.
		m_nextButtonsLabel->setText(tr("Next frame: %1").arg(describeKeyMask(nextMask)));
	}
}

void VirtualPad::frameAdvance() {
	std::shared_ptr<CoreController> controller = m_controller.lock();
	if (!controller || controller->platform() != mPLATFORM_GBA) {
		return;
	}
	controller->frameAdvance();
	updateInputStatus();
}

void VirtualPad::notifySettingsChanged() {
	if (m_loadingSettings || !m_settingsChangedHandler) {
		return;
	}
	// Coalesce quick UI edits, especially spinbox changes, into one config
	// write while still flushing immediately when the tool window closes.
	m_settingsChangedTimer.start();
}

void VirtualPad::flushSettingsChanged() {
	if (m_settingsChangedTimer.isActive()) {
		m_settingsChangedTimer.stop();
	}
	if (m_loadingSettings || !m_settingsChangedHandler) {
		return;
	}
	m_settingsChangedHandler(m_settings);
}

bool VirtualPad::hasUsableController() const {
	std::shared_ptr<CoreController> controller = m_controller.lock();
	return controller && controller->platform() == mPLATFORM_GBA;
}
