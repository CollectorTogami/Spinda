/* Copyright (c) 2013-2026 Jeffrey Pfau
 *
 * Original mGBA source is credited to Jeffrey Pfau and contributors.
 * Local custom modifications in this fork were added for this workspace and
 * are not upstream mGBA work or authored by Jeffrey Pfau.
 *
 * This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */
#include "SpindaProjectView.h"

#include "CoreController.h"
#include "VFileDevice.h"
#include "WindowsDarkChrome.h"

#include <QCheckBox>
#include <QCoreApplication>
#include <QCryptographicHash>
#include <QDateTime>
#include <QDialogButtonBox>
#include <QDir>
#include <QElapsedTimer>
#include <QFile>
#include <QFileDialog>
#include <QFileInfo>
#include <QFormLayout>
#include <QHash>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QLabel>
#include <QLineEdit>
#include <QMessageBox>
#include <QPlainTextEdit>
#include <QProgressBar>
#include <QPushButton>
#include <QRegularExpression>
#include <QSet>
#include <QSaveFile>
#include <QSpinBox>
#include <QStringList>
#include <QVBoxLayout>
#include <QtGlobal>

#include <algorithm>
#include <cerrno>
#include <cstdio>
#include <fcntl.h>
#include <limits>
#include <string>
#include <vector>

#include <mgba/core/serialize.h>
#include <mgba-util/vfs.h>

#include <zlib.h>

#ifdef Q_OS_WIN
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#else
#include <unistd.h>
#endif

using namespace QGBA;

namespace {

constexpr uint32_t GRNG_VALUE_ADDR = 0x03005000;
constexpr uint32_t GPLAYER_PARTY_COUNT_ADDR = 0x02024029;
constexpr uint32_t GPLAYER_PARTY_ADDR = 0x02024284;
constexpr int PARTY_SLOT_SIZE = 100;
constexpr int BOX_SLOT_SIZE = 80;
constexpr int EXPECTED_RECORDS = 0x10000;
constexpr int BITMAP_BYTES = EXPECTED_RECORDS / 8;
constexpr uint32_t GBA_LCRNG_MULTIPLIER = 0x41C64E6D;
constexpr uint32_t GBA_LCRNG_INCREMENT = 0x6073;
constexpr uint32_t A_BUTTON_MASK = 1u << 0;
constexpr uint16_t ZIP_METHOD_DEFLATE = 8;
constexpr uint16_t ZIP_DOS_TIME_MIDNIGHT = 0;
constexpr uint16_t ZIP_DOS_DATE_2026_01_01 = (46 << 9) | (1 << 5) | 1;
constexpr int ZIP_DEFLATE_LEVEL = 1;
constexpr int DEFAULT_LEARN_PICKUP_DELAY_SAMPLES = 32;

struct Phase3Config {
	uint16_t laneId = 0x0001;
	QString phase2StatePath;
	QString secondhalfCsvPath;
	QString outputDir;
	QString cacheDirPath;
	int pickupInputLeadFrames = 3;
	int pickupHoldFrames = 1;
	int postPickupFrames = 24;
	int minPickupDetectFrame = 4;
	int fastPickupCheckFirstFrame = 4;
	int fastPickupCheckSecondFrame = 5;
	int maxPickupDetectFrames = 12;
	int learnPickupDelaySamples = DEFAULT_LEARN_PICKUP_DELAY_SAMPLES;
	int runtimeScheduleMaxSteps = 4000000;
	int limit = 0;
	uint32_t expectedBaselineRng = 0x2B0C94C1;
	bool checkExpectedBaselineRng = true;
	bool overwrite = false;
	bool dynamicPickupDetection = true;
	bool fastPickupChecks = true;
	bool learnPickupDelay = true;
	bool enableAudioKillswitch = true;
	bool enableNoRender = true;
	bool enableFastForward = true;
	bool headlessAutorun = false;
};

struct Phase3Timing {
	qint64 frameAdvanceNs = 0;
	qint64 scratchSaveNs = 0;
	qint64 pickupWaitDetectNs = 0;
	qint64 scratchRestoreNs = 0;
	qint64 pk3ReadNs = 0;
	qint64 zipBuildWriteNs = 0;
	qint64 hashNs = 0;
};

struct LearnedPickupDelayState {
	bool enabled = false;
	bool active = false;
	bool disabledAfterMismatch = false;
	int sampleLimit = 0;
	int sampleCount = 0;
	int sampleMin = 0;
	int sampleMax = 0;
	int learnedDelay = -1;
	int fixedChecks = 0;
	int fallbackScans = 0;
};

struct SecondHalfCsvContract {
	uint16_t initialSeed = 0;
	int rowCount = 0;
	int tZeroRows = 0;
	int minFrame = 0;
	int maxFrame = 0;
};

struct SecondHalfTarget {
	uint16_t upperHalf = 0;
	int sweepIndex = 0;
	int frameFromInitialSeed = 0;
	uint32_t rngSeed = 0;
	int inputFrameFromInitialSeed = 0;
};

struct PickupTarget {
	uint16_t upperHalf = 0;
	int csvSweepIndex = 0;
	int csvFrameFromInitialSeed = 0;
	uint32_t csvRngSeed = 0;
	int eventStepFromStart = 0;
	int inputDeltaFromStart = 0;
};

struct ZipCentralEntry {
	QByteArray name;
	uint32_t crc32 = 0;
	uint32_t compressedSize = 0;
	uint32_t uncompressedSize = 0;
	uint32_t localHeaderOffset = 0;
};

struct ZipDeflater {
	z_stream stream = {};
	bool initialized = false;

	~ZipDeflater() {
		if (initialized) {
			deflateEnd(&stream);
		}
	}

	bool init(QString* errorMessage) {
		const int status = deflateInit2(&stream, ZIP_DEFLATE_LEVEL, Z_DEFLATED, -MAX_WBITS, 8, Z_DEFAULT_STRATEGY);
		if (status != Z_OK) {
			if (errorMessage) {
				*errorMessage = QObject::tr("Could not initialize ZIP deflate stream: %1").arg(status);
			}
			return false;
		}
		initialized = true;
		return true;
	}
};

struct CacheInfo {
	bool hit = false;
	QString path;
	QString key;
	int targetCount = 0;

	QJsonObject toJson() const {
		QJsonObject object;
		object.insert(QStringLiteral("hit"), hit);
		object.insert(QStringLiteral("path"), path);
		object.insert(QStringLiteral("key"), key);
		object.insert(QStringLiteral("target_count"), targetCount);
		return object;
	}
};

struct TargetCacheResult {
	SecondHalfCsvContract contract;
	QVector<SecondHalfTarget> targets;
	CacheInfo cache;
};

struct ScheduleCacheResult {
	QVector<PickupTarget> targets;
	CacheInfo cache;
};

QString formatU16(uint32_t value) {
	return QStringLiteral("0x") + QStringLiteral("%1").arg(value & 0xFFFF, 4, 16, QLatin1Char('0')).toUpper();
}

QString formatU32(uint32_t value) {
	return QStringLiteral("0x") + QStringLiteral("%1").arg(value, 8, 16, QLatin1Char('0')).toUpper();
}

bool parseUInt(const QString& raw, uint32_t maxValue, uint32_t* out) {
	bool ok = false;
	const uint value = raw.trimmed().toUInt(&ok, 0);
	if (!ok || value > maxValue) {
		return false;
	}
	if (out) {
		*out = value;
	}
	return true;
}

bool parseLaneList(const QString& raw, QVector<uint16_t>* lanes, QString* errorMessage) {
	if (!lanes) {
		return false;
	}
	lanes->clear();
	const QStringList parts = raw.split(QRegularExpression(QStringLiteral("[,;\\s]+")), Qt::SkipEmptyParts);
	QSet<uint16_t> seen;
	for (const QString& part : parts) {
		uint32_t lane = 0;
		if (!parseUInt(part, 0xFFFF, &lane)) {
			if (errorMessage) {
				*errorMessage = QObject::tr("Bad lane id in native Phase 3 lane list: %1").arg(part);
			}
			return false;
		}
		const uint16_t lane16 = uint16_t(lane);
		if (!seen.contains(lane16)) {
			seen.insert(lane16);
			lanes->append(lane16);
		}
	}
	if (lanes->isEmpty()) {
		if (errorMessage) {
			*errorMessage = QObject::tr("Native Phase 3 lane list is empty.");
		}
		return false;
	}
	return true;
}

QByteArray nativePhase3EnvName(const char* name) {
	return QByteArrayLiteral("MGBA_SPINDA_NATIVE_PHASE3_") + QByteArray(name);
}

QString nativePhase3EnvString(const char* name) {
	const QByteArray nativeName = nativePhase3EnvName(name);
	const QByteArray nativeValue = qgetenv(nativeName.constData());
	if (!nativeValue.isEmpty()) {
		return QString::fromLocal8Bit(nativeValue).trimmed();
	}
	const QByteArray legacyName = QByteArrayLiteral("MGBA_SPINDA_PHASE3_") + QByteArray(name);
	return QString::fromLocal8Bit(qgetenv(legacyName.constData())).trimmed();
}

bool nativePhase3EnvBool(const char* name, bool defaultValue, QString* errorMessage = nullptr) {
	const QString raw = nativePhase3EnvString(name);
	if (raw.isEmpty()) {
		return defaultValue;
	}
	const QString normalized = raw.toLower();
	if (normalized == QLatin1String("1") || normalized == QLatin1String("true") || normalized == QLatin1String("yes") || normalized == QLatin1String("on")) {
		return true;
	}
	if (normalized == QLatin1String("0") || normalized == QLatin1String("false") || normalized == QLatin1String("no") || normalized == QLatin1String("off")) {
		return false;
	}
	if (errorMessage) {
		const QByteArray envName = nativePhase3EnvName(name);
		*errorMessage = QObject::tr("Bad boolean environment value for %1: %2")
			.arg(QString::fromLatin1(envName.constData()), raw);
	}
	return defaultValue;
}

uint32_t lcrngNext(uint32_t state) {
	return state * GBA_LCRNG_MULTIPLIER + GBA_LCRNG_INCREMENT;
}

QString sha1Hex(const QByteArray& bytes) {
	return QString::fromLatin1(QCryptographicHash::hash(bytes, QCryptographicHash::Sha1).toHex());
}

QString sha1File(const QString& path) {
	QFile file(path);
	if (!file.open(QIODevice::ReadOnly)) {
		return QString();
	}
	QCryptographicHash hash(QCryptographicHash::Sha1);
	while (!file.atEnd()) {
		hash.addData(file.read(1024 * 1024));
	}
	return QString::fromLatin1(hash.result().toHex());
}

double secondsFromNs(qint64 ns) {
	return double(ns) / 1000000000.0;
}

QJsonObject timingToJson(const Phase3Timing& timing) {
	QJsonObject object;
	object.insert(QStringLiteral("frame_advance_seconds"), secondsFromNs(timing.frameAdvanceNs));
	object.insert(QStringLiteral("scratch_save_seconds"), secondsFromNs(timing.scratchSaveNs));
	object.insert(QStringLiteral("pickup_wait_detect_seconds"), secondsFromNs(timing.pickupWaitDetectNs));
	object.insert(QStringLiteral("scratch_restore_seconds"), secondsFromNs(timing.scratchRestoreNs));
	object.insert(QStringLiteral("pk3_read_seconds"), secondsFromNs(timing.pk3ReadNs));
	object.insert(QStringLiteral("zip_build_write_seconds"), secondsFromNs(timing.zipBuildWriteNs));
	object.insert(QStringLiteral("hash_seconds"), secondsFromNs(timing.hashNs));
	return object;
}

QJsonObject learnedPickupDelayToJson(const LearnedPickupDelayState& state) {
	QJsonObject object;
	object.insert(QStringLiteral("enabled"), state.enabled);
	object.insert(QStringLiteral("active"), state.active);
	object.insert(QStringLiteral("disabled_after_mismatch"), state.disabledAfterMismatch);
	object.insert(QStringLiteral("sample_limit"), state.sampleLimit);
	object.insert(QStringLiteral("sample_count"), state.sampleCount);
	object.insert(QStringLiteral("sample_min_frames"), state.sampleMin);
	object.insert(QStringLiteral("sample_max_frames"), state.sampleMax);
	object.insert(QStringLiteral("learned_delay_frames"), state.learnedDelay);
	object.insert(QStringLiteral("fixed_checks"), state.fixedChecks);
	object.insert(QStringLiteral("fallback_scans"), state.fallbackScans);
	return object;
}

QString cacheKey(const QString& payload) {
	return sha1Hex(payload.toUtf8());
}

QString fileStamp(const QString& path, QString* errorMessage) {
	QFileInfo info(path);
	if (!info.isFile()) {
		if (errorMessage) {
			*errorMessage = QObject::tr("File not found: %1").arg(path);
		}
		return QString();
	}
	return QStringLiteral("%1|%2|%3")
		.arg(info.canonicalFilePath().isEmpty() ? info.absoluteFilePath() : info.canonicalFilePath())
		.arg(info.size())
		.arg(info.lastModified().toMSecsSinceEpoch());
}

bool writeJsonAtomic(const QString& path, const QJsonObject& object, QString* errorMessage) {
	QDir().mkpath(QFileInfo(path).absolutePath());
	QSaveFile file(path);
	if (!file.open(QIODevice::WriteOnly | QIODevice::Text)) {
		if (errorMessage) {
			*errorMessage = QObject::tr("Could not write %1: %2").arg(path, file.errorString());
		}
		return false;
	}
	file.write(QJsonDocument(object).toJson(QJsonDocument::Indented));
	if (!file.commit()) {
		if (errorMessage) {
			*errorMessage = QObject::tr("Could not commit %1: %2").arg(path, file.errorString());
		}
		return false;
	}
	return true;
}

void appendJsonLine(const QString& path, const QJsonObject& object) {
	QDir().mkpath(QFileInfo(path).absolutePath());
	QFile file(path);
	if (!file.open(QIODevice::WriteOnly | QIODevice::Append | QIODevice::Text)) {
		return;
	}
	file.write(QJsonDocument(object).toJson(QJsonDocument::Compact));
	file.write("\n");
}

QString laneHex(const Phase3Config& config) {
	return formatU16(config.laneId);
}

QString outputZipPath(const Phase3Config& config) {
	return QDir(config.outputDir).filePath(QStringLiteral("%1.spinda80.zip").arg(laneHex(config)));
}

QString statusPath(const Phase3Config& config) {
	return QDir(config.outputDir).filePath(QStringLiteral("_%1.phase3_status.json").arg(laneHex(config)));
}

QString errorPath(const Phase3Config& config) {
	return QDir(config.outputDir).filePath(QStringLiteral("_%1.phase3_errors.jsonl").arg(laneHex(config)));
}

QString cacheDir(const Phase3Config& config) {
	if (!config.cacheDirPath.isEmpty()) {
		return config.cacheDirPath;
	}
	return QDir(config.outputDir).filePath(QStringLiteral("_cache"));
}

QJsonObject statusBase(const Phase3Config& config) {
	QJsonObject object;
	object.insert(QStringLiteral("schema_version"), 1);
	object.insert(QStringLiteral("native_qt_feature"), true);
	object.insert(QStringLiteral("lane_id"), laneHex(config));
	object.insert(QStringLiteral("phase2_state_path"), config.phase2StatePath);
	object.insert(QStringLiteral("secondhalf_csv"), config.secondhalfCsvPath);
	object.insert(QStringLiteral("output_zip_path"), outputZipPath(config));
	object.insert(QStringLiteral("cache_dir"), cacheDir(config));
	object.insert(QStringLiteral("archive_format"), QStringLiteral("explicit-pid-pk3"));
	object.insert(QStringLiteral("record_size"), BOX_SLOT_SIZE);
	object.insert(QStringLiteral("truncated_tail_bytes"), PARTY_SLOT_SIZE - BOX_SLOT_SIZE);
	object.insert(QStringLiteral("pickup_input_lead_frames"), config.pickupInputLeadFrames);
	object.insert(QStringLiteral("pickup_hold_frames"), config.pickupHoldFrames);
	object.insert(QStringLiteral("post_pickup_frames"), config.postPickupFrames);
	object.insert(QStringLiteral("dynamic_pickup_detection"), config.dynamicPickupDetection);
	object.insert(QStringLiteral("min_pickup_detect_frame"), config.minPickupDetectFrame);
	object.insert(QStringLiteral("fast_pickup_checks"), config.fastPickupChecks);
	object.insert(QStringLiteral("fast_pickup_check_first_frame"), config.fastPickupCheckFirstFrame);
	object.insert(QStringLiteral("fast_pickup_check_second_frame"), config.fastPickupCheckSecondFrame);
	object.insert(QStringLiteral("max_pickup_detect_frames"), config.maxPickupDetectFrames);
	object.insert(QStringLiteral("learn_pickup_delay"), config.learnPickupDelay);
	object.insert(QStringLiteral("learn_pickup_delay_samples"), config.learnPickupDelaySamples);
	object.insert(QStringLiteral("headless_autorun"), config.headlessAutorun);
	object.insert(QStringLiteral("expected_records"), EXPECTED_RECORDS);
	object.insert(QStringLiteral("schedule_source"), QStringLiteral("runtime-rng"));
	object.insert(QStringLiteral("runtime_schedule_max_steps"), config.runtimeScheduleMaxSteps);
	return object;
}

bool writeStatus(const Phase3Config& config, const QJsonObject& payload, QString* errorMessage = nullptr) {
	QJsonObject object = statusBase(config);
	for (auto it = payload.begin(); it != payload.end(); ++it) {
		object.insert(it.key(), it.value());
	}
	return writeJsonAtomic(statusPath(config), object, errorMessage);
}

bool loadStateFromBuffer(mCore* core, const QByteArray& state) {
	if (!core || state.isEmpty()) {
		return false;
	}
	VFile* vf = VFileFromConstMemory(state.constData(), state.size());
	if (!vf) {
		return false;
	}
	const bool ok = mCoreLoadStateNamed(core, vf, 0);
	vf->close(vf);
	return ok;
}

bool saveStateToBuffer(mCore* core, QByteArray* out) {
	if (!core || !out) {
		return false;
	}
	VFile* vf = VFileMemChunk(nullptr, 0);
	if (!vf) {
		return false;
	}
	if (!mCoreSaveStateNamed(core, vf, 0)) {
		vf->close(vf);
		return false;
	}
	void* mapped = vf->map(vf, vf->size(vf), MAP_READ);
	*out = QByteArray(static_cast<const char*>(mapped), vf->size(vf));
	vf->close(vf);
	return true;
}

bool loadStateFileDirect(mCore* core, const QString& path, QString* errorMessage) {
	VFile* vf = VFileDevice::open(path, O_RDONLY);
	if (!vf) {
		if (errorMessage) {
			*errorMessage = QObject::tr("Could not open Phase 2 savestate: %1").arg(path);
		}
		return false;
	}
	const bool ok = mCoreLoadStateNamed(core, vf, 0);
	vf->close(vf);
	if (!ok && errorMessage) {
		*errorMessage = QObject::tr("mGBA could not load Phase 2 savestate: %1").arg(path);
	}
	return ok;
}

QString targetCacheKeyPayload(const Phase3Config& config, QString* errorMessage) {
	const QString stamp = fileStamp(config.secondhalfCsvPath, errorMessage);
	if (stamp.isEmpty()) {
		return QString();
	}
	return QStringLiteral("native-phase3-secondhalf-t0|%1|lead=%2|records=%3")
		.arg(stamp)
		.arg(config.pickupInputLeadFrames)
		.arg(EXPECTED_RECORDS);
}

QJsonObject contractToJson(const SecondHalfCsvContract& contract, const QString& csvPath) {
	QJsonObject object;
	object.insert(QStringLiteral("path"), csvPath);
	object.insert(QStringLiteral("initial_seed_16bit"), formatU16(contract.initialSeed));
	object.insert(QStringLiteral("row_count"), contract.rowCount);
	object.insert(QStringLiteral("t_zero_rows"), contract.tZeroRows);
	object.insert(QStringLiteral("min_frame_from_initial_seed"), contract.minFrame);
	object.insert(QStringLiteral("max_frame_from_initial_seed"), contract.maxFrame);
	return object;
}

bool contractFromJson(const QJsonObject& object, SecondHalfCsvContract* contract) {
	if (!contract) {
		return false;
	}
	uint32_t seed = 0;
	if (!parseUInt(object.value(QStringLiteral("initial_seed_16bit")).toString(), 0xFFFF, &seed)) {
		return false;
	}
	contract->initialSeed = seed;
	contract->rowCount = object.value(QStringLiteral("row_count")).toInt();
	contract->tZeroRows = object.value(QStringLiteral("t_zero_rows")).toInt();
	contract->minFrame = object.value(QStringLiteral("min_frame_from_initial_seed")).toInt();
	contract->maxFrame = object.value(QStringLiteral("max_frame_from_initial_seed")).toInt();
	return true;
}

QJsonArray targetToJsonRow(const SecondHalfTarget& target) {
	QJsonArray row;
	row.append(int(target.upperHalf));
	row.append(target.sweepIndex);
	row.append(target.frameFromInitialSeed);
	row.append(formatU32(target.rngSeed));
	row.append(target.inputFrameFromInitialSeed);
	return row;
}

bool targetFromJsonRow(const QJsonArray& row, SecondHalfTarget* target) {
	if (!target || row.size() != 5) {
		return false;
	}
	uint32_t upper = 0;
	uint32_t rng = 0;
	if (!parseUInt(row.at(0).toVariant().toString(), 0xFFFF, &upper)
		|| !parseUInt(row.at(3).toString(), 0xFFFFFFFFu, &rng)) {
		return false;
	}
	target->upperHalf = upper;
	target->sweepIndex = row.at(1).toInt();
	target->frameFromInitialSeed = row.at(2).toInt();
	target->rngSeed = rng;
	target->inputFrameFromInitialSeed = row.at(4).toInt();
	return true;
}

bool parseSecondHalfCsv(const Phase3Config& config, TargetCacheResult* result, QString* errorMessage) {
	QFile file(config.secondhalfCsvPath);
	if (!file.open(QIODevice::ReadOnly | QIODevice::Text)) {
		if (errorMessage) {
			*errorMessage = QObject::tr("Could not read secondhalf.csv: %1").arg(file.errorString());
		}
		return false;
	}

	const QByteArray headerLine = file.readLine();
	if (headerLine.isEmpty()) {
		if (errorMessage) {
			*errorMessage = QObject::tr("secondhalf.csv is empty.");
		}
		return false;
	}

	const QStringList header = QString::fromUtf8(headerLine).trimmed().split(QLatin1Char(','));
	QHash<QString, int> columns;
	for (int i = 0; i < header.size(); ++i) {
		columns.insert(header.at(i).trimmed(), i);
	}
	const QStringList required = {
		QStringLiteral("initial_seed_16bit"),
		QStringLiteral("target_half_16bit"),
		QStringLiteral("sweep_index"),
		QStringLiteral("frame_from_initial_seed"),
		QStringLiteral("t_minus"),
		QStringLiteral("rng_seed"),
	};
	for (const QString& column : required) {
		if (!columns.contains(column)) {
			if (errorMessage) {
				*errorMessage = QObject::tr("secondhalf.csv missing required column: %1").arg(column);
			}
			return false;
		}
	}

	QVector<SecondHalfTarget> byUpper(EXPECTED_RECORDS);
	QVector<bool> seen(EXPECTED_RECORDS, false);
	QVector<uint16_t> seenUppers;
	QVector<uint16_t> initialSeeds;
	SecondHalfCsvContract contract;
	bool haveMinMax = false;
	int lineNumber = 1;

	while (!file.atEnd()) {
		++lineNumber;
		const QString line = QString::fromUtf8(file.readLine()).trimmed();
		if (line.isEmpty()) {
			continue;
		}
		++contract.rowCount;
		const QStringList cells = line.split(QLatin1Char(','));
		const auto cell = [&cells, lineNumber, errorMessage](int index, const QString& name, QString* out) -> bool {
			if (index >= cells.size()) {
				if (errorMessage) {
					*errorMessage = QObject::tr("secondhalf.csv line %1 missing column %2").arg(lineNumber).arg(name);
				}
				return false;
			}
			*out = cells.at(index).trimmed();
			return true;
		};

		QString rawSeed;
		if (!cell(columns.value(QStringLiteral("initial_seed_16bit")), QStringLiteral("initial_seed_16bit"), &rawSeed)) {
			return false;
		}
		uint32_t parsedSeed = 0;
		if (!parseUInt(rawSeed, 0xFFFF, &parsedSeed)) {
			if (errorMessage) {
				*errorMessage = QObject::tr("Bad initial seed at line %1: %2").arg(lineNumber).arg(rawSeed);
			}
			return false;
		}
		if (!initialSeeds.contains(parsedSeed)) {
			initialSeeds.append(parsedSeed);
		}

		QString rawTMinus;
		if (!cell(columns.value(QStringLiteral("t_minus")), QStringLiteral("t_minus"), &rawTMinus)) {
			return false;
		}
		if (rawTMinus.compare(QStringLiteral("t-0"), Qt::CaseInsensitive) != 0) {
			continue;
		}

		QString rawUpper;
		QString rawSweep;
		QString rawFrame;
		QString rawRng;
		if (!cell(columns.value(QStringLiteral("target_half_16bit")), QStringLiteral("target_half_16bit"), &rawUpper)
			|| !cell(columns.value(QStringLiteral("sweep_index")), QStringLiteral("sweep_index"), &rawSweep)
			|| !cell(columns.value(QStringLiteral("frame_from_initial_seed")), QStringLiteral("frame_from_initial_seed"), &rawFrame)
			|| !cell(columns.value(QStringLiteral("rng_seed")), QStringLiteral("rng_seed"), &rawRng)) {
			return false;
		}

		uint32_t upper = 0;
		uint32_t rng = 0;
		bool sweepOk = false;
		bool frameOk = false;
		const int sweep = rawSweep.toInt(&sweepOk, 0);
		const int frame = rawFrame.toInt(&frameOk, 0);
		if (!parseUInt(rawUpper, 0xFFFF, &upper) || !parseUInt(rawRng, 0xFFFFFFFFu, &rng) || !sweepOk || !frameOk) {
			if (errorMessage) {
				*errorMessage = QObject::tr("Bad t-0 row at line %1").arg(lineNumber);
			}
			return false;
		}
		if (frame < config.pickupInputLeadFrames) {
			if (errorMessage) {
				*errorMessage = QObject::tr("%1 t-0 frame %2 is before pickup lead %3")
					.arg(formatU16(upper))
					.arg(frame)
					.arg(config.pickupInputLeadFrames);
			}
			return false;
		}
		if (seen.at(upper)) {
			if (errorMessage) {
				*errorMessage = QObject::tr("Duplicate t-0 upper half %1 at line %2")
					.arg(formatU16(upper))
					.arg(lineNumber);
			}
			return false;
		}

		SecondHalfTarget target;
		target.upperHalf = upper;
		target.sweepIndex = sweep;
		target.frameFromInitialSeed = frame;
		target.rngSeed = rng;
		target.inputFrameFromInitialSeed = frame - config.pickupInputLeadFrames;
		byUpper[upper] = target;
		seen[upper] = true;
		seenUppers.append(upper);
		contract.minFrame = haveMinMax ? qMin(contract.minFrame, frame) : frame;
		contract.maxFrame = haveMinMax ? qMax(contract.maxFrame, frame) : frame;
		haveMinMax = true;
	}

	if (initialSeeds.size() != 1) {
		if (errorMessage) {
			QStringList seeds;
			for (uint16_t seed : initialSeeds) {
				seeds.append(formatU16(seed));
			}
			*errorMessage = QObject::tr("secondhalf.csv must contain exactly one initial seed, found %1")
				.arg(seeds.join(QStringLiteral(", ")));
		}
		return false;
	}
	if (seenUppers.size() != EXPECTED_RECORDS) {
		if (errorMessage) {
			*errorMessage = QObject::tr("secondhalf.csv must contain %1 unique t-0 rows, found %2")
				.arg(EXPECTED_RECORDS)
				.arg(seenUppers.size());
		}
		return false;
	}

	contract.initialSeed = initialSeeds.first();
	contract.tZeroRows = seenUppers.size();
	result->targets.reserve(seenUppers.size());
	for (uint16_t upper : seenUppers) {
		result->targets.append(byUpper.at(upper));
	}
	std::sort(result->targets.begin(), result->targets.end(), [](const SecondHalfTarget& a, const SecondHalfTarget& b) {
		if (a.inputFrameFromInitialSeed != b.inputFrameFromInitialSeed) {
			return a.inputFrameFromInitialSeed < b.inputFrameFromInitialSeed;
		}
		return a.upperHalf < b.upperHalf;
	});
	result->contract = contract;
	return true;
}

bool readPhase3TargetsCached(const Phase3Config& config, TargetCacheResult* result, QString* errorMessage) {
	const QString keyPayload = targetCacheKeyPayload(config, errorMessage);
	if (keyPayload.isEmpty()) {
		return false;
	}
	const QString key = cacheKey(keyPayload);
	const QString path = QDir(cacheDir(config)).filePath(QStringLiteral("native-secondhalf-t0-%1.json").arg(key));

	QFile cacheFile(path);
	if (cacheFile.open(QIODevice::ReadOnly | QIODevice::Text)) {
		const QJsonDocument document = QJsonDocument::fromJson(cacheFile.readAll());
		const QJsonObject object = document.object();
		if (object.value(QStringLiteral("key")).toString() == key) {
			SecondHalfCsvContract contract;
			QVector<SecondHalfTarget> targets;
			if (contractFromJson(object.value(QStringLiteral("contract")).toObject(), &contract)) {
				const QJsonArray rows = object.value(QStringLiteral("targets")).toArray();
				targets.reserve(rows.size());
				bool ok = true;
				for (const QJsonValue& value : rows) {
					SecondHalfTarget target;
					if (!targetFromJsonRow(value.toArray(), &target)) {
						ok = false;
						break;
					}
					targets.append(target);
				}
				if (ok && targets.size() == EXPECTED_RECORDS) {
					result->contract = contract;
					result->targets = targets;
					result->cache.hit = true;
					result->cache.path = path;
					result->cache.key = key;
					result->cache.targetCount = targets.size();
					return true;
				}
			}
		}
	}

	TargetCacheResult parsed;
	if (!parseSecondHalfCsv(config, &parsed, errorMessage)) {
		return false;
	}

	QJsonArray rows;
	for (const SecondHalfTarget& target : parsed.targets) {
		rows.append(targetToJsonRow(target));
	}
	QJsonObject object;
	object.insert(QStringLiteral("schema_version"), 1);
	object.insert(QStringLiteral("kind"), QStringLiteral("native-phase3-secondhalf-t0-targets"));
	object.insert(QStringLiteral("key"), key);
	object.insert(QStringLiteral("key_payload"), keyPayload);
	object.insert(QStringLiteral("contract"), contractToJson(parsed.contract, config.secondhalfCsvPath));
	object.insert(QStringLiteral("targets"), rows);
	if (!writeJsonAtomic(path, object, errorMessage)) {
		return false;
	}

	*result = parsed;
	result->cache.hit = false;
	result->cache.path = path;
	result->cache.key = key;
	result->cache.targetCount = result->targets.size();
	return true;
}

QString scheduleCacheKeyPayload(const Phase3Config& config, const QString& targetCacheKey, uint32_t observedStartRng) {
	return QStringLiteral("native-phase3-schedule|targets=%1|source=runtime-rng|start=%2|lead=%3|max=%4|records=%5")
		.arg(targetCacheKey)
		.arg(formatU32(observedStartRng))
		.arg(config.pickupInputLeadFrames)
		.arg(config.runtimeScheduleMaxSteps)
		.arg(EXPECTED_RECORDS);
}

QJsonArray pickupTargetToJsonRow(const PickupTarget& target) {
	QJsonArray row;
	row.append(int(target.upperHalf));
	row.append(target.csvSweepIndex);
	row.append(target.csvFrameFromInitialSeed);
	row.append(formatU32(target.csvRngSeed));
	row.append(target.eventStepFromStart);
	row.append(target.inputDeltaFromStart);
	return row;
}

bool pickupTargetFromJsonRow(const QJsonArray& row, PickupTarget* target) {
	if (!target || row.size() != 6) {
		return false;
	}
	uint32_t upper = 0;
	uint32_t rng = 0;
	if (!parseUInt(row.at(0).toVariant().toString(), 0xFFFF, &upper)
		|| !parseUInt(row.at(3).toString(), 0xFFFFFFFFu, &rng)) {
		return false;
	}
	target->upperHalf = upper;
	target->csvSweepIndex = row.at(1).toInt();
	target->csvFrameFromInitialSeed = row.at(2).toInt();
	target->csvRngSeed = rng;
	target->eventStepFromStart = row.at(4).toInt();
	target->inputDeltaFromStart = row.at(5).toInt();
	return true;
}

bool buildRuntimeSchedule(
	const Phase3Config& config,
	const QVector<SecondHalfTarget>& targets,
	uint32_t observedStartRng,
	QVector<PickupTarget>* scheduled,
	QString* errorMessage) {
	QVector<int> indexByUpper(EXPECTED_RECORDS, -1);
	for (int i = 0; i < targets.size(); ++i) {
		indexByUpper[targets.at(i).upperHalf] = i;
	}

	int pending = targets.size();
	uint32_t state = observedStartRng;
	scheduled->clear();
	scheduled->reserve(targets.size());
	for (int eventStep = 1; eventStep <= config.runtimeScheduleMaxSteps; ++eventStep) {
		state = lcrngNext(state);
		const uint16_t upper = state >> 16;
		const int targetIndex = indexByUpper.at(upper);
		if (eventStep < config.pickupInputLeadFrames || targetIndex < 0) {
			continue;
		}

		const SecondHalfTarget& csvTarget = targets.at(targetIndex);
		PickupTarget target;
		target.upperHalf = upper;
		target.csvSweepIndex = csvTarget.sweepIndex;
		target.csvFrameFromInitialSeed = csvTarget.frameFromInitialSeed;
		target.csvRngSeed = csvTarget.rngSeed;
		target.eventStepFromStart = eventStep;
		target.inputDeltaFromStart = eventStep - config.pickupInputLeadFrames;
		scheduled->append(target);
		indexByUpper[upper] = -1;
		--pending;
		if (!pending) {
			return true;
		}
	}

	if (errorMessage) {
		QStringList missing;
		for (int upper = 0; upper < indexByUpper.size() && missing.size() < 8; ++upper) {
			if (indexByUpper.at(upper) >= 0) {
				missing.append(formatU16(upper));
			}
		}
		*errorMessage = QObject::tr("Runtime RNG schedule did not cover all targets: covered=%1 missing=%2 max_steps=%3 sample_missing=[%4]")
			.arg(scheduled->size())
			.arg(pending)
			.arg(config.runtimeScheduleMaxSteps)
			.arg(missing.join(QStringLiteral(", ")));
	}
	return false;
}

bool scheduleNeedsChronologicalSort(const QVector<PickupTarget>& scheduled) {
	for (int i = 1; i < scheduled.size(); ++i) {
		const PickupTarget& previous = scheduled.at(i - 1);
		const PickupTarget& current = scheduled.at(i);
		if (current.inputDeltaFromStart < previous.inputDeltaFromStart || current.eventStepFromStart < previous.eventStepFromStart) {
			return true;
		}
	}
	return false;
}

void ensurePickupScheduleChronological(QVector<PickupTarget>* scheduled) {
	if (!scheduleNeedsChronologicalSort(*scheduled)) {
		return;
	}
	std::stable_sort(scheduled->begin(), scheduled->end(), [](const PickupTarget& left, const PickupTarget& right) {
		if (left.inputDeltaFromStart != right.inputDeltaFromStart) {
			return left.inputDeltaFromStart < right.inputDeltaFromStart;
		}
		if (left.eventStepFromStart != right.eventStepFromStart) {
			return left.eventStepFromStart < right.eventStepFromStart;
		}
		return left.upperHalf < right.upperHalf;
	});
}

bool validatePickupScheduleOrder(const Phase3Config& config, const QVector<PickupTarget>& scheduled, QString* errorMessage) {
	QByteArray seen(BITMAP_BYTES, char(0));
	int previousInputDelta = -1;
	int previousEventStep = -1;
	for (const PickupTarget& target : scheduled) {
		if (target.inputDeltaFromStart < previousInputDelta || target.eventStepFromStart < previousEventStep) {
			if (errorMessage) {
				*errorMessage = QObject::tr("Phase 3 schedule is not chronological at %1").arg(formatU16(target.upperHalf));
			}
			return false;
		}
		if (target.eventStepFromStart - target.inputDeltaFromStart != config.pickupInputLeadFrames) {
			if (errorMessage) {
				*errorMessage = QObject::tr("Phase 3 schedule lead mismatch at %1").arg(formatU16(target.upperHalf));
			}
			return false;
		}
		const int byteIndex = target.upperHalf >> 3;
		const int bitIndex = target.upperHalf & 7;
		if (uchar(seen.at(byteIndex)) & (1 << bitIndex)) {
			if (errorMessage) {
				*errorMessage = QObject::tr("Phase 3 schedule duplicate upper-half target: %1").arg(formatU16(target.upperHalf));
			}
			return false;
		}
		seen[byteIndex] = char(uchar(seen.at(byteIndex)) | (1 << bitIndex));
		previousInputDelta = target.inputDeltaFromStart;
		previousEventStep = target.eventStepFromStart;
	}
	return true;
}

bool buildPhase3ScheduleCached(
	const Phase3Config& config,
	const QVector<SecondHalfTarget>& targets,
	const QString& targetCacheKey,
	uint32_t observedStartRng,
	ScheduleCacheResult* result,
	QString* errorMessage) {
	const QString keyPayload = scheduleCacheKeyPayload(config, targetCacheKey, observedStartRng);
	const QString key = cacheKey(keyPayload);
	const QString path = QDir(cacheDir(config)).filePath(QStringLiteral("native-phase3-schedule-%1.json").arg(key));

	QFile cacheFile(path);
	if (cacheFile.open(QIODevice::ReadOnly | QIODevice::Text)) {
		const QJsonDocument document = QJsonDocument::fromJson(cacheFile.readAll());
		const QJsonObject object = document.object();
		if (object.value(QStringLiteral("key")).toString() == key) {
			const QJsonArray rows = object.value(QStringLiteral("targets")).toArray();
			QVector<PickupTarget> scheduled;
			scheduled.reserve(rows.size());
			bool ok = true;
			for (const QJsonValue& value : rows) {
				PickupTarget target;
				if (!pickupTargetFromJsonRow(value.toArray(), &target)) {
					ok = false;
					break;
				}
				scheduled.append(target);
			}
			if (ok && scheduled.size() == targets.size()) {
				ensurePickupScheduleChronological(&scheduled);
				QString orderError;
				ok = validatePickupScheduleOrder(config, scheduled, &orderError);
			}
			if (ok && scheduled.size() == targets.size()) {
				result->targets = scheduled;
				result->cache.hit = true;
				result->cache.path = path;
				result->cache.key = key;
				result->cache.targetCount = scheduled.size();
				return true;
			}
		}
	}

	QVector<PickupTarget> scheduled;
	if (!buildRuntimeSchedule(config, targets, observedStartRng, &scheduled, errorMessage)) {
		return false;
	}
	// Fresh runtime schedules are already emitted in event-step order. Keep
	// this cheap guard for cache/import safety without sorting every run.
	ensurePickupScheduleChronological(&scheduled);
	if (!validatePickupScheduleOrder(config, scheduled, errorMessage)) {
		return false;
	}
	QJsonArray rows;
	for (const PickupTarget& target : scheduled) {
		rows.append(pickupTargetToJsonRow(target));
	}
	QJsonObject object;
	object.insert(QStringLiteral("schema_version"), 1);
	object.insert(QStringLiteral("kind"), QStringLiteral("native-phase3-pickup-schedule"));
	object.insert(QStringLiteral("key"), key);
	object.insert(QStringLiteral("key_payload"), keyPayload);
	object.insert(QStringLiteral("targets"), rows);
	if (!writeJsonAtomic(path, object, errorMessage)) {
		return false;
	}

	result->targets = scheduled;
	result->cache.hit = false;
	result->cache.path = path;
	result->cache.key = key;
	result->cache.targetCount = scheduled.size();
	return true;
}

void runFrames(mCore* core, uint32_t keys, int frames) {
	core->setKeys(core, static_cast<int>(keys));
	for (int i = 0; i < frames; ++i) {
		core->runFrame(core);
	}
}

void readBoxedPartyRecord(mCore* core, int slotNumber, QByteArray* record) {
	if (!record) {
		return;
	}
	if (record->size() != BOX_SLOT_SIZE) {
		record->resize(BOX_SLOT_SIZE);
	}
	const uint32_t address = GPLAYER_PARTY_ADDR + (slotNumber - 1) * PARTY_SLOT_SIZE;
	for (int i = 0; i < BOX_SLOT_SIZE; ++i) {
		(*record)[i] = char(core->busRead8(core, address + i) & 0xFF);
	}
}

uint32_t readPartyPid(mCore* core, int slotNumber) {
	const uint32_t address = GPLAYER_PARTY_ADDR + (slotNumber - 1) * PARTY_SLOT_SIZE;
	uint32_t pid = 0;
	for (int i = 0; i < 4; ++i) {
		pid |= uint32_t(core->busRead8(core, address + i) & 0xFF) << (8 * i);
	}
	return pid;
}

uint32_t readPid(const QByteArray& record) {
	const uchar* data = reinterpret_cast<const uchar*>(record.constData());
	return uint32_t(data[0]) | (uint32_t(data[1]) << 8) | (uint32_t(data[2]) << 16) | (uint32_t(data[3]) << 24);
}

bool bitmapPresent(const QByteArray& bitmap, int upperHalf) {
	return bitmap.at(upperHalf >> 3) & (1 << (upperHalf & 7));
}

QString pk3FileName(uint32_t pid) {
	return QStringLiteral("%1.pk3").arg(formatU32(pid));
}

QString presentPk3RecordsSha1(const QByteArray& block, const QByteArray& bitmap) {
	QCryptographicHash hash(QCryptographicHash::Sha1);
	for (int upperHalf = 0; upperHalf < EXPECTED_RECORDS; ++upperHalf) {
		if (!bitmapPresent(bitmap, upperHalf)) {
			continue;
		}
		const int offset = upperHalf * BOX_SLOT_SIZE;
		hash.addData(block.constData() + offset, BOX_SLOT_SIZE);
	}
	return QString::fromLatin1(hash.result().toHex());
}

void appendLe16(QByteArray* bytes, uint16_t value) {
	bytes->append(char(value & 0xFF));
	bytes->append(char((value >> 8) & 0xFF));
}

void appendLe32(QByteArray* bytes, uint32_t value) {
	appendLe16(bytes, uint16_t(value & 0xFFFF));
	appendLe16(bytes, uint16_t((value >> 16) & 0xFFFF));
}

void appendLe64(QByteArray* bytes, uint64_t value) {
	appendLe32(bytes, uint32_t(value & 0xFFFFFFFFu));
	appendLe32(bytes, uint32_t((value >> 32) & 0xFFFFFFFFu));
}

bool deflateRaw(z_stream* stream, const QByteArray& input, QByteArray* output, QString* errorMessage) {
	const int resetStatus = deflateReset(stream);
	if (resetStatus != Z_OK) {
		if (errorMessage) {
			*errorMessage = QObject::tr("Could not reset ZIP deflate stream: %1").arg(resetStatus);
		}
		return false;
	}

	output->resize(int(compressBound(uLong(input.size()))));
	stream->next_in = reinterpret_cast<Bytef*>(const_cast<char*>(input.constData()));
	stream->avail_in = uInt(input.size());
	stream->next_out = reinterpret_cast<Bytef*>(output->data());
	stream->avail_out = uInt(output->size());

	const int status = deflate(stream, Z_FINISH);
	if (status != Z_STREAM_END) {
		if (errorMessage) {
			*errorMessage = QObject::tr("Could not deflate ZIP entry: %1").arg(status);
		}
		return false;
	}

	output->resize(int(stream->total_out));
	return true;
}

bool appendMemoryZipEntry(
	z_stream* deflater,
	QByteArray* zipBytes,
	std::vector<ZipCentralEntry>* entries,
	QByteArray* compressedScratch,
	const QByteArray& name,
	const QByteArray& record,
	QString* errorMessage) {
	if (uint64_t(zipBytes->size()) > std::numeric_limits<uint32_t>::max()) {
		if (errorMessage) {
			*errorMessage = QObject::tr("ZIP grew beyond 32-bit local-header offsets");
		}
		return false;
	}

	if (!deflateRaw(deflater, record, compressedScratch, errorMessage)) {
		return false;
	}
	if (uint64_t(compressedScratch->size()) > std::numeric_limits<uint32_t>::max() ||
		uint64_t(record.size()) > std::numeric_limits<uint32_t>::max()) {
		if (errorMessage) {
			*errorMessage = QObject::tr("ZIP entry is too large: %1").arg(QString::fromUtf8(name));
		}
		return false;
	}

	const uint32_t localHeaderOffset = uint32_t(zipBytes->size());
	const uint32_t entryCrc = uint32_t(::crc32(::crc32(0L, Z_NULL, 0),
		reinterpret_cast<const Bytef*>(record.constData()), uInt(record.size())));
	const uint32_t compressedSize = uint32_t(compressedScratch->size());
	const uint32_t uncompressedSize = uint32_t(record.size());

	appendLe32(zipBytes, 0x04034B50);
	appendLe16(zipBytes, 20);
	appendLe16(zipBytes, 0);
	appendLe16(zipBytes, ZIP_METHOD_DEFLATE);
	appendLe16(zipBytes, ZIP_DOS_TIME_MIDNIGHT);
	appendLe16(zipBytes, ZIP_DOS_DATE_2026_01_01);
	appendLe32(zipBytes, entryCrc);
	appendLe32(zipBytes, compressedSize);
	appendLe32(zipBytes, uncompressedSize);
	appendLe16(zipBytes, uint16_t(name.size()));
	appendLe16(zipBytes, 0);
	zipBytes->append(name);
	zipBytes->append(*compressedScratch);

	entries->push_back({name, entryCrc, compressedSize, uncompressedSize, localHeaderOffset});
	return true;
}

bool buildPhase3ZipBytes(
	const Phase3Config& config,
	const QByteArray& block,
	const QByteArray& bitmap,
	int expectedEntries,
	QByteArray* zipBytes,
	QString* errorMessage) {
	zipBytes->clear();
	const int estimatedEntryBytes = int(compressBound(BOX_SLOT_SIZE)) + 128;
	zipBytes->reserve(qMax(1, expectedEntries) * estimatedEntryBytes);
	std::vector<ZipCentralEntry> entries;
	entries.reserve(size_t(qMax(0, expectedEntries)));
	ZipDeflater deflater;
	if (!deflater.init(errorMessage)) {
		return false;
	}
	QByteArray compressedScratch;
	compressedScratch.reserve(int(compressBound(BOX_SLOT_SIZE)));

	int entriesWritten = 0;
	for (int upperHalf = 0; upperHalf < EXPECTED_RECORDS; ++upperHalf) {
		if (!bitmapPresent(bitmap, upperHalf)) {
			continue;
		}
		const int offset = upperHalf * BOX_SLOT_SIZE;
		const QByteArray record = QByteArray::fromRawData(block.constData() + offset, BOX_SLOT_SIZE);
		const uint32_t pid = readPid(record);
		const uint32_t expectedPid = (uint32_t(upperHalf) << 16) | config.laneId;
		if (pid != expectedPid) {
			if (errorMessage) {
				*errorMessage = QObject::tr("Refusing to write bad PK3 entry: upper=%1 expected_pid=%2 observed_pid=%3")
					.arg(formatU16(upperHalf))
					.arg(formatU32(expectedPid))
					.arg(formatU32(pid));
			}
			return false;
		}
		if (!appendMemoryZipEntry(
			&deflater.stream, zipBytes, &entries, &compressedScratch,
			pk3FileName(pid).toUtf8(), record, errorMessage)) {
			return false;
		}
		++entriesWritten;
	}
	if (entriesWritten != expectedEntries) {
		if (errorMessage) {
			*errorMessage = QObject::tr("ZIP PK3 entry count mismatch: expected=%1 written=%2")
				.arg(expectedEntries)
				.arg(entriesWritten);
		}
		return false;
	}
	if (uint64_t(zipBytes->size()) > std::numeric_limits<uint32_t>::max()) {
		if (errorMessage) {
			*errorMessage = QObject::tr("ZIP central directory offset exceeds 32-bit ZIP limit");
		}
		return false;
	}

	const uint64_t centralDirectoryOffset = uint64_t(zipBytes->size());
	QByteArray centralDirectory;
	centralDirectory.reserve(entriesWritten * 64);
	for (const ZipCentralEntry& entry : entries) {
		appendLe32(&centralDirectory, 0x02014B50);
		appendLe16(&centralDirectory, 20);
		appendLe16(&centralDirectory, 20);
		appendLe16(&centralDirectory, 0);
		appendLe16(&centralDirectory, ZIP_METHOD_DEFLATE);
		appendLe16(&centralDirectory, ZIP_DOS_TIME_MIDNIGHT);
		appendLe16(&centralDirectory, ZIP_DOS_DATE_2026_01_01);
		appendLe32(&centralDirectory, entry.crc32);
		appendLe32(&centralDirectory, entry.compressedSize);
		appendLe32(&centralDirectory, entry.uncompressedSize);
		appendLe16(&centralDirectory, uint16_t(entry.name.size()));
		appendLe16(&centralDirectory, 0);
		appendLe16(&centralDirectory, 0);
		appendLe16(&centralDirectory, 0);
		appendLe16(&centralDirectory, 0);
		appendLe32(&centralDirectory, 0);
		appendLe32(&centralDirectory, entry.localHeaderOffset);
		centralDirectory.append(entry.name);
	}

	const uint64_t centralDirectorySize = uint64_t(centralDirectory.size());
	zipBytes->append(centralDirectory);
	if (entriesWritten >= 0xFFFF) {
		const uint64_t zip64EndOffset = uint64_t(zipBytes->size());
		appendLe32(zipBytes, 0x06064B50);
		appendLe64(zipBytes, 44);
		appendLe16(zipBytes, 45);
		appendLe16(zipBytes, 45);
		appendLe32(zipBytes, 0);
		appendLe32(zipBytes, 0);
		appendLe64(zipBytes, uint64_t(entriesWritten));
		appendLe64(zipBytes, uint64_t(entriesWritten));
		appendLe64(zipBytes, centralDirectorySize);
		appendLe64(zipBytes, centralDirectoryOffset);

		appendLe32(zipBytes, 0x07064B50);
		appendLe32(zipBytes, 0);
		appendLe64(zipBytes, zip64EndOffset);
		appendLe32(zipBytes, 1);
	}

	appendLe32(zipBytes, 0x06054B50);
	appendLe16(zipBytes, 0);
	appendLe16(zipBytes, 0);
	appendLe16(zipBytes, entriesWritten >= 0xFFFF ? 0xFFFF : uint16_t(entriesWritten));
	appendLe16(zipBytes, entriesWritten >= 0xFFFF ? 0xFFFF : uint16_t(entriesWritten));
	appendLe32(zipBytes, uint32_t(centralDirectorySize));
	appendLe32(zipBytes, uint32_t(centralDirectoryOffset));
	appendLe16(zipBytes, 0);
	return true;
}

bool replaceFileWithTemp(const QString& tempPath, const QString& finalPath, bool overwrite, QString* errorMessage) {
#ifdef Q_OS_WIN
	const std::wstring tempNative = QDir::toNativeSeparators(tempPath).toStdWString();
	const std::wstring finalNative = QDir::toNativeSeparators(finalPath).toStdWString();
	const DWORD moveFlags = MOVEFILE_WRITE_THROUGH | (overwrite ? MOVEFILE_REPLACE_EXISTING : 0);
	if (!MoveFileExW(tempNative.c_str(), finalNative.c_str(), moveFlags)) {
		const DWORD error = GetLastError();
		QFile::remove(tempPath);
		if (errorMessage) {
			*errorMessage = !overwrite && (error == ERROR_ALREADY_EXISTS || error == ERROR_FILE_EXISTS)
				? QObject::tr("Output already exists. Enable overwrite or choose another folder: %1").arg(finalPath)
				: QObject::tr("Could not move ZIP into place: %1 (Windows error %2)")
					.arg(finalPath)
					.arg(uint(error));
		}
		return false;
	}
	return true;
#else
	const QByteArray tempNative = QFile::encodeName(tempPath);
	const QByteArray finalNative = QFile::encodeName(finalPath);
	if (overwrite) {
		// POSIX rename replaces an existing destination atomically when source
		// and destination are on the same filesystem. Do not pre-delete the final ZIP.
		// If rename fails, the previous verified archive remains.
		if (std::rename(tempNative.constData(), finalNative.constData()) != 0) {
			const int errorCode = errno;
			QFile::remove(tempPath);
			if (errorMessage) {
				*errorMessage = QObject::tr("Could not move ZIP into place: %1 (errno %2)")
					.arg(finalPath)
					.arg(errorCode);
			}
			return false;
		}
	} else {
		// No-overwrite publish uses hard-link creation as an atomic final-name
		// claim. If another worker creates the ZIP during generation, link()
		// fails with EEXIST and the competing final archive is preserved.
		if (link(tempNative.constData(), finalNative.constData()) != 0) {
			const int errorCode = errno;
			QFile::remove(tempPath);
			if (errorMessage) {
				*errorMessage = errorCode == EEXIST
					? QObject::tr("Output already exists. Enable overwrite or choose another folder: %1").arg(finalPath)
					: QObject::tr("Could not move ZIP into place: %1 (errno %2)").arg(finalPath).arg(errorCode);
			}
			return false;
		}
		QFile::remove(tempPath);
	}
	return true;
#endif
}

QString phase3ZipTempPath(const QString& finalPath) {
	// Process-local temp names prevent duplicate same-lane workers from
	// truncating each other's in-progress archive before final publish.
	return finalPath + QStringLiteral(".pid%1.tmp").arg(QCoreApplication::applicationPid());
}

bool writePhase3Zip(
	const Phase3Config& config,
	const QByteArray& block,
	const QByteArray& bitmap,
	int expectedEntries,
	QString* errorMessage) {
	QByteArray zipBytes;
	if (!buildPhase3ZipBytes(config, block, bitmap, expectedEntries, &zipBytes, errorMessage)) {
		return false;
	}

	QDir().mkpath(config.outputDir);
	const QString finalPath = outputZipPath(config);
	const QString tempPath = phase3ZipTempPath(finalPath);
	QFile::remove(tempPath);

	QFile tempFile(tempPath);
	if (!tempFile.open(QIODevice::WriteOnly | QIODevice::Truncate)) {
		if (errorMessage) {
			*errorMessage = QObject::tr("Could not create ZIP temp file: %1").arg(tempPath);
		}
		return false;
	}
	const qint64 written = tempFile.write(zipBytes);
	if (written != zipBytes.size() || !tempFile.flush()) {
		tempFile.close();
		QFile::remove(tempPath);
		if (errorMessage) {
			*errorMessage = QObject::tr("Could not write complete in-memory ZIP to temp file: %1").arg(tempPath);
		}
		return false;
	}
	tempFile.close();
	if (tempFile.error() != QFileDevice::NoError) {
		QFile::remove(tempPath);
		if (errorMessage) {
			*errorMessage = QObject::tr("Could not close ZIP temp file: %1").arg(tempPath);
		}
		return false;
	}

	return replaceFileWithTemp(tempPath, finalPath, config.overwrite, errorMessage);
}

}

struct SpindaProjectView::Ui {
	QLineEdit* phase2State = nullptr;
	QLineEdit* secondhalfCsv = nullptr;
	QLineEdit* outputDir = nullptr;
	QLineEdit* cacheDir = nullptr;
	QSpinBox* laneId = nullptr;
	QSpinBox* pickupLead = nullptr;
	QSpinBox* pickupHold = nullptr;
	QSpinBox* postPickup = nullptr;
	QSpinBox* minPickupDetect = nullptr;
	QSpinBox* fastPickupFirst = nullptr;
	QSpinBox* fastPickupSecond = nullptr;
	QSpinBox* maxPickupDetect = nullptr;
	QSpinBox* learnPickupDelaySamples = nullptr;
	QSpinBox* maxSteps = nullptr;
	QSpinBox* limit = nullptr;
	QLineEdit* expectedRng = nullptr;
	QCheckBox* checkExpectedRng = nullptr;
	QCheckBox* overwrite = nullptr;
	QCheckBox* dynamicPickupDetection = nullptr;
	QCheckBox* fastPickupChecks = nullptr;
	QCheckBox* learnPickupDelay = nullptr;
	QCheckBox* audioKillswitch = nullptr;
	QCheckBox* noRender = nullptr;
	QCheckBox* fastForward = nullptr;
	QProgressBar* progress = nullptr;
	QLabel* status = nullptr;
	QPlainTextEdit* log = nullptr;
	QPushButton* start = nullptr;
	QPushButton* cancel = nullptr;
};

SpindaProjectView::SpindaProjectView(QWidget* parent)
	: QDialog(parent)
	, m_ui(std::make_unique<Ui>()) {
	setWindowTitle(tr("Spinda project"));
	setModal(false);
	resize(780, 620);
	applyWindowsDarkChrome(this);

	const QString root = defaultWorkspaceRoot();
	QVBoxLayout* layout = new QVBoxLayout(this);

	QLabel* intro = new QLabel(tr(
		"Native Phase 3 lane builder. Uses the visible Qt core directly, keeps extracted boxed PK3 records in RAM, "
		"then writes one compressed lane ZIP at completion."));
	intro->setWordWrap(true);
	layout->addWidget(intro);

	QFormLayout* form = new QFormLayout();
	auto addPathRow = [this, form](const QString& label, QLineEdit** edit, void (SpindaProjectView::*browseSlot)()) {
		QWidget* rowWidget = new QWidget(this);
		QHBoxLayout* row = new QHBoxLayout(rowWidget);
		row->setContentsMargins(0, 0, 0, 0);
		*edit = new QLineEdit(rowWidget);
		QPushButton* browse = new QPushButton(tr("Browse..."), rowWidget);
		row->addWidget(*edit, 1);
		row->addWidget(browse);
		connect(browse, &QPushButton::clicked, this, browseSlot);
		form->addRow(label, rowWidget);
	};

	addPathRow(tr("Phase 2 state:"), &m_ui->phase2State, &SpindaProjectView::browsePhase2State);
	addPathRow(tr("secondhalf.csv:"), &m_ui->secondhalfCsv, &SpindaProjectView::browseSecondHalfCsv);
	addPathRow(tr("Output folder:"), &m_ui->outputDir, &SpindaProjectView::browseOutputDir);
	addPathRow(tr("Shared cache folder:"), &m_ui->cacheDir, &SpindaProjectView::browseCacheDir);
	m_ui->phase2State->setText(QDir(root).filePath(QStringLiteral("Phase2PickupStates/0x0001.ss0")));
	m_ui->secondhalfCsv->setText(QDir(root).filePath(QStringLiteral("build-mingw64-python-qt/secondhalf.csv")));
	m_ui->outputDir->setText(QDir(root).filePath(QStringLiteral("Phase3SpindaBlocks")));
	m_ui->cacheDir->setText(QDir(root).filePath(QStringLiteral("Phase3SpindaBlocks/_cache")));

	m_ui->laneId = new QSpinBox(this);
	m_ui->laneId->setRange(0, 0xFFFF);
	m_ui->laneId->setValue(0x0001);
	m_ui->laneId->setDisplayIntegerBase(16);
	form->addRow(tr("Lane lower half:"), m_ui->laneId);

	m_ui->pickupLead = new QSpinBox(this);
	m_ui->pickupLead->setRange(0, 120);
	m_ui->pickupLead->setValue(3);
	form->addRow(tr("Pickup A lead frames:"), m_ui->pickupLead);

	m_ui->pickupHold = new QSpinBox(this);
	m_ui->pickupHold->setRange(1, 120);
	m_ui->pickupHold->setValue(1);
	form->addRow(tr("A hold frames:"), m_ui->pickupHold);

	m_ui->postPickup = new QSpinBox(this);
	m_ui->postPickup->setRange(0, 120);
	m_ui->postPickup->setValue(24);
	form->addRow(tr("Fixed post-pickup wait frames:"), m_ui->postPickup);

	m_ui->minPickupDetect = new QSpinBox(this);
	m_ui->minPickupDetect->setRange(0, 120);
	m_ui->minPickupDetect->setValue(4);
	form->addRow(tr("Detection floor frame:"), m_ui->minPickupDetect);

	m_ui->fastPickupFirst = new QSpinBox(this);
	m_ui->fastPickupFirst->setRange(0, 120);
	m_ui->fastPickupFirst->setValue(4);
	form->addRow(tr("Fast check first frame:"), m_ui->fastPickupFirst);

	m_ui->fastPickupSecond = new QSpinBox(this);
	m_ui->fastPickupSecond->setRange(0, 120);
	m_ui->fastPickupSecond->setValue(5);
	form->addRow(tr("Fast check second frame:"), m_ui->fastPickupSecond);

	m_ui->maxPickupDetect = new QSpinBox(this);
	m_ui->maxPickupDetect->setRange(1, 120);
	m_ui->maxPickupDetect->setValue(12);
	form->addRow(tr("Detection max frames:"), m_ui->maxPickupDetect);

	m_ui->learnPickupDelaySamples = new QSpinBox(this);
	m_ui->learnPickupDelaySamples->setRange(1, 4096);
	m_ui->learnPickupDelaySamples->setValue(DEFAULT_LEARN_PICKUP_DELAY_SAMPLES);
	form->addRow(tr("Learn delay sample count:"), m_ui->learnPickupDelaySamples);

	m_ui->maxSteps = new QSpinBox(this);
	m_ui->maxSteps->setRange(1, 100000000);
	m_ui->maxSteps->setValue(4000000);
	form->addRow(tr("Runtime schedule max steps:"), m_ui->maxSteps);

	m_ui->limit = new QSpinBox(this);
	m_ui->limit->setRange(0, EXPECTED_RECORDS);
	m_ui->limit->setValue(0);
	form->addRow(tr("Limit records (0 = full):"), m_ui->limit);

	m_ui->expectedRng = new QLineEdit(QStringLiteral("0x2B0C94C1"), this);
	form->addRow(tr("Expected start gRngValue:"), m_ui->expectedRng);

	layout->addLayout(form);

	m_ui->checkExpectedRng = new QCheckBox(tr("Require expected start gRngValue"), this);
	m_ui->checkExpectedRng->setChecked(true);
	layout->addWidget(m_ui->checkExpectedRng);

	m_ui->overwrite = new QCheckBox(tr("Overwrite existing output ZIP"), this);
	layout->addWidget(m_ui->overwrite);

	m_ui->dynamicPickupDetection = new QCheckBox(tr("Detect pickup as soon as expected PID appears"), this);
	m_ui->dynamicPickupDetection->setChecked(true);
	layout->addWidget(m_ui->dynamicPickupDetection);

	m_ui->fastPickupChecks = new QCheckBox(tr("Use fast pickup checks before fallback scan"), this);
	m_ui->fastPickupChecks->setChecked(true);
	layout->addWidget(m_ui->fastPickupChecks);

	m_ui->learnPickupDelay = new QCheckBox(tr("Learn stable pickup delay after sample window"), this);
	m_ui->learnPickupDelay->setChecked(true);
	layout->addWidget(m_ui->learnPickupDelay);

	m_ui->audioKillswitch = new QCheckBox(tr("Enable Audio killswitch for run"), this);
	m_ui->noRender = new QCheckBox(tr("Enable no-render mode for run"), this);
	m_ui->fastForward = new QCheckBox(tr("Enable unbounded fast-forward for run"), this);
	m_ui->audioKillswitch->setChecked(true);
	m_ui->noRender->setChecked(true);
	m_ui->fastForward->setChecked(true);
	layout->addWidget(m_ui->audioKillswitch);
	layout->addWidget(m_ui->noRender);
	layout->addWidget(m_ui->fastForward);

	m_ui->progress = new QProgressBar(this);
	m_ui->progress->setRange(0, EXPECTED_RECORDS);
	m_ui->progress->setValue(0);
	layout->addWidget(m_ui->progress);

	m_ui->status = new QLabel(tr("Idle"), this);
	layout->addWidget(m_ui->status);

	m_ui->log = new QPlainTextEdit(this);
	m_ui->log->setReadOnly(true);
	m_ui->log->setMaximumBlockCount(1000);
	layout->addWidget(m_ui->log, 1);

	QDialogButtonBox* buttons = new QDialogButtonBox(QDialogButtonBox::Close, this);
	m_ui->start = buttons->addButton(tr("Build native Phase 3 lane"), QDialogButtonBox::ActionRole);
	m_ui->cancel = buttons->addButton(tr("Cancel run"), QDialogButtonBox::ActionRole);
	m_ui->cancel->setEnabled(false);
	connect(m_ui->start, &QPushButton::clicked, this, &SpindaProjectView::startPhase3Lane);
	connect(m_ui->cancel, &QPushButton::clicked, this, &SpindaProjectView::cancelRun);
	connect(buttons, &QDialogButtonBox::rejected, this, &QDialog::reject);
	layout->addWidget(buttons);
}

SpindaProjectView::~SpindaProjectView() = default;

void SpindaProjectView::setController(std::shared_ptr<CoreController> controller) {
	m_controller = controller;
}

bool SpindaProjectView::configureFromEnvironment(QString* errorMessage) {
	auto applyUInt = [errorMessage](const char* name, QSpinBox* spinBox, uint32_t maxValue, const QString& label) {
		const QString raw = nativePhase3EnvString(name);
		if (raw.isEmpty()) {
			return true;
		}
		uint32_t value = 0;
		if (!parseUInt(raw, maxValue, &value)) {
			if (errorMessage) {
				*errorMessage = QObject::tr("Bad %1 in native Phase 3 environment: %2").arg(label, raw);
			}
			return false;
		}
		spinBox->setValue(int(value));
		return true;
	};
	auto applyBool = [errorMessage](const char* name, QCheckBox* checkBox, const QString& label) {
		const QString raw = nativePhase3EnvString(name);
		if (raw.isEmpty()) {
			return true;
		}
		QString boolError;
		const bool value = nativePhase3EnvBool(name, checkBox->isChecked(), &boolError);
		if (!boolError.isEmpty()) {
			if (errorMessage) {
				*errorMessage = QObject::tr("Bad %1 in native Phase 3 environment: %2").arg(label, raw);
			}
			return false;
		}
		checkBox->setChecked(value);
		return true;
	};

	if (!applyUInt("LANE_ID", m_ui->laneId, 0xFFFF, tr("lane id"))
		|| !applyUInt("PICKUP_INPUT_LEAD_FRAMES", m_ui->pickupLead, 120, tr("pickup input lead frames"))
		|| !applyUInt("PICKUP_HOLD_FRAMES", m_ui->pickupHold, 120, tr("pickup hold frames"))
		|| !applyUInt("POST_PICKUP_FRAMES", m_ui->postPickup, 120, tr("post-pickup frames"))
		|| !applyUInt("MIN_PICKUP_DETECT_FRAME", m_ui->minPickupDetect, 120, tr("minimum pickup detect frame"))
		|| !applyUInt("FAST_PICKUP_CHECK_FIRST_FRAME", m_ui->fastPickupFirst, 120, tr("fast pickup first check frame"))
		|| !applyUInt("FAST_PICKUP_CHECK_SECOND_FRAME", m_ui->fastPickupSecond, 120, tr("fast pickup second check frame"))
		|| !applyUInt("MAX_PICKUP_DETECT_FRAMES", m_ui->maxPickupDetect, 120, tr("max pickup detect frames"))
		|| !applyUInt("LEARN_PICKUP_DELAY_SAMPLES", m_ui->learnPickupDelaySamples, 4096, tr("learn pickup delay samples"))
		|| !applyUInt("RUNTIME_SCHEDULE_MAX_STEPS", m_ui->maxSteps, 100000000, tr("runtime schedule max steps"))
		|| !applyUInt("LIMIT", m_ui->limit, EXPECTED_RECORDS, tr("record limit"))) {
		return false;
	}

	const uint32_t laneId = uint32_t(m_ui->laneId->value()) & 0xFFFF;
	const QString defaultStatePath = QDir(defaultWorkspaceRoot()).filePath(QStringLiteral("Phase2PickupStates/%1.ss0").arg(formatU16(laneId)));
	m_ui->phase2State->setText(defaultStatePath);

	const QString phase2State = nativePhase3EnvString("PHASE2_STATE");
	if (!phase2State.isEmpty()) {
		m_ui->phase2State->setText(QDir::cleanPath(phase2State));
	}
	const QString secondhalfCsv = nativePhase3EnvString("SECONDHALF_CSV");
	if (!secondhalfCsv.isEmpty()) {
		m_ui->secondhalfCsv->setText(QDir::cleanPath(secondhalfCsv));
	}
	const QString outputDir = nativePhase3EnvString("OUTPUT_DIR");
	if (!outputDir.isEmpty()) {
		m_ui->outputDir->setText(QDir::cleanPath(outputDir));
	}
	const QString cacheDir = nativePhase3EnvString("CACHE_DIR");
	if (!cacheDir.isEmpty()) {
		m_ui->cacheDir->setText(QDir::cleanPath(cacheDir));
	}
	const QString expectedRng = nativePhase3EnvString("EXPECTED_BASELINE_RNG");
	if (!expectedRng.isEmpty()) {
		m_ui->expectedRng->setText(expectedRng);
	}

	return applyBool("CHECK_EXPECTED_RNG", m_ui->checkExpectedRng, tr("expected RNG check"))
		&& applyBool("OVERWRITE", m_ui->overwrite, tr("overwrite flag"))
		&& applyBool("DYNAMIC_PICKUP_DETECTION", m_ui->dynamicPickupDetection, tr("dynamic pickup detection flag"))
		&& applyBool("FAST_PICKUP_CHECKS", m_ui->fastPickupChecks, tr("fast pickup checks flag"))
		&& applyBool("LEARN_PICKUP_DELAY", m_ui->learnPickupDelay, tr("learn pickup delay flag"))
		&& applyBool("ENABLE_AUDIO_KILLSWITCH", m_ui->audioKillswitch, tr("Audio killswitch flag"))
		&& applyBool("ENABLE_NO_RENDER", m_ui->noRender, tr("no-render flag"))
		&& applyBool("ENABLE_FAST_FORWARD", m_ui->fastForward, tr("fast-forward flag"));
}

void SpindaProjectView::startPhase3LaneFromEnvironment() {
	QString errorMessage;
	const bool exitOnComplete = nativePhase3EnvBool("EXIT_ON_COMPLETE", false);
	const bool suppressDialogs = nativePhase3EnvBool("SUPPRESS_DIALOGS", true);
	const bool hasLaneBundle = !nativePhase3EnvString("LANE_IDS").isEmpty();
	if (!configureFromEnvironment(&errorMessage)) {
		appendLog(errorMessage);
		m_ui->status->setText(tr("Failed"));
		if (exitOnComplete) {
			QCoreApplication::exit(1);
			return;
		}
		if (!suppressDialogs) {
			QMessageBox box(QMessageBox::Warning, tr("Spinda project"), errorMessage, QMessageBox::Ok, this);
			applyWindowsDarkChrome(&box);
			box.exec();
		}
		return;
	}
	if (hasLaneBundle) {
		QVector<uint16_t> lanes;
		if (!parseLaneList(nativePhase3EnvString("LANE_IDS"), &lanes, &errorMessage)) {
			appendLog(errorMessage);
			m_ui->status->setText(tr("Failed"));
			if (exitOnComplete) {
				QCoreApplication::exit(1);
				return;
			}
			if (!suppressDialogs) {
				QMessageBox box(QMessageBox::Warning, tr("Spinda project"), errorMessage, QMessageBox::Ok, this);
				applyWindowsDarkChrome(&box);
				box.exec();
			}
			return;
		}
		if (lanes.size() > 1 && !nativePhase3EnvString("PHASE2_STATE").isEmpty()) {
			errorMessage = tr("MGBA_SPINDA_NATIVE_PHASE3_PHASE2_STATE cannot be used with LANE_IDS; use default per-lane Phase2PickupStates paths.");
			appendLog(errorMessage);
			m_ui->status->setText(tr("Failed"));
			if (exitOnComplete) {
				QCoreApplication::exit(1);
				return;
			}
			if (!suppressDialogs) {
				QMessageBox box(QMessageBox::Warning, tr("Spinda project"), errorMessage, QMessageBox::Ok, this);
				applyWindowsDarkChrome(&box);
				box.exec();
			}
			return;
		}

		m_cancelRequested = false;
		setRunning(true);
		bool ok = true;
		for (uint16_t lane : lanes) {
			m_ui->laneId->setValue(lane);
			m_ui->phase2State->setText(QDir(defaultWorkspaceRoot()).filePath(QStringLiteral("Phase2PickupStates/%1.ss0").arg(formatU16(lane))));
			appendLog(tr("Starting bundled native Phase 3 lane %1.").arg(formatU16(lane)));
			if (!runNativePhase3(&errorMessage)) {
				ok = false;
				break;
			}
		}
		setRunning(false);
		m_ui->status->setText(ok ? tr("Complete") : tr("Failed"));
		if (!ok) {
			appendLog(errorMessage);
		}
		if (exitOnComplete) {
			QCoreApplication::exit(ok ? 0 : 1);
			return;
		}
		if (!suppressDialogs) {
			QMessageBox box(ok ? QMessageBox::Information : QMessageBox::Warning, tr("Spinda project"),
				ok ? tr("Native Phase 3 lane bundle written.") : errorMessage, QMessageBox::Ok, this);
			applyWindowsDarkChrome(&box);
			box.exec();
		}
		return;
	}
	startPhase3LaneWithOptions(suppressDialogs, exitOnComplete);
}

void SpindaProjectView::closeEvent(QCloseEvent* event) {
	if (m_running) {
		m_cancelRequested = true;
		event->ignore();
		appendLog(tr("Cancel requested. Waiting for current safe point."));
		return;
	}
	QDialog::closeEvent(event);
}

QString SpindaProjectView::defaultWorkspaceRoot() const {
	QDir appDir(QCoreApplication::applicationDirPath());
	if (appDir.dirName().startsWith(QStringLiteral("build-"))) {
		appDir.cdUp();
	}
	return appDir.absolutePath();
}

void SpindaProjectView::appendLog(const QString& message) {
	m_ui->log->appendPlainText(QStringLiteral("[%1] %2")
		.arg(QDateTime::currentDateTime().toString(QStringLiteral("HH:mm:ss")))
		.arg(message));
}

void SpindaProjectView::setRunning(bool running) {
	m_running = running;
	m_ui->start->setEnabled(!running);
	m_ui->cancel->setEnabled(running);
}

void SpindaProjectView::browsePhase2State() {
	const QString path = QFileDialog::getOpenFileName(this, tr("Select Phase 2 savestate"), QFileInfo(m_ui->phase2State->text()).absolutePath(), tr("Savestates (*.ss0 *.ss1 *.ss2 *.ss3 *.ss4 *.ss5 *.ss6 *.ss7 *.ss8 *.ss9);;All files (*)"));
	if (!path.isEmpty()) {
		m_ui->phase2State->setText(path);
	}
}

void SpindaProjectView::browseSecondHalfCsv() {
	const QString path = QFileDialog::getOpenFileName(this, tr("Select secondhalf.csv"), QFileInfo(m_ui->secondhalfCsv->text()).absolutePath(), tr("CSV files (*.csv);;All files (*)"));
	if (!path.isEmpty()) {
		m_ui->secondhalfCsv->setText(path);
	}
}

void SpindaProjectView::browseOutputDir() {
	const QString path = QFileDialog::getExistingDirectory(this, tr("Select Phase 3 output folder"), m_ui->outputDir->text());
	if (!path.isEmpty()) {
		m_ui->outputDir->setText(path);
	}
}

void SpindaProjectView::browseCacheDir() {
	const QString path = QFileDialog::getExistingDirectory(this, tr("Select shared Phase 3 cache folder"), m_ui->cacheDir->text());
	if (!path.isEmpty()) {
		m_ui->cacheDir->setText(path);
	}
}

void SpindaProjectView::cancelRun() {
	m_cancelRequested = true;
	appendLog(tr("Cancel requested."));
}

void SpindaProjectView::startPhase3Lane() {
	startPhase3LaneWithOptions(false, false);
}

void SpindaProjectView::startPhase3LaneWithOptions(bool suppressDialogs, bool exitOnComplete) {
	if (m_running) {
		return;
	}
	m_cancelRequested = false;
	setRunning(true);
	QString errorMessage;
	const bool ok = runNativePhase3(&errorMessage);
	setRunning(false);
	if (!ok) {
		m_ui->status->setText(tr("Failed"));
		appendLog(errorMessage);
		if (exitOnComplete) {
			QCoreApplication::exit(1);
			return;
		}
		if (!suppressDialogs) {
			QMessageBox box(QMessageBox::Warning, tr("Spinda project"), errorMessage, QMessageBox::Ok, this);
			applyWindowsDarkChrome(&box);
			box.exec();
		}
		return;
	}
	m_ui->status->setText(tr("Complete"));
	if (exitOnComplete) {
		QCoreApplication::exit(0);
		return;
	}
	if (!suppressDialogs) {
		QMessageBox box(QMessageBox::Information, tr("Spinda project"), tr("Native Phase 3 lane ZIP written."), QMessageBox::Ok, this);
		applyWindowsDarkChrome(&box);
		box.exec();
	}
}

bool SpindaProjectView::runNativePhase3(QString* errorMessage) {
	Phase3Config config;
	config.laneId = m_ui->laneId->value() & 0xFFFF;
	config.phase2StatePath = QDir::cleanPath(m_ui->phase2State->text());
	config.secondhalfCsvPath = QDir::cleanPath(m_ui->secondhalfCsv->text());
	config.outputDir = QDir::cleanPath(m_ui->outputDir->text());
	config.cacheDirPath = QDir::cleanPath(m_ui->cacheDir->text());
	config.pickupInputLeadFrames = m_ui->pickupLead->value();
	config.pickupHoldFrames = m_ui->pickupHold->value();
	config.postPickupFrames = m_ui->postPickup->value();
	config.minPickupDetectFrame = m_ui->minPickupDetect->value();
	config.fastPickupCheckFirstFrame = m_ui->fastPickupFirst->value();
	config.fastPickupCheckSecondFrame = m_ui->fastPickupSecond->value();
	config.maxPickupDetectFrames = m_ui->maxPickupDetect->value();
	config.learnPickupDelaySamples = m_ui->learnPickupDelaySamples->value();
	config.runtimeScheduleMaxSteps = m_ui->maxSteps->value();
	config.limit = m_ui->limit->value();
	config.checkExpectedBaselineRng = m_ui->checkExpectedRng->isChecked();
	config.overwrite = m_ui->overwrite->isChecked();
	config.dynamicPickupDetection = m_ui->dynamicPickupDetection->isChecked();
	config.fastPickupChecks = m_ui->fastPickupChecks->isChecked();
	config.learnPickupDelay = m_ui->learnPickupDelay->isChecked();
	config.enableAudioKillswitch = m_ui->audioKillswitch->isChecked();
	config.enableNoRender = m_ui->noRender->isChecked();
	config.enableFastForward = m_ui->fastForward->isChecked();
	config.headlessAutorun = nativePhase3EnvBool("HEADLESS", false);
	if (!parseUInt(m_ui->expectedRng->text(), 0xFFFFFFFFu, &config.expectedBaselineRng)) {
		if (errorMessage) {
			*errorMessage = tr("Bad expected gRngValue: %1").arg(m_ui->expectedRng->text());
		}
		return false;
	}
	if (config.pickupInputLeadFrames > config.runtimeScheduleMaxSteps) {
		if (errorMessage) {
			*errorMessage = tr("Runtime schedule max steps must cover pickup lead frames.");
		}
		return false;
	}
	if (config.minPickupDetectFrame > config.maxPickupDetectFrames) {
		if (errorMessage) {
			*errorMessage = tr("Minimum pickup detect frame must be less than or equal to max pickup detect frames.");
		}
		return false;
	}
	if (config.fastPickupChecks
		&& (config.fastPickupCheckFirstFrame > config.maxPickupDetectFrames
			|| config.fastPickupCheckSecondFrame > config.maxPickupDetectFrames)) {
		if (errorMessage) {
			*errorMessage = tr("Fast pickup check frames must be less than or equal to max pickup detect frames.");
		}
		return false;
	}
	if (QFileInfo::exists(outputZipPath(config)) && !config.overwrite) {
		if (errorMessage) {
			*errorMessage = tr("Output already exists. Enable overwrite or choose another folder: %1").arg(outputZipPath(config));
		}
		return false;
	}
	if (!m_controller || !m_controller->hasStarted() || !m_controller->thread() || !m_controller->thread()->core) {
		if (errorMessage) {
			*errorMessage = tr("Start a GBA game before running the native Spinda project tool.");
		}
		return false;
	}
	if (m_controller->platform() != mPLATFORM_GBA) {
		if (errorMessage) {
			*errorMessage = tr("Native Spinda project tool requires a GBA core.");
		}
		return false;
	}

	appendLog(tr("Reading target cache or secondhalf.csv."));
	QElapsedTimer totalTimer;
	totalTimer.start();
	QElapsedTimer parseTimer;
	parseTimer.start();
	TargetCacheResult targetResult;
	if (!readPhase3TargetsCached(config, &targetResult, errorMessage)) {
		return false;
	}
	const double csvParseSeconds = parseTimer.elapsed() / 1000.0;
	appendLog(targetResult.cache.hit
		? tr("Target cache hit: %1").arg(targetResult.cache.path)
		: tr("Target cache rebuilt: %1").arg(targetResult.cache.path));

	if (!config.overwrite && QFileInfo::exists(outputZipPath(config))) {
		if (errorMessage) {
			*errorMessage = tr("Output already exists. Enable overwrite or choose another folder: %1").arg(outputZipPath(config));
		}
		return false;
	}

	if (config.enableAudioKillswitch) {
		m_controller->setAudioKillswitch(true);
	}
	if (config.enableNoRender) {
		m_controller->setNoRenderMode(true);
	}
	if (config.enableFastForward) {
		m_controller->setFastForwardRatio(-1.f);
		m_controller->setFastForward(true);
	}

	QJsonObject startStatus;
	startStatus.insert(QStringLiteral("status"), QStringLiteral("running"));
	startStatus.insert(QStringLiteral("generated_records"), 0);
	startStatus.insert(QStringLiteral("selected_targets"), config.limit > 0 ? config.limit : EXPECTED_RECORDS);
	startStatus.insert(QStringLiteral("target_cache"), targetResult.cache.toJson());
	writeStatus(config, startStatus);

	QByteArray block;
	QByteArray bitmap;
	QVector<PickupTarget> selectedTargets;
	CacheInfo scheduleCache;
	uint32_t observedStartRng = 0;
	int written = 0;
	int currentInputDelta = 0;
	qint64 pickupDetectFrameTotal = 0;
	int pickupDetectFrameMin = 0;
	int pickupDetectFrameMax = 0;
	bool havePickupDetectStats = false;
	Phase3Timing timing;
	LearnedPickupDelayState learnedDelay;
	learnedDelay.enabled = config.dynamicPickupDetection && config.learnPickupDelay;
	learnedDelay.sampleLimit = config.learnPickupDelaySamples;
	bool ok = true;
	QString runError;
	QElapsedTimer scheduleTimer;
	scheduleTimer.start();

	m_controller->setScriptTimingOverride(true);
	{
		CoreController::Interrupter interrupter(m_controller.get());
		mCore* core = m_controller->thread()->core;
		auto timedRunFrames = [&timing, core](uint32_t keys, int frames, qint64* bucket) {
			if (frames <= 0) {
				return;
			}
			QElapsedTimer timer;
			timer.start();
			runFrames(core, keys, frames);
			*bucket += timer.nsecsElapsed();
		};
		if (!loadStateFileDirect(core, config.phase2StatePath, &runError)) {
			ok = false;
		}
		if (ok) {
			observedStartRng = core->busRead32(core, GRNG_VALUE_ADDR);
			if (config.checkExpectedBaselineRng && observedStartRng != config.expectedBaselineRng) {
				runError = tr("Phase 3 starting RNG mismatch: expected=%1 observed=%2")
					.arg(formatU32(config.expectedBaselineRng))
					.arg(formatU32(observedStartRng));
				ok = false;
			}
		}

		ScheduleCacheResult scheduleResult;
		if (ok && !buildPhase3ScheduleCached(config, targetResult.targets, targetResult.cache.key, observedStartRng, &scheduleResult, &runError)) {
			ok = false;
		}
		if (ok) {
			scheduleCache = scheduleResult.cache;
			selectedTargets = scheduleResult.targets;
			if (config.limit > 0 && config.limit < selectedTargets.size()) {
				selectedTargets.resize(config.limit);
			}
			m_ui->progress->setRange(0, selectedTargets.size());
			appendLog(scheduleCache.hit
				? tr("Schedule cache hit: %1").arg(scheduleCache.path)
				: tr("Schedule cache rebuilt: %1").arg(scheduleCache.path));
			core->setKeys(core, 0);
			// Delay full lane buffers until after state and schedule validation.
			// Early setup failures then avoid reserving the full PK3 block.
			block.resize(EXPECTED_RECORDS * BOX_SLOT_SIZE);
			block.fill(char(0));
			bitmap.resize(BITMAP_BYTES);
			bitmap.fill(char(0));
		}

		QByteArray scratchState;
		QByteArray record;
		scratchState.reserve(512 * 1024);
		record.resize(BOX_SLOT_SIZE);
		for (const PickupTarget& target : selectedTargets) {
			if (!ok || m_cancelRequested) {
				if (m_cancelRequested) {
					runError = tr("Run cancelled.");
					ok = false;
				}
				break;
			}

			const int delta = target.inputDeltaFromStart - currentInputDelta;
			if (delta < 0) {
				runError = tr("Phase 3 schedule moved backward at %1").arg(formatU16(target.upperHalf));
				ok = false;
				break;
			}
			if (delta) {
				timedRunFrames(0, delta, &timing.frameAdvanceNs);
				currentInputDelta = target.inputDeltaFromStart;
			}

			{
				QElapsedTimer timer;
				timer.start();
				if (!saveStateToBuffer(core, &scratchState)) {
					timing.scratchSaveNs += timer.nsecsElapsed();
					runError = tr("Could not save in-RAM scratch state at %1").arg(formatU16(target.upperHalf));
					ok = false;
					break;
				}
				timing.scratchSaveNs += timer.nsecsElapsed();
			}
			timedRunFrames(A_BUTTON_MASK, config.pickupHoldFrames, &timing.pickupWaitDetectNs);

			const uint32_t expectedPid = (uint32_t(target.upperHalf) << 16) | config.laneId;
			int pickupWaitFrames = config.postPickupFrames;
			int partyCount = 0;
			uint32_t pid = 0;
			bool foundPickup = false;
			int elapsedPickupFrames = 0;
			int lastCheckedPickupFrame = -1;
			auto waitToPickupFrame = [&](int targetFrame) {
				if (targetFrame <= elapsedPickupFrames) {
					return;
				}
				timedRunFrames(0, targetFrame - elapsedPickupFrames, &timing.pickupWaitDetectNs);
				elapsedPickupFrames = targetFrame;
			};
			auto checkPickupNow = [&](int waitedFrame) {
				lastCheckedPickupFrame = waitedFrame;
				partyCount = core->busRead8(core, GPLAYER_PARTY_COUNT_ADDR) & 0xFF;
				if (partyCount < 2) {
					return false;
				}
				pid = readPartyPid(core, 2);
				if (pid != expectedPid) {
					return false;
				}
				QElapsedTimer timer;
				timer.start();
				readBoxedPartyRecord(core, 2, &record);
				timing.pk3ReadNs += timer.nsecsElapsed();
				pid = readPid(record);
				if (pid != expectedPid) {
					return false;
				}
				pickupWaitFrames = waitedFrame;
				foundPickup = true;
				return true;
			};
			if (config.dynamicPickupDetection) {
				if (learnedDelay.active && learnedDelay.learnedDelay >= 0) {
					waitToPickupFrame(learnedDelay.learnedDelay);
					++learnedDelay.fixedChecks;
					checkPickupNow(learnedDelay.learnedDelay);
					if (!foundPickup) {
						++learnedDelay.fallbackScans;
						learnedDelay.active = false;
						learnedDelay.disabledAfterMismatch = true;
					}
				}
				if (config.fastPickupChecks && !foundPickup) {
					QVector<int> fastFrames;
					fastFrames.append(config.fastPickupCheckFirstFrame);
					fastFrames.append(config.fastPickupCheckSecondFrame);
					std::sort(fastFrames.begin(), fastFrames.end());
					fastFrames.erase(std::unique(fastFrames.begin(), fastFrames.end()), fastFrames.end());
					for (int frame : fastFrames) {
						if (frame < config.minPickupDetectFrame || frame > config.maxPickupDetectFrames) {
							continue;
						}
						waitToPickupFrame(frame);
						if (checkPickupNow(frame)) {
							break;
						}
					}
				}
				const int fallbackStartFrame = qMax(config.minPickupDetectFrame, lastCheckedPickupFrame + 1);
				if (!foundPickup && elapsedPickupFrames < fallbackStartFrame) {
					waitToPickupFrame(fallbackStartFrame);
				}
				for (int waited = fallbackStartFrame; !foundPickup && waited <= config.maxPickupDetectFrames; ++waited) {
					waitToPickupFrame(waited);
					if (checkPickupNow(waited)) {
						break;
					}
				}
			} else {
				waitToPickupFrame(config.postPickupFrames);
				checkPickupNow(config.postPickupFrames);
			}
			if (!foundPickup) {
				runError = tr("Extracted PID mismatch: target_upper=%1 expected_pid=%2 observed_pid=%3 csv_t0_frame=%4 input_delta=%5")
					.arg(formatU16(target.upperHalf))
					.arg(formatU32(expectedPid))
					.arg(formatU32(pid))
					.arg(target.csvFrameFromInitialSeed)
					.arg(target.inputDeltaFromStart);
				if (partyCount < 2) {
					runError += tr(" party_count=%1").arg(partyCount);
				}
				if (config.dynamicPickupDetection) {
					runError += tr(" max_pickup_detect_frames=%1").arg(config.maxPickupDetectFrames);
				}
				ok = false;
				break;
			}
			if (config.dynamicPickupDetection) {
				pickupDetectFrameTotal += pickupWaitFrames;
				pickupDetectFrameMin = havePickupDetectStats ? qMin(pickupDetectFrameMin, pickupWaitFrames) : pickupWaitFrames;
				pickupDetectFrameMax = havePickupDetectStats ? qMax(pickupDetectFrameMax, pickupWaitFrames) : pickupWaitFrames;
				havePickupDetectStats = true;
				if (learnedDelay.enabled && !learnedDelay.active && !learnedDelay.disabledAfterMismatch && learnedDelay.sampleCount < learnedDelay.sampleLimit) {
					learnedDelay.sampleMin = learnedDelay.sampleCount ? qMin(learnedDelay.sampleMin, pickupWaitFrames) : pickupWaitFrames;
					learnedDelay.sampleMax = learnedDelay.sampleCount ? qMax(learnedDelay.sampleMax, pickupWaitFrames) : pickupWaitFrames;
					++learnedDelay.sampleCount;
					if (learnedDelay.sampleCount >= learnedDelay.sampleLimit && learnedDelay.sampleMin == learnedDelay.sampleMax) {
						learnedDelay.learnedDelay = learnedDelay.sampleMin;
						learnedDelay.active = true;
						appendLog(tr("Learned stable pickup delay: %1 frames after %2 samples.")
							.arg(learnedDelay.learnedDelay)
							.arg(learnedDelay.sampleCount));
					}
				}
			}

			const int byteIndex = target.upperHalf >> 3;
			const int bitIndex = target.upperHalf & 7;
			if (bitmap.at(byteIndex) & (1 << bitIndex)) {
				runError = tr("Duplicate upper-half record: %1").arg(formatU16(target.upperHalf));
				ok = false;
				break;
			}
			const int offset = int(target.upperHalf) * BOX_SLOT_SIZE;
			memcpy(block.data() + offset, record.constData(), BOX_SLOT_SIZE);
			bitmap[byteIndex] = char(uchar(bitmap.at(byteIndex)) | (1 << bitIndex));
			++written;

			{
				QElapsedTimer timer;
				timer.start();
				if (!loadStateFromBuffer(core, scratchState)) {
					timing.scratchRestoreNs += timer.nsecsElapsed();
					runError = tr("Could not restore in-RAM scratch state at %1").arg(formatU16(target.upperHalf));
					ok = false;
					break;
				}
				timing.scratchRestoreNs += timer.nsecsElapsed();
			}
			core->setKeys(core, 0);

			if ((written & 0x3FF) == 0 || written == selectedTargets.size()) {
				m_ui->progress->setValue(written);
				m_ui->status->setText(tr("%1 / %2 in RAM").arg(written).arg(selectedTargets.size()));
				if (!config.headlessAutorun) {
					QCoreApplication::processEvents(QEventLoop::AllEvents, 20);
				}
			}
		}
		core->setKeys(core, 0);
	}
	m_controller->setScriptTimingOverride(false);

	if (!ok) {
		QJsonObject error;
		error.insert(QStringLiteral("time_unix"), QDateTime::currentSecsSinceEpoch());
		error.insert(QStringLiteral("error"), runError);
		error.insert(QStringLiteral("generated_records"), written);
		error.insert(QStringLiteral("current_input_delta_from_start"), currentInputDelta);
		appendJsonLine(errorPath(config), error);

		QJsonObject failed;
		failed.insert(QStringLiteral("status"), QStringLiteral("failed"));
		failed.insert(QStringLiteral("generated_records"), written);
		failed.insert(QStringLiteral("selected_targets"), selectedTargets.size());
		failed.insert(QStringLiteral("error"), runError);
		failed.insert(QStringLiteral("target_cache"), targetResult.cache.toJson());
		failed.insert(QStringLiteral("schedule_cache"), scheduleCache.toJson());
		failed.insert(QStringLiteral("learned_pickup_delay"), learnedPickupDelayToJson(learnedDelay));
		failed.insert(QStringLiteral("timing"), timingToJson(timing));
		failed.insert(QStringLiteral("elapsed_seconds"), totalTimer.elapsed() / 1000.0);
		writeStatus(config, failed);
		if (errorMessage) {
			*errorMessage = runError;
		}
		return false;
	}

	const double scheduleSeconds = scheduleTimer.elapsed() / 1000.0;
	QElapsedTimer hashTimer;
	hashTimer.start();
	const QString pk3RecordsSha1 = presentPk3RecordsSha1(block, bitmap);
	const QString presenceBitmapSha1 = sha1Hex(bitmap);
	timing.hashNs += hashTimer.nsecsElapsed();

	appendLog(tr("Writing ZIP: %1").arg(outputZipPath(config)));
	QElapsedTimer zipTimer;
	zipTimer.start();
	if (!writePhase3Zip(config, block, bitmap, written, errorMessage)) {
		return false;
	}
	timing.zipBuildWriteNs += zipTimer.nsecsElapsed();
	const double elapsedSeconds = totalTimer.elapsed() / 1000.0;

	QJsonObject result;
	result.insert(QStringLiteral("lane_id"), laneHex(config));
	result.insert(QStringLiteral("generated_records"), written);
	result.insert(QStringLiteral("output_zip_path"), outputZipPath(config));
	result.insert(QStringLiteral("archive_format"), QStringLiteral("explicit-pid-pk3"));
	result.insert(QStringLiteral("pk3_entry_count"), written);
	result.insert(QStringLiteral("pk3_record_size"), BOX_SLOT_SIZE);
	result.insert(QStringLiteral("pk3_records_sha1"), pk3RecordsSha1);
	result.insert(QStringLiteral("presence_bitmap_sha1"), presenceBitmapSha1);
	result.insert(QStringLiteral("zip_sha1"), sha1File(outputZipPath(config)));
	result.insert(QStringLiteral("timing"), timingToJson(timing));
	result.insert(QStringLiteral("elapsed_seconds"), elapsedSeconds);

	QJsonObject complete;
	complete.insert(QStringLiteral("status"), QStringLiteral("complete"));
	complete.insert(QStringLiteral("generated_records"), written);
	complete.insert(QStringLiteral("selected_targets"), selectedTargets.size());
	complete.insert(QStringLiteral("archive_format"), QStringLiteral("explicit-pid-pk3"));
	complete.insert(QStringLiteral("result"), result);
	complete.insert(QStringLiteral("target_cache"), targetResult.cache.toJson());
	complete.insert(QStringLiteral("schedule_cache"), scheduleCache.toJson());
	complete.insert(QStringLiteral("learned_pickup_delay"), learnedPickupDelayToJson(learnedDelay));
	complete.insert(QStringLiteral("timing"), timingToJson(timing));
	if (config.dynamicPickupDetection && havePickupDetectStats && written > 0) {
		complete.insert(QStringLiteral("pickup_detect_min_frames"), pickupDetectFrameMin);
		complete.insert(QStringLiteral("pickup_detect_max_frames"), pickupDetectFrameMax);
		complete.insert(QStringLiteral("pickup_detect_avg_frames"), double(pickupDetectFrameTotal) / double(written));
	}
	complete.insert(QStringLiteral("csv_parse_seconds"), csvParseSeconds);
	complete.insert(QStringLiteral("schedule_seconds"), scheduleSeconds);
	complete.insert(QStringLiteral("elapsed_seconds"), elapsedSeconds);
	writeStatus(config, complete);

	m_ui->progress->setValue(written);
	appendLog(tr("Complete. ZIP SHA1: %1").arg(result.value(QStringLiteral("zip_sha1")).toString()));
	return true;
}
