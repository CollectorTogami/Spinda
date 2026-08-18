/* SPDX-License-Identifier: MPL-2.0
 * COLLECTOR_TOGAMI_SOURCE_CREDIT_2026-05-11
 * Project credit: Collector Togami is the person behind the Spinda/SPC3 project
 * as a whole and is credited as project originator, coordinator, and driving force.
 * Shawrkie helped with SPC3 compressor/decompressor work and contributed compute
 * for corpus processing and verification.
 */
/* Copyright (c) 2013-2026 Jeffrey Pfau
 *
 * Original mGBA source is credited to Jeffrey Pfau and contributors.
 * Local custom modifications in this fork were added for this workspace and
 * are not upstream mGBA work or authored by Jeffrey Pfau.
 *
 * This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at http://mozilla.org/MPL/2.0/. */
#include <mgba/core/config.h>
#include <mgba/core/core.h>
#include <mgba/core/log.h>
#include <mgba/core/serialize.h>
#include <mgba-util/vfs.h>

#include <zlib.h>

#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <direct.h>
#include <windows.h>
#else
#include <sys/stat.h>
#include <unistd.h>
#endif

#include <algorithm>
#include <cerrno>
#include <chrono>
#include <cinttypes>
#include <cstdarg>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fcntl.h>
#include <fstream>
#include <map>
#include <set>
#include <sstream>
#include <string>
#include <sys/stat.h>
#include <vector>

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
constexpr uint16_t ZIP_METHOD_STORE = 0;
constexpr uint16_t ZIP_DOS_TIME_MIDNIGHT = 0;
constexpr uint16_t ZIP_DOS_DATE_2026_01_01 = (46 << 9) | (1 << 5) | 1;
constexpr int ZIP_DEFLATE_LEVEL = 1;
constexpr uint32_t TARGET_CACHE_MAGIC = 0x33505443; // "CTP3"
constexpr uint32_t SCHEDULE_CACHE_MAGIC = 0x33505343; // "CSP3"
constexpr uint32_t PHASE3_CLI_CACHE_VERSION = 1;

struct Config {
	std::string romPath;
	std::string phase2StatePath;
	std::string phase2StateDirPath;
	std::string secondhalfCsvPath;
	std::string outputDir;
	std::string cacheDirPath;
	uint16_t laneId = 1;
	std::vector<uint16_t> laneIds;
	uint16_t expectedInitialSeed = 0xCD39;
	uint32_t expectedBaselineRng = 0x2B0C94C1;
	bool checkExpectedInitialSeed = true;
	bool checkExpectedBaselineRng = true;
	bool overwrite = false;
	int limit = 0;
	int pickupInputLeadFrames = 3;
	int pickupHoldFrames = 1;
	int minPickupDetectFrame = 4;
	int fastPickupCheckFirstFrame = 4;
	int fastPickupCheckSecondFrame = 5;
	bool fastPickupChecks = true;
	int maxPickupDetectFrames = 12;
	bool learnPickupDelay = true;
	int learnPickupDelaySamples = 32;
	int runtimeScheduleMaxSteps = 4000000;
	int neutralWaitProofFrames = 0;
	bool zipStoreOnly = false;
	bool quietCoreLogs = true;
	bool showHelp = false;
};

struct TimingBuckets {
	uint64_t csvTargetCacheNs = 0;
	uint64_t scheduleCacheNs = 0;
	uint64_t stateLoadNs = 0;
	uint64_t frameAdvanceNs = 0;
	uint64_t scratchSaveNs = 0;
	uint64_t pickupWaitDetectNs = 0;
	uint64_t scratchRestoreNs = 0;
	uint64_t pk3ReadNs = 0;
	uint64_t zipBuildWriteNs = 0;
};

struct CacheStats {
	bool targetCacheHit = false;
	bool scheduleCacheHit = false;
	std::string targetCachePath;
	std::string scheduleCachePath;
};

struct LearnedPickupDelayState {
	bool enabled = true;
	bool active = false;
	bool disabledAfterMismatch = false;
	int sampleLimit = 32;
	int sampleCount = 0;
	int sampleMin = 0;
	int sampleMax = 0;
	int learnedDelay = -1;
	int fixedChecks = 0;
	int fallbackScans = 0;
};

struct SecondHalfTarget {
	uint16_t upperHalf = 0;
	int sweepIndex = 0;
	int frameFromInitialSeed = 0;
	uint32_t rngSeed = 0;
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
	std::string name;
	uint16_t method = ZIP_METHOD_DEFLATE;
	uint32_t crc = 0;
	uint32_t compressedSize = 0;
	uint32_t uncompressedSize = 0;
	uint32_t localHeaderOffset = 0;
};

std::string formatU16(uint32_t value) {
	char buffer[16];
	std::snprintf(buffer, sizeof(buffer), "0x%04X", value & 0xFFFF);
	return buffer;
}

std::string formatU32(uint32_t value) {
	char buffer[16];
	std::snprintf(buffer, sizeof(buffer), "0x%08X", value);
	return buffer;
}

std::string laneHex(const Config& config) {
	return formatU16(config.laneId);
}

std::string outputZipPath(const Config& config) {
	return config.outputDir + "/" + laneHex(config) + ".spinda80.zip";
}

std::string statusPath(const Config& config) {
	return config.outputDir + "/_" + laneHex(config) + ".phase3_status.json";
}

std::string phase2StatePathForLane(const Config& config, uint16_t laneId) {
	if (config.phase2StateDirPath.empty()) {
		return config.phase2StatePath;
	}
	return config.phase2StateDirPath + "/" + formatU16(laneId) + ".ss0";
}

std::string cacheDirPath(const Config& config) {
	return config.cacheDirPath.empty() ? config.outputDir + "/_cache" : config.cacheDirPath;
}

uint64_t nowNs() {
	using Clock = std::chrono::steady_clock;
	return static_cast<uint64_t>(std::chrono::duration_cast<std::chrono::nanoseconds>(Clock::now().time_since_epoch()).count());
}

double secondsFromNs(uint64_t ns) {
	return static_cast<double>(ns) / 1000000000.0;
}

std::string jsonEscape(const std::string& raw) {
	std::string out;
	out.reserve(raw.size() + 8);
	for (char ch : raw) {
		switch (ch) {
		case '\\':
			out += "\\\\";
			break;
		case '"':
			out += "\\\"";
			break;
		case '\n':
			out += "\\n";
			break;
		case '\r':
			out += "\\r";
			break;
		case '\t':
			out += "\\t";
			break;
		default:
			out += ch;
			break;
		}
	}
	return out;
}

bool parseUInt(const char* raw, uint32_t maxValue, uint32_t* out) {
	if (!raw || !*raw) {
		return false;
	}
	// `strtoul` accepts signs; reject them so CLI/CSV numeric fields cannot
	// silently wrap `-1` into an unsigned seed or lane id.
	if (raw[0] == '-' || raw[0] == '+') {
		return false;
	}
	errno = 0;
	char* end = nullptr;
	unsigned long value = std::strtoul(raw, &end, 0);
	if (errno || !end || *end || value > maxValue) {
		return false;
	}
	if (out) {
		*out = static_cast<uint32_t>(value);
	}
	return true;
}

bool parseLaneToken(const std::string& token, std::vector<uint16_t>* lanes) {
	if (token.empty()) {
		return false;
	}
	size_t rangePos = token.find("..");
	size_t rangeLen = 2;
	if (rangePos == std::string::npos) {
		rangePos = token.find('-');
		rangeLen = 1;
	}
	uint32_t first = 0;
	uint32_t last = 0;
	if (rangePos == std::string::npos) {
		if (!parseUInt(token.c_str(), 0xFFFF, &first)) {
			return false;
		}
		lanes->push_back(static_cast<uint16_t>(first));
		return true;
	}
	const std::string rawFirst = token.substr(0, rangePos);
	const std::string rawLast = token.substr(rangePos + rangeLen);
	if (!parseUInt(rawFirst.c_str(), 0xFFFF, &first) || !parseUInt(rawLast.c_str(), 0xFFFF, &last) || first > last) {
		return false;
	}
	for (uint32_t lane = first; lane <= last; ++lane) {
		lanes->push_back(static_cast<uint16_t>(lane));
	}
	return true;
}

bool parseLaneList(const char* raw, std::vector<uint16_t>* lanes) {
	if (!raw || !*raw) {
		return false;
	}
	std::string text(raw);
	size_t start = 0;
	while (start <= text.size()) {
		const size_t end = text.find(',', start);
		const std::string token = text.substr(start, end == std::string::npos ? std::string::npos : end - start);
		if (!parseLaneToken(token, lanes)) {
			return false;
		}
		if (end == std::string::npos) {
			break;
		}
		start = end + 1;
	}
	return true;
}

uint32_t lcrngNext(uint32_t state) {
	return state * GBA_LCRNG_MULTIPLIER + GBA_LCRNG_INCREMENT;
}

void appendJsonTiming(std::ostringstream& json, const TimingBuckets& timing) {
	json << ",\n  \"timing\": {\n";
	json << "    \"csv_target_cache_seconds\": " << secondsFromNs(timing.csvTargetCacheNs) << ",\n";
	json << "    \"schedule_cache_seconds\": " << secondsFromNs(timing.scheduleCacheNs) << ",\n";
	json << "    \"state_load_seconds\": " << secondsFromNs(timing.stateLoadNs) << ",\n";
	json << "    \"frame_advance_seconds\": " << secondsFromNs(timing.frameAdvanceNs) << ",\n";
	json << "    \"scratch_save_seconds\": " << secondsFromNs(timing.scratchSaveNs) << ",\n";
	json << "    \"pickup_wait_detect_seconds\": " << secondsFromNs(timing.pickupWaitDetectNs) << ",\n";
	json << "    \"scratch_restore_seconds\": " << secondsFromNs(timing.scratchRestoreNs) << ",\n";
	json << "    \"pk3_read_seconds\": " << secondsFromNs(timing.pk3ReadNs) << ",\n";
	json << "    \"zip_build_write_seconds\": " << secondsFromNs(timing.zipBuildWriteNs) << "\n";
	json << "  }";
}

void appendJsonCacheStats(std::ostringstream& json, const CacheStats& stats) {
	json << ",\n  \"cache\": {\n";
	json << "    \"target_cache_hit\": " << (stats.targetCacheHit ? "true" : "false") << ",\n";
	json << "    \"target_cache_path\": \"" << jsonEscape(stats.targetCachePath) << "\",\n";
	json << "    \"schedule_cache_hit\": " << (stats.scheduleCacheHit ? "true" : "false") << ",\n";
	json << "    \"schedule_cache_path\": \"" << jsonEscape(stats.scheduleCachePath) << "\"\n";
	json << "  }";
}

void appendJsonLearnedDelay(std::ostringstream& json, const LearnedPickupDelayState& learned) {
	json << ",\n  \"learned_pickup_delay\": {\n";
	json << "    \"enabled\": " << (learned.enabled ? "true" : "false") << ",\n";
	json << "    \"active\": " << (learned.active ? "true" : "false") << ",\n";
	json << "    \"disabled_after_mismatch\": " << (learned.disabledAfterMismatch ? "true" : "false") << ",\n";
	json << "    \"sample_limit\": " << learned.sampleLimit << ",\n";
	json << "    \"sample_count\": " << learned.sampleCount << ",\n";
	json << "    \"sample_min\": " << learned.sampleMin << ",\n";
	json << "    \"sample_max\": " << learned.sampleMax << ",\n";
	json << "    \"learned_delay_frames\": " << learned.learnedDelay << ",\n";
	json << "    \"fixed_checks\": " << learned.fixedChecks << ",\n";
	json << "    \"fallback_scans\": " << learned.fallbackScans << "\n";
	json << "  }";
}

void quietCoreLog(mLogger* logger, int category, enum mLogLevel level, const char* format, va_list args) {
	(void) logger;
	(void) category;
	(void) level;
	(void) format;
	(void) args;
}

std::vector<std::string> splitCsvLine(const std::string& line) {
	std::vector<std::string> cells;
	std::string cell;
	std::istringstream stream(line);
	while (std::getline(stream, cell, ',')) {
		while (!cell.empty() && (cell.back() == '\r' || cell.back() == '\n' || cell.back() == ' ' || cell.back() == '\t')) {
			cell.pop_back();
		}
		size_t start = 0;
		while (start < cell.size() && (cell[start] == ' ' || cell[start] == '\t')) {
			++start;
		}
		cells.push_back(cell.substr(start));
	}
	return cells;
}

bool pathIsDirectory(const std::string& path) {
#ifdef _WIN32
	const DWORD attrs = GetFileAttributesA(path.c_str());
	return attrs != INVALID_FILE_ATTRIBUTES && (attrs & FILE_ATTRIBUTE_DIRECTORY);
#else
	struct stat st;
	return stat(path.c_str(), &st) == 0 && S_ISDIR(st.st_mode);
#endif
}

bool pathExists(const std::string& path) {
#ifdef _WIN32
	return GetFileAttributesA(path.c_str()) != INVALID_FILE_ATTRIBUTES;
#else
	struct stat st;
	return stat(path.c_str(), &st) == 0;
#endif
}

uint32_t currentProcessId() {
#ifdef _WIN32
	return static_cast<uint32_t>(GetCurrentProcessId());
#else
	return static_cast<uint32_t>(getpid());
#endif
}

std::string zipTempPathFor(const std::string& finalPath) {
	std::ostringstream stream;
	stream << finalPath << ".pid" << currentProcessId() << ".tmp";
	return stream.str();
}

std::string cacheTempPathFor(const std::string& finalPath) {
	std::ostringstream stream;
	stream << finalPath << ".pid" << currentProcessId() << ".tmp";
	return stream.str();
}

bool replaceCacheFile(const std::string& tmpPath, const std::string& finalPath) {
#ifdef _WIN32
	return MoveFileExA(tmpPath.c_str(), finalPath.c_str(), MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH);
#else
	return std::rename(tmpPath.c_str(), finalPath.c_str()) == 0;
#endif
}

bool makeDirs(const std::string& path) {
	if (path.empty()) {
		return false;
	}
	std::string normalized = path;
	std::replace(normalized.begin(), normalized.end(), '\\', '/');
	for (size_t pos = 0; pos <= normalized.size(); ++pos) {
		if (pos != normalized.size() && normalized[pos] != '/') {
			continue;
		}
		if (pos == 0 || (pos == 2 && normalized[1] == ':')) {
			continue;
		}
		const std::string part = normalized.substr(0, pos);
		if (part.empty()) {
			continue;
		}
#ifdef _WIN32
		if (!CreateDirectoryA(part.c_str(), nullptr)) {
			const DWORD error = GetLastError();
			if (error != ERROR_ALREADY_EXISTS || !pathIsDirectory(part)) {
				return false;
			}
		}
#else
		if (mkdir(part.c_str(), 0777)) {
			if (errno != EEXIST || !pathIsDirectory(part)) {
				return false;
			}
		}
#endif
	}
	return true;
}

bool writeTextFile(const std::string& path, const std::string& text) {
	std::ofstream file(path.c_str(), std::ios::out | std::ios::trunc | std::ios::binary);
	if (!file) {
		return false;
	}
	file << text;
	return bool(file);
}

bool fileStamp(const std::string& path, uint64_t* size, uint64_t* mtime) {
#ifdef _WIN32
	WIN32_FILE_ATTRIBUTE_DATA data;
	if (!GetFileAttributesExA(path.c_str(), GetFileExInfoStandard, &data)) {
		return false;
	}
	ULARGE_INTEGER fileSize;
	fileSize.HighPart = data.nFileSizeHigh;
	fileSize.LowPart = data.nFileSizeLow;
	ULARGE_INTEGER fileTime;
	fileTime.HighPart = data.ftLastWriteTime.dwHighDateTime;
	fileTime.LowPart = data.ftLastWriteTime.dwLowDateTime;
	if (size) {
		*size = fileSize.QuadPart;
	}
	if (mtime) {
		*mtime = fileTime.QuadPart;
	}
	return true;
#else
	struct stat st;
	if (stat(path.c_str(), &st) != 0) {
		return false;
	}
	if (size) {
		*size = static_cast<uint64_t>(st.st_size);
	}
	if (mtime) {
		*mtime = static_cast<uint64_t>(st.st_mtime);
	}
	return true;
#endif
}

std::string targetCachePath(const Config& config) {
	uint64_t size = 0;
	uint64_t mtime = 0;
	fileStamp(config.secondhalfCsvPath, &size, &mtime);
	std::ostringstream stream;
	stream << cacheDirPath(config) << "/cli-secondhalf-t0-v" << PHASE3_CLI_CACHE_VERSION
		<< "-seed-" << formatU16(config.expectedInitialSeed)
		<< "-lead-" << config.pickupInputLeadFrames
		<< "-records-" << EXPECTED_RECORDS
		<< "-size-" << size
		<< "-mtime-" << mtime
		<< ".bin";
	return stream.str();
}

std::string scheduleCachePath(const Config& config, uint32_t observedStartRng, int targetLimit) {
	const int requestedTargets = targetLimit > 0 && targetLimit < EXPECTED_RECORDS ? targetLimit : EXPECTED_RECORDS;
	uint64_t size = 0;
	uint64_t mtime = 0;
	fileStamp(config.secondhalfCsvPath, &size, &mtime);
	std::ostringstream stream;
	stream << cacheDirPath(config) << "/cli-runtime-schedule-v" << PHASE3_CLI_CACHE_VERSION
		<< "-seed-" << formatU16(config.expectedInitialSeed)
		<< "-start-" << formatU32(observedStartRng)
		<< "-lead-" << config.pickupInputLeadFrames
		<< "-max-" << config.runtimeScheduleMaxSteps
		<< "-count-" << requestedTargets
		<< "-csvsize-" << size
		<< "-csvmtime-" << mtime
		<< ".bin";
	return stream.str();
}

void writeStatus(
	const Config& config,
	const std::string& status,
	int generated,
	int selected,
	const std::string& error = std::string(),
	uint32_t observedRng = 0,
	const TimingBuckets* timing = nullptr,
	const CacheStats* cacheStats = nullptr,
	const LearnedPickupDelayState* learnedDelay = nullptr,
	int pickupDetectMin = 0,
	int pickupDetectMax = 0,
	double pickupDetectAvg = 0.0) {
	makeDirs(config.outputDir);
	std::ostringstream json;
	json << "{\n";
	json << "  \"schema_version\": 1,\n";
	json << "  \"headless_cli_feature\": true,\n";
	json << "  \"status\": \"" << jsonEscape(status) << "\",\n";
	json << "  \"lane_id\": \"" << laneHex(config) << "\",\n";
	json << "  \"phase2_state_path\": \"" << jsonEscape(config.phase2StatePath) << "\",\n";
	json << "  \"secondhalf_csv\": \"" << jsonEscape(config.secondhalfCsvPath) << "\",\n";
	json << "  \"output_zip_path\": \"" << jsonEscape(outputZipPath(config)) << "\",\n";
	json << "  \"archive_format\": \"explicit-pid-pk3\",\n";
	json << "  \"record_size\": " << BOX_SLOT_SIZE << ",\n";
	json << "  \"expected_initial_seed\": \"" << formatU16(config.expectedInitialSeed) << "\",\n";
	json << "  \"check_expected_initial_seed\": " << (config.checkExpectedInitialSeed ? "true" : "false") << ",\n";
	json << "  \"cache_dir\": \"" << jsonEscape(cacheDirPath(config)) << "\",\n";
	json << "  \"generated_records\": " << generated << ",\n";
	json << "  \"selected_targets\": " << selected << ",\n";
	json << "  \"expected_records\": " << EXPECTED_RECORDS << ",\n";
	json << "  \"observed_start_rng\": \"" << formatU32(observedRng) << "\",\n";
	json << "  \"zip_method\": \"" << (config.zipStoreOnly ? "store" : "deflate") << "\",\n";
	json << "  \"status_hashes\": false,\n";
	json << "  \"hashing_policy\": \"hot CLI run skips full PK3 and ZIP hashes; validate archives after run\",\n";
	json << "  \"min_pickup_detect_frame\": " << config.minPickupDetectFrame << ",\n";
	json << "  \"fast_pickup_checks\": " << (config.fastPickupChecks ? "true" : "false") << ",\n";
	json << "  \"fast_pickup_check_first_frame\": " << config.fastPickupCheckFirstFrame << ",\n";
	json << "  \"fast_pickup_check_second_frame\": " << config.fastPickupCheckSecondFrame << ",\n";
	json << "  \"max_pickup_detect_frames\": " << config.maxPickupDetectFrames << ",\n";
	json << "  \"learn_pickup_delay\": " << (config.learnPickupDelay ? "true" : "false") << ",\n";
	json << "  \"learn_pickup_delay_samples\": " << config.learnPickupDelaySamples << ",\n";
	json << "  \"pickup_detect_min_frames\": " << pickupDetectMin << ",\n";
	json << "  \"pickup_detect_max_frames\": " << pickupDetectMax << ",\n";
	json << "  \"pickup_detect_avg_frames\": " << pickupDetectAvg;
	if (timing) {
		appendJsonTiming(json, *timing);
	}
	if (cacheStats) {
		appendJsonCacheStats(json, *cacheStats);
	}
	if (learnedDelay) {
		appendJsonLearnedDelay(json, *learnedDelay);
	}
	if (!error.empty()) {
		json << ",\n  \"error\": \"" << jsonEscape(error) << "\"";
	}
	json << "\n}\n";
	writeTextFile(statusPath(config), json.str());
}

void writeLe16(std::ostream& out, uint16_t value) {
	char bytes[2] = {
		static_cast<char>(value & 0xFF),
		static_cast<char>((value >> 8) & 0xFF),
	};
	out.write(bytes, sizeof(bytes));
}

void writeLe32(std::ostream& out, uint32_t value) {
	writeLe16(out, static_cast<uint16_t>(value & 0xFFFF));
	writeLe16(out, static_cast<uint16_t>((value >> 16) & 0xFFFF));
}

void writeLe64(std::ostream& out, uint64_t value) {
	writeLe32(out, static_cast<uint32_t>(value & 0xFFFFFFFFu));
	writeLe32(out, static_cast<uint32_t>((value >> 32) & 0xFFFFFFFFu));
}

bool readLe16(std::istream& in, uint16_t* out) {
	unsigned char bytes[2] = {};
	if (!in.read(reinterpret_cast<char*>(bytes), sizeof(bytes))) {
		return false;
	}
	*out = uint16_t(bytes[0]) | (uint16_t(bytes[1]) << 8);
	return true;
}

bool readLe32(std::istream& in, uint32_t* out) {
	uint16_t lo = 0;
	uint16_t hi = 0;
	if (!readLe16(in, &lo) || !readLe16(in, &hi)) {
		return false;
	}
	*out = uint32_t(lo) | (uint32_t(hi) << 16);
	return true;
}

bool readLe64(std::istream& in, uint64_t* out) {
	uint32_t lo = 0;
	uint32_t hi = 0;
	if (!readLe32(in, &lo) || !readLe32(in, &hi)) {
		return false;
	}
	*out = uint64_t(lo) | (uint64_t(hi) << 32);
	return true;
}

bool writeTargetCache(const Config& config, const std::string& path, const std::vector<SecondHalfTarget>& targets) {
	if (!makeDirs(cacheDirPath(config))) {
		return false;
	}
	uint64_t csvSize = 0;
	uint64_t csvMtime = 0;
	if (!fileStamp(config.secondhalfCsvPath, &csvSize, &csvMtime)) {
		return false;
	}
	// Cache files are shared by parallel workers. Use process-local temp names
	// so first-run workers can race safely; a failed cache publish should leave
	// any older usable cache in place.
	const std::string tmpPath = cacheTempPathFor(path);
	std::ofstream out(tmpPath.c_str(), std::ios::binary | std::ios::trunc);
	if (!out) {
		return false;
	}
	writeLe32(out, TARGET_CACHE_MAGIC);
	writeLe32(out, PHASE3_CLI_CACHE_VERSION);
	writeLe64(out, csvSize);
	writeLe64(out, csvMtime);
	writeLe32(out, config.pickupInputLeadFrames);
	writeLe32(out, EXPECTED_RECORDS);
	writeLe32(out, config.expectedInitialSeed);
	writeLe32(out, static_cast<uint32_t>(targets.size()));
	for (const SecondHalfTarget& target : targets) {
		writeLe16(out, target.upperHalf);
		writeLe32(out, static_cast<uint32_t>(target.sweepIndex));
		writeLe32(out, static_cast<uint32_t>(target.frameFromInitialSeed));
		writeLe32(out, target.rngSeed);
	}
	out.close();
	if (!out) {
		std::remove(tmpPath.c_str());
		return false;
	}
	if (!replaceCacheFile(tmpPath, path)) {
		std::remove(tmpPath.c_str());
		return false;
	}
	return true;
}

bool readTargetCache(const Config& config, const std::string& path, std::vector<SecondHalfTarget>* targets) {
	uint64_t csvSize = 0;
	uint64_t csvMtime = 0;
	if (!fileStamp(config.secondhalfCsvPath, &csvSize, &csvMtime)) {
		return false;
	}
	std::ifstream in(path.c_str(), std::ios::binary);
	if (!in) {
		return false;
	}
	uint32_t magic = 0;
	uint32_t version = 0;
	uint64_t cachedSize = 0;
	uint64_t cachedMtime = 0;
	uint32_t lead = 0;
	uint32_t expectedRecords = 0;
	uint32_t expectedSeed = 0;
	uint32_t count = 0;
	if (!readLe32(in, &magic) || !readLe32(in, &version)
		|| !readLe64(in, &cachedSize) || !readLe64(in, &cachedMtime)
		|| !readLe32(in, &lead) || !readLe32(in, &expectedRecords)
		|| !readLe32(in, &expectedSeed) || !readLe32(in, &count)) {
		return false;
	}
	if (magic != TARGET_CACHE_MAGIC || version != PHASE3_CLI_CACHE_VERSION
		|| cachedSize != csvSize || cachedMtime != csvMtime
		|| lead != static_cast<uint32_t>(config.pickupInputLeadFrames)
		|| expectedRecords != EXPECTED_RECORDS
		|| expectedSeed != config.expectedInitialSeed
		|| count != EXPECTED_RECORDS) {
		return false;
	}
	std::vector<SecondHalfTarget> loaded;
	std::vector<uint8_t> seen(BITMAP_BYTES, 0);
	loaded.reserve(count);
	for (uint32_t i = 0; i < count; ++i) {
		uint16_t upper = 0;
		uint32_t sweep = 0;
		uint32_t frame = 0;
		uint32_t rng = 0;
		if (!readLe16(in, &upper) || !readLe32(in, &sweep) || !readLe32(in, &frame) || !readLe32(in, &rng)) {
			return false;
		}
		// Treat corrupt cache data as a miss. Rebuilding from CSV is cheaper
		// than letting duplicate/out-of-range cached rows fail deep in the run.
		if (sweep > 0x7FFFFFFF || frame > 0x7FFFFFFF) {
			return false;
		}
		const int byteIndex = upper >> 3;
		const int bitIndex = upper & 7;
		if (seen[byteIndex] & (1 << bitIndex)) {
			return false;
		}
		seen[byteIndex] = static_cast<uint8_t>(seen[byteIndex] | (1 << bitIndex));
		SecondHalfTarget target;
		target.upperHalf = upper;
		target.sweepIndex = static_cast<int>(sweep);
		target.frameFromInitialSeed = static_cast<int>(frame);
		target.rngSeed = rng;
		loaded.push_back(target);
	}
	targets->swap(loaded);
	return true;
}

bool parseSecondHalfCsv(const Config& config, std::vector<SecondHalfTarget>* targets, std::string* error) {
	std::ifstream file(config.secondhalfCsvPath.c_str(), std::ios::in | std::ios::binary);
	if (!file) {
		*error = "Could not open secondhalf.csv: " + config.secondhalfCsvPath;
		return false;
	}
	std::string line;
	if (!std::getline(file, line)) {
		*error = "secondhalf.csv is empty.";
		return false;
	}
	const std::vector<std::string> header = splitCsvLine(line);
	std::map<std::string, size_t> columns;
	for (size_t i = 0; i < header.size(); ++i) {
		columns[header[i]] = i;
	}
	const char* required[] = {
		"initial_seed_16bit",
		"target_half_16bit",
		"sweep_index",
		"frame_from_initial_seed",
		"t_minus",
		"rng_seed",
	};
	for (const char* column : required) {
		if (!columns.count(column)) {
			*error = std::string("secondhalf.csv missing required column: ") + column;
			return false;
		}
	}

	std::vector<SecondHalfTarget> byUpper(EXPECTED_RECORDS);
	std::vector<uint8_t> seen(EXPECTED_RECORDS, 0);
	std::vector<uint16_t> seenUppers;
	seenUppers.reserve(EXPECTED_RECORDS);
	std::set<uint16_t> initialSeeds;
	int lineNumber = 1;
	while (std::getline(file, line)) {
		++lineNumber;
		if (line.empty()) {
			continue;
		}
		const std::vector<std::string> cells = splitCsvLine(line);
		auto cell = [&](const char* name, std::string* out) -> bool {
			size_t index = columns[name];
			if (index >= cells.size()) {
				std::ostringstream stream;
				stream << "secondhalf.csv line " << lineNumber << " missing column " << name;
				*error = stream.str();
				return false;
			}
			*out = cells[index];
			return true;
		};

		std::string rawSeed;
		std::string rawTMinus;
		if (!cell("initial_seed_16bit", &rawSeed) || !cell("t_minus", &rawTMinus)) {
			return false;
		}
		uint32_t parsedSeed = 0;
		if (!parseUInt(rawSeed.c_str(), 0xFFFF, &parsedSeed)) {
			*error = "Bad initial_seed_16bit at line " + std::to_string(lineNumber);
			return false;
		}
		initialSeeds.insert(static_cast<uint16_t>(parsedSeed));
		if (rawTMinus != "t-0" && rawTMinus != "T-0") {
			continue;
		}

		std::string rawUpper;
		std::string rawSweep;
		std::string rawFrame;
		std::string rawRng;
		if (!cell("target_half_16bit", &rawUpper)
			|| !cell("sweep_index", &rawSweep)
			|| !cell("frame_from_initial_seed", &rawFrame)
			|| !cell("rng_seed", &rawRng)) {
			return false;
		}
		uint32_t upper = 0;
		uint32_t rng = 0;
		if (!parseUInt(rawUpper.c_str(), 0xFFFF, &upper) || !parseUInt(rawRng.c_str(), 0xFFFFFFFFu, &rng)) {
			*error = "Bad t-0 row numeric field at line " + std::to_string(lineNumber);
			return false;
		}
		uint32_t sweep = 0;
		uint32_t frame = 0;
		// Keep CSV numeric parsing on the same unsigned path as CLI parsing so
		// signed fields cannot pass in through the precomputed schedule file.
		if (!parseUInt(rawSweep.c_str(), 0x7FFFFFFF, &sweep)) {
			*error = "Bad sweep_index at line " + std::to_string(lineNumber);
			return false;
		}
		if (!parseUInt(rawFrame.c_str(), 0x7FFFFFFF, &frame) || static_cast<int>(frame) < config.pickupInputLeadFrames) {
			*error = "Bad frame_from_initial_seed at line " + std::to_string(lineNumber);
			return false;
		}
		if (seen[upper]) {
			*error = "Duplicate t-0 target_half_16bit " + formatU16(upper);
			return false;
		}
		SecondHalfTarget target;
		target.upperHalf = static_cast<uint16_t>(upper);
		target.sweepIndex = static_cast<int>(sweep);
		target.frameFromInitialSeed = static_cast<int>(frame);
		target.rngSeed = rng;
		byUpper[upper] = target;
		seen[upper] = 1;
		seenUppers.push_back(static_cast<uint16_t>(upper));
	}
	if (initialSeeds.size() != 1) {
		*error = "secondhalf.csv must contain exactly one initial seed.";
		return false;
	}
	const uint16_t actualInitialSeed = *initialSeeds.begin();
	// Phase 2 pickup states in this corpus were built for the `secondhalf.csv`
	// seed contract. Rejecting a wrong one-seed CSV here prevents a clean but
	// meaningless run against a schedule from another seed family.
	if (config.checkExpectedInitialSeed && actualInitialSeed != config.expectedInitialSeed) {
		*error = "secondhalf.csv initial seed mismatch: expected=" + formatU16(config.expectedInitialSeed)
			+ " observed=" + formatU16(actualInitialSeed);
		return false;
	}
	if (seenUppers.size() != EXPECTED_RECORDS) {
		std::ostringstream stream;
		stream << "secondhalf.csv must contain " << EXPECTED_RECORDS << " unique t-0 rows, found " << seenUppers.size();
		*error = stream.str();
		return false;
	}
	targets->clear();
	targets->reserve(EXPECTED_RECORDS);
	for (uint16_t upper : seenUppers) {
		targets->push_back(byUpper[upper]);
	}
	return true;
}

bool buildRuntimeSchedule(const Config& config, const std::vector<SecondHalfTarget>& targets, uint32_t observedStartRng, int targetLimit, std::vector<PickupTarget>* scheduled, std::string* error) {
	std::vector<int> indexByUpper(EXPECTED_RECORDS, -1);
	for (size_t i = 0; i < targets.size(); ++i) {
		indexByUpper[targets[i].upperHalf] = static_cast<int>(i);
	}
	const int requestedTargets = targetLimit > 0 && targetLimit < static_cast<int>(targets.size())
		? targetLimit
		: static_cast<int>(targets.size());
	uint32_t state = observedStartRng;
	scheduled->clear();
	scheduled->reserve(requestedTargets);
	for (int eventStep = 1; eventStep <= config.runtimeScheduleMaxSteps; ++eventStep) {
		state = lcrngNext(state);
		const uint16_t upper = state >> 16;
		const int targetIndex = indexByUpper[upper];
		if (eventStep < config.pickupInputLeadFrames || targetIndex < 0) {
			continue;
		}
		const SecondHalfTarget& csvTarget = targets[targetIndex];
		PickupTarget target;
		target.upperHalf = upper;
		target.csvSweepIndex = csvTarget.sweepIndex;
		target.csvFrameFromInitialSeed = csvTarget.frameFromInitialSeed;
		target.csvRngSeed = csvTarget.rngSeed;
		target.eventStepFromStart = eventStep;
		target.inputDeltaFromStart = eventStep - config.pickupInputLeadFrames;
		scheduled->push_back(target);
		indexByUpper[upper] = -1;
		if (static_cast<int>(scheduled->size()) == requestedTargets) {
			return true;
		}
	}
	std::ostringstream stream;
	stream << "Runtime RNG schedule did not cover requested targets: covered=" << scheduled->size()
		<< " requested=" << requestedTargets << " max_steps=" << config.runtimeScheduleMaxSteps;
	*error = stream.str();
	return false;
}

bool writeScheduleCache(const Config& config, const std::string& path, uint32_t observedStartRng, int targetLimit, const std::vector<PickupTarget>& scheduled) {
	if (!makeDirs(cacheDirPath(config))) {
		return false;
	}
	// Schedule cache shares the same parallel-worker safety rule as the parsed
	// target cache: unique temp per process, atomic replacement when possible.
	const std::string tmpPath = cacheTempPathFor(path);
	std::ofstream out(tmpPath.c_str(), std::ios::binary | std::ios::trunc);
	if (!out) {
		return false;
	}
	const int requestedTargets = targetLimit > 0 && targetLimit < EXPECTED_RECORDS ? targetLimit : EXPECTED_RECORDS;
	writeLe32(out, SCHEDULE_CACHE_MAGIC);
	writeLe32(out, PHASE3_CLI_CACHE_VERSION);
	writeLe32(out, observedStartRng);
	writeLe32(out, config.pickupInputLeadFrames);
	writeLe32(out, static_cast<uint32_t>(config.runtimeScheduleMaxSteps));
	writeLe32(out, static_cast<uint32_t>(requestedTargets));
	writeLe32(out, static_cast<uint32_t>(scheduled.size()));
	for (const PickupTarget& target : scheduled) {
		writeLe16(out, target.upperHalf);
		writeLe32(out, static_cast<uint32_t>(target.csvSweepIndex));
		writeLe32(out, static_cast<uint32_t>(target.csvFrameFromInitialSeed));
		writeLe32(out, target.csvRngSeed);
		writeLe32(out, static_cast<uint32_t>(target.eventStepFromStart));
		writeLe32(out, static_cast<uint32_t>(target.inputDeltaFromStart));
	}
	out.close();
	if (!out) {
		std::remove(tmpPath.c_str());
		return false;
	}
	if (!replaceCacheFile(tmpPath, path)) {
		std::remove(tmpPath.c_str());
		return false;
	}
	return true;
}

bool readScheduleCache(const Config& config, const std::string& path, uint32_t observedStartRng, int targetLimit, std::vector<PickupTarget>* scheduled) {
	std::ifstream in(path.c_str(), std::ios::binary);
	if (!in) {
		return false;
	}
	const int requestedTargets = targetLimit > 0 && targetLimit < EXPECTED_RECORDS ? targetLimit : EXPECTED_RECORDS;
	uint32_t magic = 0;
	uint32_t version = 0;
	uint32_t cachedStartRng = 0;
	uint32_t lead = 0;
	uint32_t maxSteps = 0;
	uint32_t cachedRequested = 0;
	uint32_t count = 0;
	if (!readLe32(in, &magic) || !readLe32(in, &version)
		|| !readLe32(in, &cachedStartRng) || !readLe32(in, &lead)
		|| !readLe32(in, &maxSteps) || !readLe32(in, &cachedRequested)
		|| !readLe32(in, &count)) {
		return false;
	}
	if (magic != SCHEDULE_CACHE_MAGIC || version != PHASE3_CLI_CACHE_VERSION
		|| cachedStartRng != observedStartRng
		|| lead != static_cast<uint32_t>(config.pickupInputLeadFrames)
		|| maxSteps != static_cast<uint32_t>(config.runtimeScheduleMaxSteps)
		|| cachedRequested != static_cast<uint32_t>(requestedTargets)
		|| count != static_cast<uint32_t>(requestedTargets)) {
		return false;
	}
	std::vector<PickupTarget> loaded;
	std::vector<uint8_t> seen(BITMAP_BYTES, 0);
	loaded.reserve(count);
	for (uint32_t i = 0; i < count; ++i) {
		uint16_t upper = 0;
		uint32_t sweep = 0;
		uint32_t frame = 0;
		uint32_t rng = 0;
		uint32_t eventStep = 0;
		uint32_t inputDelta = 0;
		if (!readLe16(in, &upper) || !readLe32(in, &sweep) || !readLe32(in, &frame)
			|| !readLe32(in, &rng) || !readLe32(in, &eventStep) || !readLe32(in, &inputDelta)) {
			return false;
		}
		// Cached schedules are an optimization, not authority. Reject bad cache
		// rows here and let the runtime scheduler rebuild from CSV + live RNG.
		if (sweep > 0x7FFFFFFF || frame > 0x7FFFFFFF
			|| eventStep > 0x7FFFFFFF || inputDelta > 0x7FFFFFFF) {
			return false;
		}
		if (eventStep < inputDelta || eventStep - inputDelta != static_cast<uint32_t>(config.pickupInputLeadFrames)) {
			return false;
		}
		const int byteIndex = upper >> 3;
		const int bitIndex = upper & 7;
		if (seen[byteIndex] & (1 << bitIndex)) {
			return false;
		}
		seen[byteIndex] = static_cast<uint8_t>(seen[byteIndex] | (1 << bitIndex));
		PickupTarget target;
		target.upperHalf = upper;
		target.csvSweepIndex = static_cast<int>(sweep);
		target.csvFrameFromInitialSeed = static_cast<int>(frame);
		target.csvRngSeed = rng;
		target.eventStepFromStart = static_cast<int>(eventStep);
		target.inputDeltaFromStart = static_cast<int>(inputDelta);
		loaded.push_back(target);
	}
	scheduled->swap(loaded);
	return true;
}

bool scheduleNeedsChronologicalSort(const std::vector<PickupTarget>& scheduled) {
	for (size_t i = 1; i < scheduled.size(); ++i) {
		const PickupTarget& previous = scheduled[i - 1];
		const PickupTarget& current = scheduled[i];
		if (current.inputDeltaFromStart < previous.inputDeltaFromStart || current.eventStepFromStart < previous.eventStepFromStart) {
			return true;
		}
	}
	return false;
}

void ensurePickupScheduleChronological(std::vector<PickupTarget>* scheduled) {
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

bool validatePickupScheduleOrder(const Config& config, const std::vector<PickupTarget>& scheduled, std::string* error) {
	std::vector<uint8_t> seen(BITMAP_BYTES, 0);
	int previousInputDelta = -1;
	int previousEventStep = -1;
	for (const PickupTarget& target : scheduled) {
		if (target.inputDeltaFromStart < previousInputDelta || target.eventStepFromStart < previousEventStep) {
			*error = "Phase 3 schedule is not chronological at " + formatU16(target.upperHalf);
			return false;
		}
		if (target.eventStepFromStart - target.inputDeltaFromStart != config.pickupInputLeadFrames) {
			*error = "Phase 3 schedule lead mismatch at " + formatU16(target.upperHalf);
			return false;
		}
		const int byteIndex = target.upperHalf >> 3;
		const int bitIndex = target.upperHalf & 7;
		if (seen[byteIndex] & (1 << bitIndex)) {
			*error = "Phase 3 schedule duplicate upper-half target: " + formatU16(target.upperHalf);
			return false;
		}
		seen[byteIndex] = static_cast<uint8_t>(seen[byteIndex] | (1 << bitIndex));
		previousInputDelta = target.inputDeltaFromStart;
		previousEventStep = target.eventStepFromStart;
	}
	return true;
}

struct FrameRunner {
	mCore* core = nullptr;
	int currentKeys = -1;

	explicit FrameRunner(mCore* core)
		: core(core) {
	}

	void setKeys(uint32_t keys) {
		const int nextKeys = static_cast<int>(keys);
		if (currentKeys != nextKeys) {
			core->setKeys(core, nextKeys);
			currentKeys = nextKeys;
		}
	}

	void run(uint32_t keys, int frames) {
		if (frames <= 0) {
			return;
		}
		setKeys(keys);
		for (int i = 0; i < frames; ++i) {
			core->runFrame(core);
		}
	}

	void invalidate() {
		currentKeys = -1;
	}
};

uint32_t readPartyPid(mCore* core, int slotNumber) {
	const uint32_t address = GPLAYER_PARTY_ADDR + (slotNumber - 1) * PARTY_SLOT_SIZE;
	uint32_t pid = 0;
	for (int i = 0; i < 4; ++i) {
		pid |= uint32_t(core->busRead8(core, address + i) & 0xFF) << (8 * i);
	}
	return pid;
}

void readBoxedPartyRecord(mCore* core, int slotNumber, std::vector<uint8_t>* record) {
	if (record->size() != BOX_SLOT_SIZE) {
		record->resize(BOX_SLOT_SIZE);
	}
	const uint32_t address = GPLAYER_PARTY_ADDR + (slotNumber - 1) * PARTY_SLOT_SIZE;
	for (int i = 0; i < BOX_SLOT_SIZE; ++i) {
		(*record)[i] = static_cast<uint8_t>(core->busRead8(core, address + i) & 0xFF);
	}
}

uint32_t readPid(const std::vector<uint8_t>& record) {
	return uint32_t(record[0]) | (uint32_t(record[1]) << 8) | (uint32_t(record[2]) << 16) | (uint32_t(record[3]) << 24);
}

bool saveStateToBuffer(mCore* core, std::vector<uint8_t>* out) {
	VFile* vf = VFileMemChunk(nullptr, 0);
	if (!vf) {
		return false;
	}
	if (!mCoreSaveStateNamed(core, vf, 0)) {
		vf->close(vf);
		return false;
	}
	const ssize_t size = vf->size(vf);
	void* mapped = vf->map(vf, size, MAP_READ);
	if (!mapped) {
		vf->close(vf);
		return false;
	}
	out->assign(static_cast<uint8_t*>(mapped), static_cast<uint8_t*>(mapped) + size);
	vf->unmap(vf, mapped, size);
	vf->close(vf);
	return true;
}

bool loadStateFromBuffer(mCore* core, const std::vector<uint8_t>& buffer) {
	VFile* vf = VFileFromConstMemory(buffer.data(), buffer.size());
	if (!vf) {
		return false;
	}
	const bool ok = mCoreLoadStateNamed(core, vf, 0);
	vf->close(vf);
	return ok;
}

bool readBinaryFile(const std::string& path, std::vector<uint8_t>* out, std::string* error) {
	std::ifstream file(path.c_str(), std::ios::binary);
	if (!file) {
		*error = "Could not open file: " + path;
		return false;
	}
	file.seekg(0, std::ios::end);
	const std::streamoff size = file.tellg();
	if (size < 0) {
		*error = "Could not size file: " + path;
		return false;
	}
	file.seekg(0, std::ios::beg);
	out->resize(static_cast<size_t>(size));
	if (size > 0 && !file.read(reinterpret_cast<char*>(out->data()), size)) {
		*error = "Could not read complete file: " + path;
		return false;
	}
	return true;
}

std::vector<uint8_t> neutralWaitWitness(mCore* core) {
	std::vector<uint8_t> witness;
	witness.reserve(4 + 1 + PARTY_SLOT_SIZE);
	const uint32_t rng = core->busRead32(core, GRNG_VALUE_ADDR);
	witness.push_back(static_cast<uint8_t>(rng & 0xFF));
	witness.push_back(static_cast<uint8_t>((rng >> 8) & 0xFF));
	witness.push_back(static_cast<uint8_t>((rng >> 16) & 0xFF));
	witness.push_back(static_cast<uint8_t>((rng >> 24) & 0xFF));
	witness.push_back(static_cast<uint8_t>(core->busRead8(core, GPLAYER_PARTY_COUNT_ADDR) & 0xFF));
	const uint32_t partySlot2 = GPLAYER_PARTY_ADDR + PARTY_SLOT_SIZE;
	for (int i = 0; i < PARTY_SLOT_SIZE; ++i) {
		witness.push_back(static_cast<uint8_t>(core->busRead8(core, partySlot2 + i) & 0xFF));
	}
	return witness;
}

bool proveNeutralWaitEquivalence(mCore* core, int frames, std::string* error) {
	if (frames <= 0) {
		return true;
	}
	std::vector<uint8_t> baseline;
	if (!saveStateToBuffer(core, &baseline)) {
		*error = "Could not save neutral-wait proof baseline.";
		return false;
	}

	FrameRunner runner(core);
	runner.run(0, frames);
	runner.setKeys(0);
	const uint32_t batchedRng = core->busRead32(core, GRNG_VALUE_ADDR);
	const std::vector<uint8_t> batchedWitness = neutralWaitWitness(core);

	if (!loadStateFromBuffer(core, baseline)) {
		*error = "Could not restore neutral-wait proof baseline.";
		return false;
	}
	for (int i = 0; i < frames; ++i) {
		core->setKeys(core, 0);
		core->runFrame(core);
	}
	core->setKeys(core, 0);
	const uint32_t referenceRng = core->busRead32(core, GRNG_VALUE_ADDR);
	const std::vector<uint8_t> referenceWitness = neutralWaitWitness(core);

	if (batchedRng != referenceRng || batchedWitness != referenceWitness) {
		std::ostringstream stream;
		stream << "Neutral-wait proof failed: frames=" << frames
			<< " batched_rng=" << formatU32(batchedRng)
			<< " reference_rng=" << formatU32(referenceRng)
			<< " batched_witness_bytes=" << batchedWitness.size()
			<< " reference_witness_bytes=" << referenceWitness.size();
		*error = stream.str();
		return false;
	}
	if (!loadStateFromBuffer(core, baseline)) {
		*error = "Could not restore neutral-wait proof baseline after success.";
		return false;
	}
	return true;
}

bool bitmapPresent(const std::vector<uint8_t>& bitmap, int upperHalf) {
	return bitmap[upperHalf >> 3] & (1 << (upperHalf & 7));
}

void appendLe16(std::vector<uint8_t>* bytes, uint16_t value) {
	bytes->push_back(static_cast<uint8_t>(value & 0xFF));
	bytes->push_back(static_cast<uint8_t>((value >> 8) & 0xFF));
}

void appendLe32(std::vector<uint8_t>* bytes, uint32_t value) {
	appendLe16(bytes, static_cast<uint16_t>(value & 0xFFFF));
	appendLe16(bytes, static_cast<uint16_t>((value >> 16) & 0xFFFF));
}

void appendLe64(std::vector<uint8_t>* bytes, uint64_t value) {
	appendLe32(bytes, static_cast<uint32_t>(value & 0xFFFFFFFFu));
	appendLe32(bytes, static_cast<uint32_t>((value >> 32) & 0xFFFFFFFFu));
}

std::string pk3FileName(uint32_t pid) {
	return formatU32(pid) + ".pk3";
}

bool deflateRaw(z_stream* stream, const uint8_t* input, size_t inputSize, std::vector<uint8_t>* output, std::string* error) {
	const int resetStatus = deflateReset(stream);
	if (resetStatus != Z_OK) {
		*error = "Could not reset ZIP deflate stream.";
		return false;
	}
	output->resize(compressBound(static_cast<uLong>(inputSize)));
	stream->next_in = const_cast<Bytef*>(reinterpret_cast<const Bytef*>(input));
	stream->avail_in = static_cast<uInt>(inputSize);
	stream->next_out = reinterpret_cast<Bytef*>(output->data());
	stream->avail_out = static_cast<uInt>(output->size());
	const int status = deflate(stream, Z_FINISH);
	if (status != Z_STREAM_END) {
		*error = "Could not deflate ZIP entry.";
		return false;
	}
	output->resize(stream->total_out);
	return true;
}

bool appendZipEntry(z_stream* stream, const std::string& name, const uint8_t* record, bool storeOnly, std::vector<uint8_t>* compressed, std::vector<uint8_t>* zipBytes, std::vector<ZipCentralEntry>* central, std::string* error) {
	if (storeOnly) {
		compressed->assign(record, record + BOX_SLOT_SIZE);
	} else if (!deflateRaw(stream, record, BOX_SLOT_SIZE, compressed, error)) {
		return false;
	}
	const uint32_t crc = crc32(crc32(0L, Z_NULL, 0), record, BOX_SLOT_SIZE);
	ZipCentralEntry entry;
	entry.name = name;
	entry.method = storeOnly ? ZIP_METHOD_STORE : ZIP_METHOD_DEFLATE;
	entry.crc = crc;
	entry.compressedSize = static_cast<uint32_t>(compressed->size());
	entry.uncompressedSize = BOX_SLOT_SIZE;
	entry.localHeaderOffset = static_cast<uint32_t>(zipBytes->size());

	appendLe32(zipBytes, 0x04034B50);
	appendLe16(zipBytes, 20);
	appendLe16(zipBytes, 0);
	appendLe16(zipBytes, entry.method);
	appendLe16(zipBytes, ZIP_DOS_TIME_MIDNIGHT);
	appendLe16(zipBytes, ZIP_DOS_DATE_2026_01_01);
	appendLe32(zipBytes, crc);
	appendLe32(zipBytes, entry.compressedSize);
	appendLe32(zipBytes, entry.uncompressedSize);
	appendLe16(zipBytes, static_cast<uint16_t>(name.size()));
	appendLe16(zipBytes, 0);
	zipBytes->insert(zipBytes->end(), name.begin(), name.end());
	zipBytes->insert(zipBytes->end(), compressed->begin(), compressed->end());
	central->push_back(entry);
	return true;
}

bool buildZipBytes(const Config& config, const std::vector<uint8_t>& block, const std::vector<uint8_t>& bitmap, int expectedEntries, std::vector<uint8_t>* zipBytes, std::string* error) {
	zipBytes->clear();
	zipBytes->reserve(static_cast<size_t>(std::max(1, expectedEntries)) * 170);
	std::vector<ZipCentralEntry> central;
	central.reserve(expectedEntries);

	z_stream stream;
	std::memset(&stream, 0, sizeof(stream));
	const bool storeOnly = config.zipStoreOnly;
	if (!storeOnly && deflateInit2(&stream, ZIP_DEFLATE_LEVEL, Z_DEFLATED, -MAX_WBITS, 8, Z_DEFAULT_STRATEGY) != Z_OK) {
		*error = "Could not initialize ZIP deflate stream.";
		return false;
	}
	std::vector<uint8_t> compressed;
	for (int upper = 0; upper < EXPECTED_RECORDS; ++upper) {
		if (!bitmapPresent(bitmap, upper)) {
			continue;
		}
		const uint8_t* record = block.data() + upper * BOX_SLOT_SIZE;
		const uint32_t actualPid = uint32_t(record[0]) | (uint32_t(record[1]) << 8) | (uint32_t(record[2]) << 16) | (uint32_t(record[3]) << 24);
		if (!appendZipEntry(&stream, pk3FileName(actualPid), record, storeOnly, &compressed, zipBytes, &central, error)) {
			if (!storeOnly) {
				deflateEnd(&stream);
			}
			return false;
		}
	}
	if (!storeOnly) {
		deflateEnd(&stream);
	}
	if (static_cast<int>(central.size()) != expectedEntries) {
		*error = "ZIP entry count did not match generated record count.";
		return false;
	}

	const uint64_t centralOffset = zipBytes->size();
	for (const ZipCentralEntry& entry : central) {
		appendLe32(zipBytes, 0x02014B50);
		appendLe16(zipBytes, 20);
		appendLe16(zipBytes, 20);
		appendLe16(zipBytes, 0);
		appendLe16(zipBytes, entry.method);
		appendLe16(zipBytes, ZIP_DOS_TIME_MIDNIGHT);
		appendLe16(zipBytes, ZIP_DOS_DATE_2026_01_01);
		appendLe32(zipBytes, entry.crc);
		appendLe32(zipBytes, entry.compressedSize);
		appendLe32(zipBytes, entry.uncompressedSize);
		appendLe16(zipBytes, static_cast<uint16_t>(entry.name.size()));
		appendLe16(zipBytes, 0);
		appendLe16(zipBytes, 0);
		appendLe16(zipBytes, 0);
		appendLe16(zipBytes, 0);
		appendLe32(zipBytes, 0);
		appendLe32(zipBytes, entry.localHeaderOffset);
		zipBytes->insert(zipBytes->end(), entry.name.begin(), entry.name.end());
	}
	const uint64_t centralSize = zipBytes->size() - centralOffset;
	if (central.size() >= 0xFFFF) {
		const uint64_t zip64Offset = zipBytes->size();
		appendLe32(zipBytes, 0x06064B50);
		appendLe64(zipBytes, 44);
		appendLe16(zipBytes, 45);
		appendLe16(zipBytes, 45);
		appendLe32(zipBytes, 0);
		appendLe32(zipBytes, 0);
		appendLe64(zipBytes, central.size());
		appendLe64(zipBytes, central.size());
		appendLe64(zipBytes, centralSize);
		appendLe64(zipBytes, centralOffset);
		appendLe32(zipBytes, 0x07064B50);
		appendLe32(zipBytes, 0);
		appendLe64(zipBytes, zip64Offset);
		appendLe32(zipBytes, 1);
	}
	appendLe32(zipBytes, 0x06054B50);
	appendLe16(zipBytes, 0);
	appendLe16(zipBytes, 0);
	appendLe16(zipBytes, central.size() >= 0xFFFF ? 0xFFFF : static_cast<uint16_t>(central.size()));
	appendLe16(zipBytes, central.size() >= 0xFFFF ? 0xFFFF : static_cast<uint16_t>(central.size()));
	appendLe32(zipBytes, static_cast<uint32_t>(centralSize));
	appendLe32(zipBytes, static_cast<uint32_t>(centralOffset));
	appendLe16(zipBytes, 0);
	return true;
}

bool writeBinaryAtomic(const std::string& finalPath, const std::vector<uint8_t>& bytes, bool overwrite, std::string* error) {
	// Process-local temp names keep duplicate same-lane workers from sharing
	// one temp ZIP while still allowing stale temps to be cleaned on retry.
	const std::string tmpPath = zipTempPathFor(finalPath);
	if (!overwrite) {
		std::ifstream existing(finalPath.c_str(), std::ios::binary);
		if (existing.good()) {
			*error = "Output exists; pass --overwrite to replace: " + finalPath;
			return false;
		}
	}
	std::ofstream file(tmpPath.c_str(), std::ios::binary | std::ios::trunc);
	if (!file) {
		*error = "Could not write temp ZIP: " + tmpPath;
		return false;
	}
	file.write(reinterpret_cast<const char*>(bytes.data()), bytes.size());
	file.close();
	if (!file) {
		*error = "Could not finish temp ZIP write: " + tmpPath;
		return false;
	}
#ifdef _WIN32
	const DWORD moveFlags = MOVEFILE_WRITE_THROUGH | (overwrite ? MOVEFILE_REPLACE_EXISTING : 0);
	if (!MoveFileExA(tmpPath.c_str(), finalPath.c_str(), moveFlags)) {
		const DWORD moveError = GetLastError();
		std::ostringstream stream;
		if (!overwrite && (moveError == ERROR_ALREADY_EXISTS || moveError == ERROR_FILE_EXISTS)) {
			stream << "Output exists; pass --overwrite to replace: " << finalPath;
		} else {
			stream << "Could not move temp ZIP into place, GetLastError=" << moveError;
		}
		*error = stream.str();
		std::remove(tmpPath.c_str());
		return false;
	}
#else
	if (overwrite) {
		// POSIX `rename` replaces an existing destination atomically when source
		// and destination are on the same filesystem. Do not unlink the final ZIP
		// first; if rename then failed, the last valid archive would be lost.
		if (std::rename(tmpPath.c_str(), finalPath.c_str())) {
			*error = "Could not move temp ZIP into place.";
			std::remove(tmpPath.c_str());
			return false;
		}
	} else {
		// No-overwrite publish must be race-safe too. A hard link creates the
		// final name only if it did not appear while the lane was generating.
		if (link(tmpPath.c_str(), finalPath.c_str())) {
			*error = errno == EEXIST
				? "Output exists; pass --overwrite to replace: " + finalPath
				: "Could not move temp ZIP into place.";
			std::remove(tmpPath.c_str());
			return false;
		}
		std::remove(tmpPath.c_str());
	}
#endif
	return true;
}

bool runPhase3(const Config& config, std::string* error) {
	TimingBuckets timing;
	CacheStats cacheStats;
	LearnedPickupDelayState learnedDelay;
	learnedDelay.enabled = config.learnPickupDelay;
	learnedDelay.sampleLimit = config.learnPickupDelaySamples;

	if (!makeDirs(config.outputDir)) {
		*error = "Could not create output directory: " + config.outputDir;
		return false;
	}
	if (!config.overwrite && pathExists(outputZipPath(config))) {
		*error = "Output exists; pass --overwrite to replace: " + outputZipPath(config);
		return false;
	}
	writeStatus(config, "running", 0, config.limit > 0 ? config.limit : EXPECTED_RECORDS);

	std::vector<SecondHalfTarget> csvTargets;
	cacheStats.targetCachePath = targetCachePath(config);
	{
		const uint64_t start = nowNs();
		cacheStats.targetCacheHit = readTargetCache(config, cacheStats.targetCachePath, &csvTargets);
		if (!cacheStats.targetCacheHit) {
			if (!parseSecondHalfCsv(config, &csvTargets, error)) {
				timing.csvTargetCacheNs += nowNs() - start;
				writeStatus(config, "failed", 0, 0, *error, 0, &timing, &cacheStats, &learnedDelay);
				return false;
			}
			writeTargetCache(config, cacheStats.targetCachePath, csvTargets);
		}
		timing.csvTargetCacheNs += nowNs() - start;
	}
	struct mCore* core = mCoreFind(config.romPath.c_str());
	if (!core) {
		*error = "Could not find mGBA core for ROM: " + config.romPath;
		writeStatus(config, "failed", 0, 0, *error, 0, &timing, &cacheStats, &learnedDelay);
		return false;
	}
	if (!core->init(core)) {
		*error = "Could not initialize mGBA core.";
		writeStatus(config, "failed", 0, 0, *error, 0, &timing, &cacheStats, &learnedDelay);
		return false;
	}
	bool clean = false;
	bool romLoaded = false;
	uint32_t observedStartRng = 0;
	std::vector<PickupTarget> schedule;
	std::vector<uint8_t> block;
	std::vector<uint8_t> bitmap;
	std::vector<uint8_t> phase2StateBytes;
	int written = 0;
	int pickupDetectMin = 0;
	int pickupDetectMax = 0;
	uint64_t pickupDetectTotal = 0;
	bool havePickupDetectStats = false;

	mCoreInitConfig(core, "spindaPhase3Cli");
	struct mCoreOptions opts = {};
	mCoreConfigMap(&core->config, &opts);
	opts.audioSync = false;
	opts.videoSync = false;
	mCoreConfigLoadDefaults(&core->config, &opts);
	mCoreConfigSetDefaultValue(&core->config, "idleOptimization", "detect");
	mCoreLoadConfig(core);

	if (!mCoreLoadFile(core, config.romPath.c_str())) {
		*error = "Could not load ROM: " + config.romPath;
		goto done;
	}
	romLoaded = true;
	core->reset(core);
	{
		const uint64_t start = nowNs();
		if (!readBinaryFile(config.phase2StatePath, &phase2StateBytes, error)) {
			timing.stateLoadNs += nowNs() - start;
			goto done;
		}
		if (!loadStateFromBuffer(core, phase2StateBytes)) {
			*error = "Could not load Phase 2 state: " + config.phase2StatePath;
			timing.stateLoadNs += nowNs() - start;
			goto done;
		}
		timing.stateLoadNs += nowNs() - start;
	}
	if (phase2StateBytes.empty()) {
		*error = "Could not load Phase 2 state: " + config.phase2StatePath;
		goto done;
	}
	observedStartRng = core->busRead32(core, GRNG_VALUE_ADDR);
	if (config.checkExpectedBaselineRng && observedStartRng != config.expectedBaselineRng) {
		*error = "Phase 3 starting RNG mismatch: expected=" + formatU32(config.expectedBaselineRng)
			+ " observed=" + formatU32(observedStartRng);
		goto done;
	}
	if (config.neutralWaitProofFrames > 0 && !proveNeutralWaitEquivalence(core, config.neutralWaitProofFrames, error)) {
		goto done;
	}
	// `--limit` is a proof/benchmark knob. Stop schedule construction once the
	// requested chronological pickup targets are found instead of walking the
	// whole 65,536-target lane for a two-record smoke test.
	cacheStats.scheduleCachePath = scheduleCachePath(config, observedStartRng, config.limit);
	{
		const uint64_t start = nowNs();
		cacheStats.scheduleCacheHit = readScheduleCache(config, cacheStats.scheduleCachePath, observedStartRng, config.limit, &schedule);
		if (!cacheStats.scheduleCacheHit) {
			if (!buildRuntimeSchedule(config, csvTargets, observedStartRng, config.limit, &schedule, error)) {
				timing.scheduleCacheNs += nowNs() - start;
				goto done;
			}
			writeScheduleCache(config, cacheStats.scheduleCachePath, observedStartRng, config.limit, schedule);
		}
		timing.scheduleCacheNs += nowNs() - start;
	}
	// Runtime schedule construction already walks RNG event steps forward.
	// Keep a cheap guard here so future cached/imported schedules can be
	// repaired without adding sort cost to the normal production path.
	ensurePickupScheduleChronological(&schedule);
	if (!validatePickupScheduleOrder(config, schedule, error)) {
		goto done;
	}
	// Delay lane-sized buffers until after CSV/state/proof validation. Failed
	// launches then avoid reserving the full 5 MiB PK3 block.
	block.assign(EXPECTED_RECORDS * BOX_SLOT_SIZE, 0);
	bitmap.assign(BITMAP_BYTES, 0);

	{
		FrameRunner frames(core);
		int currentInputDelta = 0;
		std::vector<uint8_t> scratchState;
		std::vector<uint8_t> record;
		for (const PickupTarget& target : schedule) {
			const int delta = target.inputDeltaFromStart - currentInputDelta;
			if (delta < 0) {
				*error = "Phase 3 schedule moved backward at " + formatU16(target.upperHalf);
				goto done;
			}
			uint64_t timer = nowNs();
			frames.run(0, delta);
			timing.frameAdvanceNs += nowNs() - timer;
			currentInputDelta = target.inputDeltaFromStart;
			timer = nowNs();
			if (!saveStateToBuffer(core, &scratchState)) {
				timing.scratchSaveNs += nowNs() - timer;
				*error = "Could not save scratch state at " + formatU16(target.upperHalf);
				goto done;
			}
			timing.scratchSaveNs += nowNs() - timer;
			timer = nowNs();
			frames.run(A_BUTTON_MASK, config.pickupHoldFrames);
			timing.pickupWaitDetectNs += nowNs() - timer;
			const uint32_t expectedPid = (uint32_t(target.upperHalf) << 16) | config.laneId;
			int elapsedPickupFrames = 0;
			int lastCheckedPickupFrame = -1;
			int partyCount = 0;
			uint32_t pid = 0;
			bool foundPickup = false;
			auto waitToPickupFrame = [&](int targetFrame) {
				if (targetFrame > elapsedPickupFrames) {
					const uint64_t waitStart = nowNs();
					frames.run(0, targetFrame - elapsedPickupFrames);
					timing.pickupWaitDetectNs += nowNs() - waitStart;
					elapsedPickupFrames = targetFrame;
				}
			};
			auto checkPickupNow = [&](int waitedFrame) {
				const uint64_t checkStart = nowNs();
				lastCheckedPickupFrame = waitedFrame;
				partyCount = core->busRead8(core, GPLAYER_PARTY_COUNT_ADDR) & 0xFF;
				if (partyCount < 2) {
					timing.pickupWaitDetectNs += nowNs() - checkStart;
					return false;
				}
				pid = readPartyPid(core, 2);
				if (pid != expectedPid) {
					timing.pickupWaitDetectNs += nowNs() - checkStart;
					return false;
				}
				timing.pickupWaitDetectNs += nowNs() - checkStart;
				// Hot path reads only four PID bytes until pickup is proven.
				// Full boxed PK3 read happens once per successful target.
				const uint64_t readStart = nowNs();
				readBoxedPartyRecord(core, 2, &record);
				timing.pk3ReadNs += nowNs() - readStart;
				pid = readPid(record);
				foundPickup = pid == expectedPid;
				return foundPickup;
			};
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
			if (!foundPickup && config.fastPickupChecks) {
				int fastFrames[2] = {config.fastPickupCheckFirstFrame, config.fastPickupCheckSecondFrame};
				if (fastFrames[1] < fastFrames[0]) {
					std::swap(fastFrames[0], fastFrames[1]);
				}
				for (int frame : fastFrames) {
					if (frame < config.minPickupDetectFrame || frame > config.maxPickupDetectFrames || frame == lastCheckedPickupFrame) {
						continue;
					}
					waitToPickupFrame(frame);
					if (checkPickupNow(frame)) {
						break;
					}
				}
			}
			const int fallbackStartFrame = std::max(config.minPickupDetectFrame, lastCheckedPickupFrame + 1);
			for (int waited = fallbackStartFrame; !foundPickup && waited <= config.maxPickupDetectFrames; ++waited) {
				waitToPickupFrame(waited);
				if (checkPickupNow(waited)) {
					break;
				}
			}
			if (!foundPickup) {
				std::ostringstream stream;
				stream << "Extracted PID mismatch: target_upper=" << formatU16(target.upperHalf)
					<< " expected_pid=" << formatU32(expectedPid)
					<< " observed_pid=" << formatU32(pid)
					<< " party_count=" << partyCount;
				*error = stream.str();
				goto done;
			}
			pickupDetectTotal += static_cast<uint64_t>(lastCheckedPickupFrame);
			pickupDetectMin = havePickupDetectStats ? std::min(pickupDetectMin, lastCheckedPickupFrame) : lastCheckedPickupFrame;
			pickupDetectMax = havePickupDetectStats ? std::max(pickupDetectMax, lastCheckedPickupFrame) : lastCheckedPickupFrame;
			havePickupDetectStats = true;
			if (learnedDelay.enabled && !learnedDelay.active && !learnedDelay.disabledAfterMismatch && learnedDelay.sampleCount < learnedDelay.sampleLimit) {
				learnedDelay.sampleMin = learnedDelay.sampleCount ? std::min(learnedDelay.sampleMin, lastCheckedPickupFrame) : lastCheckedPickupFrame;
				learnedDelay.sampleMax = learnedDelay.sampleCount ? std::max(learnedDelay.sampleMax, lastCheckedPickupFrame) : lastCheckedPickupFrame;
				++learnedDelay.sampleCount;
				if (learnedDelay.sampleCount >= learnedDelay.sampleLimit && learnedDelay.sampleMin == learnedDelay.sampleMax) {
					learnedDelay.learnedDelay = learnedDelay.sampleMin;
					learnedDelay.active = true;
				}
			}
			const int byteIndex = target.upperHalf >> 3;
			const int bitIndex = target.upperHalf & 7;
			if (bitmap[byteIndex] & (1 << bitIndex)) {
				*error = "Duplicate upper-half record: " + formatU16(target.upperHalf);
				goto done;
			}
			std::memcpy(block.data() + target.upperHalf * BOX_SLOT_SIZE, record.data(), BOX_SLOT_SIZE);
			bitmap[byteIndex] = static_cast<uint8_t>(bitmap[byteIndex] | (1 << bitIndex));
			++written;
			timer = nowNs();
			if (!loadStateFromBuffer(core, scratchState)) {
				timing.scratchRestoreNs += nowNs() - timer;
				*error = "Could not restore scratch state at " + formatU16(target.upperHalf);
				goto done;
			}
			timing.scratchRestoreNs += nowNs() - timer;
			frames.invalidate();
			frames.setKeys(0);
		}
	}

	{
		const uint64_t start = nowNs();
		std::vector<uint8_t> zipBytes;
		if (!buildZipBytes(config, block, bitmap, written, &zipBytes, error)) {
			timing.zipBuildWriteNs += nowNs() - start;
			goto done;
		}
		if (!writeBinaryAtomic(outputZipPath(config), zipBytes, config.overwrite, error)) {
			timing.zipBuildWriteNs += nowNs() - start;
			goto done;
		}
		timing.zipBuildWriteNs += nowNs() - start;
	}
	clean = true;

done:
	const double pickupDetectAvg = written > 0 ? static_cast<double>(pickupDetectTotal) / static_cast<double>(written) : 0.0;
	if (!clean) {
		const bool noOverwriteOutputExists = !config.overwrite
			&& error->find("Output exists; pass --overwrite to replace: ") == 0;
		if (!noOverwriteOutputExists) {
			writeStatus(config, "failed", written, schedule.empty() ? 0 : static_cast<int>(schedule.size()), *error, observedStartRng, &timing, &cacheStats, &learnedDelay, pickupDetectMin, pickupDetectMax, pickupDetectAvg);
		}
	} else {
		writeStatus(config, "complete", written, static_cast<int>(schedule.size()), std::string(), observedStartRng, &timing, &cacheStats, &learnedDelay, pickupDetectMin, pickupDetectMax, pickupDetectAvg);
	}
	if (romLoaded) {
		core->unloadROM(core);
	}
	mCoreConfigFreeOpts(&opts);
	mCoreConfigDeinit(&core->config);
	core->deinit(core);
	return clean;
}

void usage(const char* argv0) {
	std::fprintf(stderr,
		"Usage: %s --rom PATH --lane 0x0001 --phase2-state PATH --secondhalf-csv PATH --output-dir DIR [options]\n"
		"       %s --rom PATH --lanes 0x0001-0x0004 --phase2-dir DIR --secondhalf-csv PATH --output-dir DIR [options]\n"
		"Options:\n"
		"  --lanes LIST\n"
		"  --phase2-dir DIR\n"
		"  --cache-dir DIR\n"
		"  --limit N\n"
		"  --overwrite\n"
		"  --expected-initial-seed 0xCD39\n"
		"  --no-expected-initial-seed-check\n"
		"  --expected-rng 0x2B0C94C1\n"
		"  --no-expected-rng-check\n"
		"  --pickup-lead N\n"
		"  --pickup-hold N\n"
		"  --min-pickup-detect-frame N\n"
		"  --fast-pickup-check-first-frame N\n"
		"  --fast-pickup-check-second-frame N\n"
		"  --no-fast-pickup-checks\n"
		"  --max-pickup-detect-frames N\n"
		"  --learn-pickup-delay-samples N\n"
		"  --no-learn-pickup-delay\n"
		"  --runtime-schedule-max-steps N\n"
		"  --neutral-wait-proof-frames N\n"
		"  --zip-store\n"
		"  --verbose-core-logs\n",
		argv0,
		argv0);
}

bool parseArgs(int argc, char** argv, Config* config) {
	// Keep the single-lane and bundled-lane modes exclusive. Mixing them would
	// make lane order ambiguous and could duplicate a lane inside one process.
	bool sawLane = false;
	bool sawLanes = false;
	for (int i = 1; i < argc; ++i) {
		const std::string arg = argv[i];
		auto needValue = [&](const char* name) -> const char* {
			if (i + 1 >= argc) {
				std::fprintf(stderr, "%s needs a value\n", name);
				return nullptr;
			}
			return argv[++i];
		};
		if (arg == "--rom") {
			const char* value = needValue("--rom");
			if (!value) return false;
			config->romPath = value;
		} else if (arg == "--lane") {
			if (sawLanes) {
				std::fprintf(stderr, "--lane and --lanes cannot be mixed\n");
				return false;
			}
			sawLane = true;
			const char* value = needValue("--lane");
			uint32_t parsed = 0;
			if (!value || !parseUInt(value, 0xFFFF, &parsed)) return false;
			config->laneId = static_cast<uint16_t>(parsed);
			config->laneIds.push_back(config->laneId);
		} else if (arg == "--lanes") {
			if (sawLane) {
				std::fprintf(stderr, "--lane and --lanes cannot be mixed\n");
				return false;
			}
			sawLanes = true;
			const char* value = needValue("--lanes");
			if (!parseLaneList(value, &config->laneIds)) return false;
		} else if (arg == "--phase2-state") {
			const char* value = needValue("--phase2-state");
			if (!value) return false;
			config->phase2StatePath = value;
		} else if (arg == "--phase2-dir") {
			const char* value = needValue("--phase2-dir");
			if (!value) return false;
			config->phase2StateDirPath = value;
		} else if (arg == "--secondhalf-csv") {
			const char* value = needValue("--secondhalf-csv");
			if (!value) return false;
			config->secondhalfCsvPath = value;
		} else if (arg == "--output-dir") {
			const char* value = needValue("--output-dir");
			if (!value) return false;
			config->outputDir = value;
		} else if (arg == "--cache-dir") {
			const char* value = needValue("--cache-dir");
			if (!value) return false;
			config->cacheDirPath = value;
		} else if (arg == "--limit") {
			const char* value = needValue("--limit");
			uint32_t parsed = 0;
			if (!value || !parseUInt(value, EXPECTED_RECORDS, &parsed)) return false;
			config->limit = static_cast<int>(parsed);
		} else if (arg == "--overwrite") {
			config->overwrite = true;
		} else if (arg == "--expected-initial-seed") {
			const char* value = needValue("--expected-initial-seed");
			uint32_t parsed = 0;
			if (!value || !parseUInt(value, 0xFFFF, &parsed)) return false;
			config->expectedInitialSeed = static_cast<uint16_t>(parsed);
		} else if (arg == "--no-expected-initial-seed-check") {
			config->checkExpectedInitialSeed = false;
		} else if (arg == "--expected-rng") {
			const char* value = needValue("--expected-rng");
			if (!value || !parseUInt(value, 0xFFFFFFFFu, &config->expectedBaselineRng)) return false;
		} else if (arg == "--no-expected-rng-check") {
			config->checkExpectedBaselineRng = false;
		} else if (arg == "--pickup-lead") {
			const char* value = needValue("--pickup-lead");
			uint32_t parsed = 0;
			if (!value || !parseUInt(value, 120, &parsed)) return false;
			config->pickupInputLeadFrames = static_cast<int>(parsed);
		} else if (arg == "--pickup-hold") {
			const char* value = needValue("--pickup-hold");
			uint32_t parsed = 0;
			if (!value || !parseUInt(value, 120, &parsed)) return false;
			config->pickupHoldFrames = static_cast<int>(parsed);
		} else if (arg == "--min-pickup-detect-frame") {
			const char* value = needValue("--min-pickup-detect-frame");
			uint32_t parsed = 0;
			if (!value || !parseUInt(value, 120, &parsed)) return false;
			config->minPickupDetectFrame = static_cast<int>(parsed);
		} else if (arg == "--fast-pickup-check-first-frame") {
			const char* value = needValue("--fast-pickup-check-first-frame");
			uint32_t parsed = 0;
			if (!value || !parseUInt(value, 120, &parsed)) return false;
			config->fastPickupCheckFirstFrame = static_cast<int>(parsed);
		} else if (arg == "--fast-pickup-check-second-frame") {
			const char* value = needValue("--fast-pickup-check-second-frame");
			uint32_t parsed = 0;
			if (!value || !parseUInt(value, 120, &parsed)) return false;
			config->fastPickupCheckSecondFrame = static_cast<int>(parsed);
		} else if (arg == "--no-fast-pickup-checks") {
			config->fastPickupChecks = false;
		} else if (arg == "--max-pickup-detect-frames") {
			const char* value = needValue("--max-pickup-detect-frames");
			uint32_t parsed = 0;
			if (!value || !parseUInt(value, 120, &parsed)) return false;
			config->maxPickupDetectFrames = static_cast<int>(parsed);
		} else if (arg == "--learn-pickup-delay-samples") {
			const char* value = needValue("--learn-pickup-delay-samples");
			uint32_t parsed = 0;
			if (!value || !parseUInt(value, 4096, &parsed)) return false;
			config->learnPickupDelaySamples = static_cast<int>(parsed);
		} else if (arg == "--no-learn-pickup-delay") {
			config->learnPickupDelay = false;
		} else if (arg == "--runtime-schedule-max-steps") {
			const char* value = needValue("--runtime-schedule-max-steps");
			uint32_t parsed = 0;
			if (!value || !parseUInt(value, 100000000, &parsed)) return false;
			config->runtimeScheduleMaxSteps = static_cast<int>(parsed);
		} else if (arg == "--neutral-wait-proof-frames") {
			const char* value = needValue("--neutral-wait-proof-frames");
			uint32_t parsed = 0;
			if (!value || !parseUInt(value, 1000000, &parsed)) return false;
			config->neutralWaitProofFrames = static_cast<int>(parsed);
		} else if (arg == "--zip-store") {
			config->zipStoreOnly = true;
		} else if (arg == "--verbose-core-logs") {
			config->quietCoreLogs = false;
		} else if (arg == "--help" || arg == "-h") {
			config->showHelp = true;
			return true;
		} else {
			std::fprintf(stderr, "Unknown argument: %s\n", arg.c_str());
			return false;
		}
	}
	if (config->showHelp) {
		return true;
	}
	if (config->laneIds.empty()) {
		config->laneIds.push_back(config->laneId);
	}
	if (config->romPath.empty() || config->secondhalfCsvPath.empty() || config->outputDir.empty()) {
		return false;
	}
	if (config->phase2StatePath.empty() && config->phase2StateDirPath.empty()) {
		return false;
	}
	if (config->laneIds.size() > 1 && config->phase2StateDirPath.empty()) {
		return false;
	}
	if (config->minPickupDetectFrame > config->maxPickupDetectFrames) {
		return false;
	}
	if (config->pickupHoldFrames <= 0) {
		// A zero-frame A press sets no emulated input frame at all; fail before
		// spending time building schedules and loading savestates.
		return false;
	}
	if (config->learnPickupDelaySamples <= 0) {
		return false;
	}
	return true;
}

} // namespace

int main(int argc, char** argv) {
	Config config;
	if (!parseArgs(argc, argv, &config)) {
		usage(argv[0]);
		return 2;
	}
	if (config.showHelp) {
		usage(argv[0]);
		return 0;
	}
	mLogger quietLogger = {};
	if (config.quietCoreLogs) {
		quietLogger.log = quietCoreLog;
		mLogSetDefaultLogger(&quietLogger);
	}
	for (uint16_t laneId : config.laneIds) {
		Config laneConfig = config;
		laneConfig.laneId = laneId;
		laneConfig.phase2StatePath = phase2StatePathForLane(config, laneId);
		std::string error;
		if (!runPhase3(laneConfig, &error)) {
			std::fprintf(stderr, "Phase 3 failed for %s: %s\n", laneHex(laneConfig).c_str(), error.c_str());
			return 1;
		}
	}
	return 0;
}


