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

#include <QByteArray>
#include <QHash>
#include <QMap>
#include <QMutex>
#include <QString>

namespace QGBA {

class SavestateMemoryCache {
public:
	struct Stats {
		bool enabled = false;
		int entryCount = 0;
		int maxEntries = 0;
		quint64 usedBytes = 0;
		quint64 maxBytes = 0;
	};

	SavestateMemoryCache();

	void setEnabled(bool enabled);
	bool isEnabled() const;

	void configure(int maxEntries, quint64 maxBytes);
	void clear();

	bool load(const QString& key, QByteArray* out);
	void store(const QString& key, const QByteArray& payload);

	Stats stats() const;

private:
	struct Entry {
		QByteArray payload;
		quint64 tick = 0;
	};

	void touchLocked(const QString& key, Entry* entry);
	void evictLocked();

	mutable QMutex m_mutex;
	QHash<QString, Entry> m_entries;
	QMap<quint64, QString> m_lru;
	bool m_enabled = false;
	int m_maxEntries = 64;
	quint64 m_maxBytes = 4ull * 1024ull * 1024ull * 1024ull;
	quint64 m_usedBytes = 0;
	quint64 m_tick = 0;
};

}
