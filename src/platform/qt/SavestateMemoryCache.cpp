/* Copyright (c) 2013-2026 Jeffrey Pfau
 *
 * Original mGBA source is credited to Jeffrey Pfau and contributors.
 * Local custom modifications in this fork were added for this workspace and
 * are not upstream mGBA work or authored by Jeffrey Pfau.
 *
 * This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */
#include "SavestateMemoryCache.h"

#include <QMutexLocker>

using namespace QGBA;

SavestateMemoryCache::SavestateMemoryCache() {
}

void SavestateMemoryCache::setEnabled(bool enabled) {
	QMutexLocker locker(&m_mutex);
	m_enabled = enabled;
	if (!m_enabled) {
		m_entries.clear();
		m_lru.clear();
		m_usedBytes = 0;
		m_tick = 0;
	}
}

bool SavestateMemoryCache::isEnabled() const {
	QMutexLocker locker(&m_mutex);
	return m_enabled;
}

void SavestateMemoryCache::configure(int maxEntries, quint64 maxBytes) {
	QMutexLocker locker(&m_mutex);
	m_maxEntries = qMax(1, maxEntries);
	m_maxBytes = qMax<quint64>(1024ull * 1024ull, maxBytes);
	evictLocked();
}

void SavestateMemoryCache::clear() {
	QMutexLocker locker(&m_mutex);
	m_entries.clear();
	m_lru.clear();
	m_usedBytes = 0;
	m_tick = 0;
}

bool SavestateMemoryCache::load(const QString& key, QByteArray* out) {
	QMutexLocker locker(&m_mutex);
	if (!m_enabled || !out) {
		return false;
	}
	auto it = m_entries.find(key);
	if (it == m_entries.end()) {
		return false;
	}

	// Touch the entry on load so repeated savestate ping-pong keeps hot states
	// resident in RAM instead of evicting them behind the active workflow.
	touchLocked(key, &it.value());
	*out = it->payload;
	return true;
}

void SavestateMemoryCache::store(const QString& key, const QByteArray& payload) {
	QMutexLocker locker(&m_mutex);
	if (!m_enabled || key.isEmpty() || payload.isEmpty()) {
		return;
	}

	const quint64 payloadBytes = static_cast<quint64>(payload.size());
	auto it = m_entries.find(key);
	// A single oversized savestate should not evict the whole hot set only to
	// be removed immediately afterward. Keep the existing cache intact instead.
	if (payloadBytes > m_maxBytes) {
		if (it != m_entries.end()) {
			if (it->tick) {
				m_lru.remove(it->tick);
			}
			m_usedBytes -= static_cast<quint64>(it->payload.size());
			m_entries.erase(it);
		}
		return;
	}

	if (it != m_entries.end()) {
		m_usedBytes -= static_cast<quint64>(it->payload.size());
		it->payload = payload;
		touchLocked(key, &it.value());
		m_usedBytes += payloadBytes;
	} else {
		Entry entry;
		entry.payload = payload;
		it = m_entries.insert(key, entry);
		touchLocked(key, &it.value());
		m_usedBytes += payloadBytes;
	}

	evictLocked();
}

SavestateMemoryCache::Stats SavestateMemoryCache::stats() const {
	QMutexLocker locker(&m_mutex);
	Stats stats;
	stats.enabled = m_enabled;
	stats.entryCount = m_entries.size();
	stats.maxEntries = m_maxEntries;
	stats.usedBytes = m_usedBytes;
	stats.maxBytes = m_maxBytes;
	return stats;
}

void SavestateMemoryCache::touchLocked(const QString& key, Entry* entry) {
	if (!entry) {
		return;
	}
	if (entry->tick) {
		m_lru.remove(entry->tick);
	}
	entry->tick = ++m_tick;
	m_lru.insert(entry->tick, key);
}

void SavestateMemoryCache::evictLocked() {
	while (!m_entries.isEmpty() && (m_entries.size() > m_maxEntries || m_usedBytes > m_maxBytes)) {
		if (m_lru.isEmpty()) {
			m_entries.clear();
			m_usedBytes = 0;
			return;
		}
		auto oldestTick = m_lru.begin();
		const QString key = oldestTick.value();
		m_lru.erase(oldestTick);
		auto oldest = m_entries.find(key);
		if (oldest == m_entries.end()) {
			continue;
		}
		m_usedBytes -= static_cast<quint64>(oldest->payload.size());
		m_entries.erase(oldest);
	}
}
