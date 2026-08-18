// SPDX-License-Identifier: MPL-2.0
// COLLECTOR_TOGAMI_SOURCE_CREDIT_2026-05-11
// Project credit: Collector Togami is the person behind the Spinda/SPC3 project as a whole and is credited as project originator, coordinator, and driving force.
//
// SPC3 Phase 3 compression prototype -- STREAMING VARIANT.
//
// Streaming variant by Shawrkie. Integrated into the Spinda project as the
// memory-bounded SPC3 prototype implementation.
// Shawrkie also helped with SPC3 compressor/decompressor work and contributed
// compute for corpus processing and verification. Keep this credit with source
// and binary packages that include this tool.
//
// Memory-bounded variant of spc3_prototype.cpp. Identical SPC3 file format,
// identical report JSON shape. Only pack and verify modes have been rewritten
// to process lanes one at a time so peak working set is ~5 MB per lane instead
// of holding every LaneModel / decoded payload resident at once.
//
// Pack: each lane is compressed and roundtrip-verified, then its stream bytes
// are appended to a temp file (<output>.streamdata.tmp); after all lanes the
// final SPC3 is assembled as header + predictor + table + cat(temp).
// Unpack/Verify/Bench: decoded lanes are streamed through callbacks and dropped
// after transform/compare/report; decoded lane vectors are no longer
// materialized.
//
// Other modes (audit, inspect, consolidate) are unchanged from the legacy
// prototype; their working sets were not the OOM bottleneck.
//
// This tool is still a prototype, but it now owns the full measurement loop:
// audit Phase 3 lane ZIPs, pack/inspect/verify/unpack SPC3 containers, compare
// zlib/zstd/LZMA2/rANS typed streams, profile CPU decode slices, and optionally
// offload v0.2 typed level-3 rebuild to CUDA/NVRTC with CPU fallback.
//
// The hot model keeps one lane as a contiguous 65536 * 80 byte buffer so CPU
// assembly/SIMD and GPU rebuild paths both work over predictable memory.

#include <lzma.h>
#include <zlib.h>
#include <zstd.h>

#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cctype>
#include <cstdlib>
#include <cstdint>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <map>
#include <memory>
#include <mutex>
#include <optional>
#include <sstream>
#include <stdexcept>
#include <string>
#include <string_view>
#include <thread>
#include <utility>
#include <vector>

#if defined(_WIN32)
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#endif

#if defined(__x86_64__) && defined(_WIN64)
#define SPC3_USE_X86_64_ASM 1
extern "C" uint16_t spc3_checksum_asm(const uint8_t* data);
extern "C" int spc3_template_match_asm(const uint8_t* base, const uint8_t* candidate);
extern "C" void spc3_shuffle48_asm(uint8_t* data, const uint8_t* order);
extern "C" void spc3_xor48_asm(uint8_t* data, uint32_t seed);
extern "C" uint64_t spc3_expand_iv32_exceptions_asm(
    uint32_t* out,
    const uint32_t* predictor,
    const uint8_t* bitmap,
    const uint32_t* values);
#else
#define SPC3_USE_X86_64_ASM 0
#endif

namespace fs = std::filesystem;

namespace {

constexpr uint32_t kExpectedRecords = 0x10000;
constexpr uint32_t kRecordSize = 80;
constexpr uint32_t kPayloadSize = kExpectedRecords * kRecordSize;
constexpr uint64_t kLevel3ExceptionBitmapBytes = kExpectedRecords / 8ULL;
constexpr uint64_t kLevel3ExceptionValueMaxBytes = kExpectedRecords * 4ULL;
constexpr uint64_t kLevel3ModelMinSize = kRecordSize + kLevel3ExceptionBitmapBytes;
constexpr uint64_t kLevel3ModelMaxSize = kLevel3ModelMinSize + kLevel3ExceptionValueMaxBytes;
constexpr uint64_t kGpuBulkDownloadLimitBytes = 512ULL * 1024ULL * 1024ULL;
constexpr uint64_t kGpuRebuildChunkBytesBudget = 256ULL * 1024ULL * 1024ULL;
constexpr uint32_t kZipMethodStore = 0;
constexpr uint32_t kZipMethodDeflate = 8;
constexpr uint16_t kZipFlagEncrypted = 0x0001;
constexpr uint16_t kZipFlagDataDescriptor = 0x0008;
constexpr uint16_t kZipFlagStrongEncrypted = 0x0040;
constexpr uint32_t kSpc3VersionV1 = 1;
constexpr uint32_t kSpc3VersionV2 = 2;
constexpr uint32_t kSpc3HeaderSize = 80;
constexpr uint64_t kSpc3TableEntrySize = 96;
constexpr uint32_t kSpc3FlagPredictorEmbedded = 0x00000001;
constexpr uint32_t kSpc3KnownFlags = kSpc3FlagPredictorEmbedded;
// v0.1 keeps the 96-byte table fixed; codec metadata lives in the existing
// per-entry flags word so old flags=0 files still decode as legacy zlib.
constexpr uint32_t kSpc3EntryCodecIdMask = 0x000000FF;
constexpr uint32_t kSpc3EntryCodecLevelMask = 0x0000FF00;
constexpr uint32_t kSpc3EntryCodecSettingsMask = 0x00FF0000;
constexpr uint32_t kSpc3EntryStreamFlagsMask = 0xFF000000;
constexpr uint64_t kSpc3TypedLevel3SubstreamCount = 3;
constexpr uint64_t kSpc3TypedLevel3SubstreamEntrySize = 32;
constexpr uint32_t kSpc3StreamKindTypedLevel3 = 4;
constexpr uint32_t kSpc3TypedSubstreamTemplate = 1;
constexpr uint32_t kSpc3TypedSubstreamBitmap = 2;
constexpr uint32_t kSpc3TypedSubstreamValues = 3;
constexpr int kZlibDefaultLevel = 9;
constexpr int kZstdDefaultLevel = 3;
constexpr int kZstdRecommendedLevel = 9;
constexpr int kLzma2DefaultPreset = 9;
constexpr uint64_t kFnv1a64Offset = 14695981039346656037ULL;
constexpr uint64_t kFnv1a64Prime = 1099511628211ULL;

enum class CodecId : uint32_t {
    LegacyAuto = 0,
    None = 1,
    Zlib = 2,
    Zstd = 3,
    Lzma2 = 4,
    Rans = 5,
};

enum class CodecProfile {
    None,
    Compat,
    Fast,
    Small,
};

enum class UnpackFormat {
    Zip,
    Raw,
};

enum class LaneSelectMode {
    All,
    One,
    Range,
};

enum class Pk3CorpusState {
    Egg,
    HatchedShiny,
    HatchedNotShiny,
};

struct CodecSpec {
    CodecId id = CodecId::LegacyAuto;
    int level = 0;
    uint32_t settings = 0;
};

const char* hotloop_backend() {
#if SPC3_USE_X86_64_ASM
    return "x86_64_asm";
#else
    return "c_fallback";
#endif
}

constexpr std::array<std::array<uint8_t, 4>, 24> kBlockPosition = {{
    {{0, 1, 2, 3}},
    {{0, 1, 3, 2}},
    {{0, 2, 1, 3}},
    {{0, 3, 1, 2}},
    {{0, 2, 3, 1}},
    {{0, 3, 2, 1}},
    {{1, 0, 2, 3}},
    {{1, 0, 3, 2}},
    {{2, 0, 1, 3}},
    {{3, 0, 1, 2}},
    {{2, 0, 3, 1}},
    {{3, 0, 2, 1}},
    {{1, 2, 0, 3}},
    {{1, 3, 0, 2}},
    {{2, 1, 0, 3}},
    {{3, 1, 0, 2}},
    {{2, 3, 0, 1}},
    {{3, 2, 0, 1}},
    {{1, 2, 3, 0}},
    {{1, 3, 2, 0}},
    {{2, 1, 3, 0}},
    {{3, 1, 2, 0}},
    {{2, 3, 1, 0}},
    {{3, 2, 1, 0}},
}};

constexpr std::array<uint8_t, 24> kBlockPositionInvertSelector = {{
    0, 1, 2, 4,
    3, 5, 6, 7,
    12, 18, 13, 19,
    8, 10, 14, 20,
    16, 22, 9, 11,
    15, 21, 17, 23,
}};

struct Stopwatch {
    std::chrono::steady_clock::time_point start = std::chrono::steady_clock::now();

    double elapsed_ms() const {
        const auto now = std::chrono::steady_clock::now();
        return std::chrono::duration<double, std::milli>(now - start).count();
    }
};

struct ScopedTimer {
    double& target;
    Stopwatch watch;

    explicit ScopedTimer(double& target_ms) : target(target_ms) {}

    ~ScopedTimer() {
        target += watch.elapsed_ms();
    }
};

uint16_t read_u16(const std::vector<uint8_t>& data, size_t offset) {
    if (offset > data.size() || 2 > data.size() - offset) {
        throw std::runtime_error("short read_u16");
    }
    return static_cast<uint16_t>(data[offset]) |
           static_cast<uint16_t>(data[offset + 1] << 8);
}

uint32_t read_u32(const std::vector<uint8_t>& data, size_t offset) {
    if (offset > data.size() || 4 > data.size() - offset) {
        throw std::runtime_error("short read_u32");
    }
    return static_cast<uint32_t>(data[offset]) |
           (static_cast<uint32_t>(data[offset + 1]) << 8) |
           (static_cast<uint32_t>(data[offset + 2]) << 16) |
           (static_cast<uint32_t>(data[offset + 3]) << 24);
}

uint64_t read_u64(const std::vector<uint8_t>& data, size_t offset) {
    if (offset > data.size() || 8 > data.size() - offset) {
        throw std::runtime_error("short read_u64");
    }
    uint64_t value = 0;
    for (size_t i = 0; i < 8; ++i) {
        value |= static_cast<uint64_t>(data[offset + i]) << (i * 8);
    }
    return value;
}

uint16_t load_le16(const uint8_t* ptr) {
    return static_cast<uint16_t>(ptr[0]) |
           static_cast<uint16_t>(ptr[1] << 8);
}

uint32_t load_le32(const uint8_t* ptr) {
    return static_cast<uint32_t>(ptr[0]) |
           (static_cast<uint32_t>(ptr[1]) << 8) |
           (static_cast<uint32_t>(ptr[2]) << 16) |
           (static_cast<uint32_t>(ptr[3]) << 24);
}

void store_le16(uint8_t* ptr, uint16_t value) {
    ptr[0] = static_cast<uint8_t>(value & 0xFF);
    ptr[1] = static_cast<uint8_t>((value >> 8) & 0xFF);
}

void store_le32(uint8_t* ptr, uint32_t value) {
    ptr[0] = static_cast<uint8_t>(value & 0xFF);
    ptr[1] = static_cast<uint8_t>((value >> 8) & 0xFF);
    ptr[2] = static_cast<uint8_t>((value >> 16) & 0xFF);
    ptr[3] = static_cast<uint8_t>((value >> 24) & 0xFF);
}

void append_u32(std::vector<uint8_t>& out, uint32_t value) {
    out.push_back(static_cast<uint8_t>(value & 0xFF));
    out.push_back(static_cast<uint8_t>((value >> 8) & 0xFF));
    out.push_back(static_cast<uint8_t>((value >> 16) & 0xFF));
    out.push_back(static_cast<uint8_t>((value >> 24) & 0xFF));
}

void append_u16(std::vector<uint8_t>& out, uint16_t value) {
    out.push_back(static_cast<uint8_t>(value & 0xFF));
    out.push_back(static_cast<uint8_t>((value >> 8) & 0xFF));
}

void append_u64(std::vector<uint8_t>& out, uint64_t value) {
    for (size_t i = 0; i < 8; ++i) {
        out.push_back(static_cast<uint8_t>((value >> (i * 8)) & 0xFF));
    }
}

void patch_u32(std::vector<uint8_t>& out, size_t offset, uint32_t value) {
    if (offset > out.size() || 4 > out.size() - offset) {
        throw std::runtime_error("patch_u32 outside buffer");
    }
    out[offset] = static_cast<uint8_t>(value & 0xFF);
    out[offset + 1] = static_cast<uint8_t>((value >> 8) & 0xFF);
    out[offset + 2] = static_cast<uint8_t>((value >> 16) & 0xFF);
    out[offset + 3] = static_cast<uint8_t>((value >> 24) & 0xFF);
}

void patch_u64(std::vector<uint8_t>& out, size_t offset, uint64_t value) {
    if (offset > out.size() || 8 > out.size() - offset) {
        throw std::runtime_error("patch_u64 outside buffer");
    }
    for (size_t i = 0; i < 8; ++i) {
        out[offset + i] = static_cast<uint8_t>((value >> (i * 8)) & 0xFF);
    }
}

bool range_fits_size(const std::vector<uint8_t>& data, uint64_t offset, uint64_t length) {
    if (offset > data.size()) {
        return false;
    }
    const size_t start = static_cast<size_t>(offset);
    return length <= data.size() - start;
}

size_t checked_offset(const std::vector<uint8_t>& data, uint64_t offset, uint64_t length, const std::string& label) {
    if (!range_fits_size(data, offset, length)) {
        throw std::runtime_error(label + " outside ZIP");
    }
    return static_cast<size_t>(offset);
}

uint64_t checked_add_u64(uint64_t a, uint64_t b, const std::string& label) {
    if (a > std::numeric_limits<uint64_t>::max() - b) {
        throw std::runtime_error(label + " overflows uint64");
    }
    return a + b;
}

uint64_t checked_mul_u64(uint64_t a, uint64_t b, const std::string& label) {
    if (b != 0 && a > std::numeric_limits<uint64_t>::max() / b) {
        throw std::runtime_error(label + " overflows uint64");
    }
    return a * b;
}

size_t checked_u64_to_size(uint64_t value, const std::string& label) {
    if (value > std::numeric_limits<size_t>::max()) {
        throw std::runtime_error(label + " exceeds addressable memory");
    }
    return static_cast<size_t>(value);
}

std::string json_escape(std::string_view input) {
    std::ostringstream out;
    for (const char ch : input) {
        switch (ch) {
        case '\\':
            out << "\\\\";
            break;
        case '"':
            out << "\\\"";
            break;
        case '\n':
            out << "\\n";
            break;
        case '\r':
            out << "\\r";
            break;
        case '\t':
            out << "\\t";
            break;
        default:
            if (static_cast<unsigned char>(ch) < 0x20) {
                out << "\\u" << std::hex << std::setw(4) << std::setfill('0')
                    << static_cast<int>(static_cast<unsigned char>(ch))
                    << std::dec << std::setfill(' ');
            } else {
                out << ch;
            }
            break;
        }
    }
    return out.str();
}

std::string hex4(uint32_t value) {
    std::ostringstream out;
    out << "0x" << std::uppercase << std::hex << std::setw(4) << std::setfill('0')
        << (value & 0xFFFF);
    return out.str();
}

std::string hex8(uint32_t value) {
    std::ostringstream out;
    out << "0x" << std::uppercase << std::hex << std::setw(8) << std::setfill('0')
        << value;
    return out.str();
}

void reset_standard_stream_formatting() {
    for (std::ostream* stream : {&std::cout, &std::cerr}) {
        *stream << std::dec << std::noboolalpha << std::nouppercase << std::defaultfloat;
        stream->precision(6);
        stream->fill(' ');
    }
}

bool parse_hex_nibble(char ch, uint8_t& out);

uint32_t parse_u32_option(const std::string& text, std::string_view name) {
    if (text.empty()) {
        throw std::runtime_error(std::string(name) + " must be an unsigned decimal integer");
    }
    uint64_t value = 0;
    for (const char ch : text) {
        if (ch < '0' || ch > '9') {
            throw std::runtime_error(std::string(name) + " must be an unsigned decimal integer");
        }
        value = value * 10 + static_cast<uint32_t>(ch - '0');
        if (value > std::numeric_limits<uint32_t>::max()) {
            throw std::runtime_error(std::string(name) + " is out of range");
        }
    }
    return static_cast<uint32_t>(value);
}

uint32_t parse_u32_range_option(const std::string& text, std::string_view name, uint32_t max_value) {
    const uint32_t value = parse_u32_option(text, name);
    if (value > max_value) {
        std::ostringstream error;
        error << name << " must be 0.." << max_value;
        throw std::runtime_error(error.str());
    }
    return value;
}

std::vector<std::string> split_comma_values(std::string_view text, std::string_view name, size_t expected_count) {
    std::vector<std::string> values;
    size_t start = 0;
    while (start <= text.size()) {
        const size_t comma = text.find(',', start);
        const size_t end = comma == std::string_view::npos ? text.size() : comma;
        size_t first = start;
        size_t last = end;
        while (first < last && std::isspace(static_cast<unsigned char>(text[first]))) {
            ++first;
        }
        while (last > first && std::isspace(static_cast<unsigned char>(text[last - 1]))) {
            --last;
        }
        if (first == last) {
            throw std::runtime_error(std::string(name) + " contains an empty value");
        }
        values.emplace_back(text.substr(first, last - first));
        if (comma == std::string_view::npos) {
            break;
        }
        start = comma + 1;
    }
    if (values.size() != expected_count) {
        std::ostringstream error;
        error << name << " needs exactly " << expected_count << " comma-separated values";
        throw std::runtime_error(error.str());
    }
    return values;
}

std::array<uint16_t, 4> parse_u16_list4(std::string_view text, std::string_view name) {
    const std::vector<std::string> values = split_comma_values(text, name, 4);
    std::array<uint16_t, 4> out{};
    for (size_t i = 0; i < out.size(); ++i) {
        out[i] = static_cast<uint16_t>(parse_u32_range_option(values[i], name, 0xFFFF));
    }
    return out;
}

std::array<uint8_t, 4> parse_u8_list4(std::string_view text, std::string_view name, uint32_t max_value = 0xFF) {
    const std::vector<std::string> values = split_comma_values(text, name, 4);
    std::array<uint8_t, 4> out{};
    for (size_t i = 0; i < out.size(); ++i) {
        out[i] = static_cast<uint8_t>(parse_u32_range_option(values[i], name, max_value));
    }
    return out;
}

std::array<uint8_t, 6> parse_u8_list6(std::string_view text, std::string_view name, uint32_t max_value = 0xFF) {
    const std::vector<std::string> values = split_comma_values(text, name, 6);
    std::array<uint8_t, 6> out{};
    for (size_t i = 0; i < out.size(); ++i) {
        out[i] = static_cast<uint8_t>(parse_u32_range_option(values[i], name, max_value));
    }
    return out;
}

uint16_t parse_lane_hex_option(const std::string& text, std::string_view name) {
    std::string_view value(text);
    if (value.rfind("0x", 0) == 0 || value.rfind("0X", 0) == 0) {
        value.remove_prefix(2);
    }
    if (value.empty() || value.size() > 4) {
        throw std::runtime_error(std::string(name) + " must be a 1..4 digit hexadecimal shared PID half");
    }
    uint32_t parsed = 0;
    for (const char ch : value) {
        uint8_t nibble = 0;
        if (!parse_hex_nibble(ch, nibble)) {
            throw std::runtime_error(std::string(name) + " must be hexadecimal");
        }
        parsed = (parsed << 4) | nibble;
    }
    return static_cast<uint16_t>(parsed);
}

UnpackFormat parse_unpack_format(std::string_view text) {
    if (text == "zip") {
        return UnpackFormat::Zip;
    }
    if (text == "raw" || text == "pk3raw") {
        return UnpackFormat::Raw;
    }
    throw std::runtime_error("unknown unpack format: " + std::string(text));
}

LaneSelectMode parse_lane_select_mode(std::string_view text) {
    if (text == "all") {
        return LaneSelectMode::All;
    }
    if (text == "one") {
        return LaneSelectMode::One;
    }
    if (text == "range") {
        return LaneSelectMode::Range;
    }
    throw std::runtime_error("unknown lane selection mode: " + std::string(text));
}

Pk3CorpusState parse_pk3_corpus_state(std::string_view text) {
    if (text == "egg" || text == "eggs") {
        return Pk3CorpusState::Egg;
    }
    if (text == "hatched-shiny" || text == "shiny") {
        return Pk3CorpusState::HatchedShiny;
    }
    if (text == "hatched-not-shiny" || text == "not-shiny" || text == "non-shiny") {
        return Pk3CorpusState::HatchedNotShiny;
    }
    throw std::runtime_error("unknown PK3 state: " + std::string(text));
}

const char* unpack_format_name(UnpackFormat format) {
    switch (format) {
    case UnpackFormat::Zip:
        return "zip";
    case UnpackFormat::Raw:
        return "raw";
    }
    return "unknown";
}

const char* lane_select_mode_name(LaneSelectMode mode) {
    switch (mode) {
    case LaneSelectMode::All:
        return "all";
    case LaneSelectMode::One:
        return "one";
    case LaneSelectMode::Range:
        return "range";
    }
    return "unknown";
}

const char* pk3_corpus_state_name(Pk3CorpusState state) {
    switch (state) {
    case Pk3CorpusState::Egg:
        return "egg";
    case Pk3CorpusState::HatchedShiny:
        return "hatched-shiny";
    case Pk3CorpusState::HatchedNotShiny:
        return "hatched-not-shiny";
    }
    return "unknown";
}

const char* codec_name(CodecId id) {
    switch (id) {
    case CodecId::LegacyAuto:
        return "legacy_auto";
    case CodecId::None:
        return "none";
    case CodecId::Zlib:
        return "zlib";
    case CodecId::Zstd:
        return "zstd";
    case CodecId::Lzma2:
        return "lzma2";
    case CodecId::Rans:
        return "rans";
    }
    return "unknown";
}

CodecId parse_codec_id(std::string_view text) {
    if (text == "auto" || text == "legacy") {
        return CodecId::LegacyAuto;
    }
    if (text == "none") {
        return CodecId::None;
    }
    if (text == "zlib") {
        return CodecId::Zlib;
    }
    if (text == "zstd") {
        return CodecId::Zstd;
    }
    if (text == "lzma2") {
        return CodecId::Lzma2;
    }
    if (text == "rans" || text == "fse") {
        return CodecId::Rans;
    }
    throw std::runtime_error("unknown codec: " + std::string(text));
}

const char* codec_profile_name(CodecProfile profile) {
    switch (profile) {
    case CodecProfile::None:
        return "none";
    case CodecProfile::Compat:
        return "compat";
    case CodecProfile::Fast:
        return "fast";
    case CodecProfile::Small:
        return "small";
    }
    return "unknown";
}

CodecProfile parse_codec_profile(std::string_view text) {
    if (text == "compat") {
        return CodecProfile::Compat;
    }
    if (text == "fast") {
        return CodecProfile::Fast;
    }
    if (text == "small") {
        return CodecProfile::Small;
    }
    throw std::runtime_error("unknown codec profile: " + std::string(text));
}

CodecSpec codec_for_profile(CodecProfile profile, uint32_t level) {
    if (level == 0) {
        return {CodecId::None, 0, 0};
    }
    switch (profile) {
    case CodecProfile::None:
    case CodecProfile::Compat:
        return {CodecId::Zlib, kZlibDefaultLevel, 0};
    case CodecProfile::Fast:
        return {CodecId::Zstd, kZstdRecommendedLevel, 0};
    case CodecProfile::Small:
        return {CodecId::Lzma2, kLzma2DefaultPreset, 0};
    }
    return {CodecId::Zlib, kZlibDefaultLevel, 0};
}

CodecId codec_id_from_raw(uint32_t raw) {
    switch (raw) {
    case 0:
        return CodecId::LegacyAuto;
    case 1:
        return CodecId::None;
    case 2:
        return CodecId::Zlib;
    case 3:
        return CodecId::Zstd;
    case 4:
        return CodecId::Lzma2;
    case 5:
        return CodecId::Rans;
    default:
        throw std::runtime_error("unsupported SPC3 codec id");
    }
}

CodecSpec legacy_codec_for_level(uint32_t level) {
    if (level == 0) {
        return {CodecId::None, 0, 0};
    }
    return {CodecId::Zlib, kZlibDefaultLevel, 0};
}

void validate_codec_spec(
    const CodecSpec& codec,
    uint32_t spc3_level,
    std::string_view context,
    bool allow_rans = false)
{
    if (spc3_level > 3) {
        throw std::runtime_error("SPC3 level must be 0..3");
    }
    if (spc3_level == 0 && codec.id != CodecId::None) {
        throw std::runtime_error(std::string(context) + ": level 0 codec must be none");
    }
    if (codec.settings > 0xFF) {
        throw std::runtime_error(std::string(context) + ": codec settings byte is out of range");
    }
    switch (codec.id) {
    case CodecId::LegacyAuto:
        throw std::runtime_error(std::string(context) + ": legacy_auto is not a concrete codec");
    case CodecId::None:
        if (codec.level != 0) {
            throw std::runtime_error(std::string(context) + ": none codec level must be 0");
        }
        break;
    case CodecId::Zlib:
        if (codec.level < 1 || codec.level > 9) {
            throw std::runtime_error(std::string(context) + ": zlib level must be 1..9");
        }
        break;
    case CodecId::Zstd:
        if (codec.level < 1 || codec.level > 22) {
            throw std::runtime_error(std::string(context) + ": zstd level must be 1..22");
        }
        break;
    case CodecId::Lzma2:
        if (codec.level < 0 || codec.level > 9) {
            throw std::runtime_error(std::string(context) + ": lzma2 preset must be 0..9");
        }
        break;
    case CodecId::Rans:
        if (!allow_rans) {
            throw std::runtime_error(std::string(context) + ": rANS/FSE is reserved for experimental typed streams");
        }
        if (codec.level != 0 || codec.settings != 0) {
            throw std::runtime_error(std::string(context) + ": rANS/FSE codec level/settings must be 0");
        }
        break;
    }
}

CodecSpec resolve_pack_codec(uint32_t spc3_level, const CodecSpec& requested, bool codec_level_set = false) {
    if (spc3_level == 0) {
        if (requested.id != CodecId::LegacyAuto && requested.id != CodecId::None) {
            throw std::runtime_error("level 0 always uses raw none codec");
        }
        if (requested.id == CodecId::None && (requested.level != 0 || requested.settings != 0)) {
            throw std::runtime_error("level 0 raw none codec does not accept codec level/settings");
        }
        return {CodecId::None, 0, 0};
    }
    CodecSpec codec = requested;
    if (codec.id == CodecId::LegacyAuto) {
        codec = {CodecId::Zlib, kZlibDefaultLevel, 0};
    } else if (codec.id == CodecId::Zlib && !codec_level_set) {
        codec.level = kZlibDefaultLevel;
    } else if (codec.id == CodecId::Zstd && !codec_level_set) {
        codec.level = kZstdDefaultLevel;
    } else if (codec.id == CodecId::Lzma2 && !codec_level_set) {
        codec.level = kLzma2DefaultPreset;
    }
    validate_codec_spec(codec, spc3_level, "pack codec");
    return codec;
}

uint32_t pack_entry_codec_flags(const CodecSpec& codec) {
    if (codec.id == CodecId::LegacyAuto) {
        return 0;
    }
    if (codec.level < 0 || codec.level > 0xFF || codec.settings > 0xFF) {
        throw std::runtime_error("codec metadata does not fit SPC3 table flags");
    }
    return (static_cast<uint32_t>(codec.id) & kSpc3EntryCodecIdMask) |
           ((static_cast<uint32_t>(codec.level) << 8) & kSpc3EntryCodecLevelMask) |
           ((codec.settings << 16) & kSpc3EntryCodecSettingsMask);
}

CodecSpec codec_from_entry_flags(uint32_t flags, uint32_t spc3_level, bool allow_rans = false) {
    if (flags == 0) {
        return legacy_codec_for_level(spc3_level);
    }
    if ((flags & kSpc3EntryStreamFlagsMask) != 0) {
        throw std::runtime_error("unsupported SPC3 entry stream flags");
    }
    CodecSpec codec;
    codec.id = codec_id_from_raw(flags & kSpc3EntryCodecIdMask);
    codec.level = static_cast<int>((flags & kSpc3EntryCodecLevelMask) >> 8);
    codec.settings = (flags & kSpc3EntryCodecSettingsMask) >> 16;
    validate_codec_spec(codec, spc3_level, "SPC3 table codec", allow_rans);
    return codec;
}

std::string codec_display_name(const CodecSpec& codec) {
    std::ostringstream out;
    out << codec_name(codec.id);
    if (codec.id == CodecId::Zlib || codec.id == CodecId::Zstd || codec.id == CodecId::Lzma2) {
        out << "-" << codec.level;
    }
    return out.str();
}

size_t checked_file_size_to_size_t(std::streampos size, const fs::path& path) {
    const auto signed_size = static_cast<std::streamoff>(size);
    if (signed_size < 0) {
        throw std::runtime_error("could not size " + path.string());
    }
    const auto unsigned_size = static_cast<uintmax_t>(signed_size);
    if (unsigned_size > std::numeric_limits<size_t>::max()) {
        throw std::runtime_error("file too large to read into memory: " + path.string());
    }
    return static_cast<size_t>(unsigned_size);
}

bool parse_hex_nibble(char ch, uint8_t& out) {
    if (ch >= '0' && ch <= '9') {
        out = static_cast<uint8_t>(ch - '0');
        return true;
    }
    if (ch >= 'A' && ch <= 'F') {
        out = static_cast<uint8_t>(10 + ch - 'A');
        return true;
    }
    if (ch >= 'a' && ch <= 'f') {
        out = static_cast<uint8_t>(10 + ch - 'a');
        return true;
    }
    return false;
}

std::optional<uint32_t> parse_hex_fixed(std::string_view text, size_t begin, size_t count) {
    if (begin > text.size() || count > text.size() - begin) {
        return std::nullopt;
    }
    uint32_t value = 0;
    for (size_t i = 0; i < count; ++i) {
        uint8_t nibble = 0;
        if (!parse_hex_nibble(text[begin + i], nibble)) {
            return std::nullopt;
        }
        value = (value << 4) | nibble;
    }
    return value;
}

std::optional<uint16_t> parse_lane_zip_name(const std::string& name) {
    if (name.size() != std::string_view("0x0000.spinda80.zip").size()) {
        return std::nullopt;
    }
    if (name.rfind("0x", 0) != 0 || name.substr(6) != ".spinda80.zip") {
        return std::nullopt;
    }
    const auto lane = parse_hex_fixed(name, 2, 4);
    if (!lane) {
        return std::nullopt;
    }
    return static_cast<uint16_t>(*lane);
}

std::optional<uint32_t> parse_pk3_entry_name(const std::string& name) {
    if (name.size() != std::string_view("0x00000000.pk3").size()) {
        return std::nullopt;
    }
    if (name.rfind("0x", 0) != 0 || name.substr(10) != ".pk3") {
        return std::nullopt;
    }
    return parse_hex_fixed(name, 2, 8);
}

std::vector<uint8_t> read_file_bytes(const fs::path& path) {
    std::ifstream input(path, std::ios::binary);
    if (!input) {
        throw std::runtime_error("could not open " + path.string());
    }
    input.seekg(0, std::ios::end);
    const size_t size = checked_file_size_to_size_t(input.tellg(), path);
    input.seekg(0, std::ios::beg);
    std::vector<uint8_t> data(size);
    if (!data.empty()) {
        if (data.size() > static_cast<size_t>(std::numeric_limits<std::streamsize>::max())) {
            throw std::runtime_error("file too large for one stream read: " + path.string());
        }
        input.read(reinterpret_cast<char*>(data.data()), static_cast<std::streamsize>(data.size()));
    }
    if (!input) {
        throw std::runtime_error("could not read " + path.string());
    }
    return data;
}

void write_binary_file(const fs::path& path, const uint8_t* bytes, size_t byte_count) {
    const fs::path parent = path.parent_path();
    if (!parent.empty()) {
        fs::create_directories(parent);
    }
    std::ofstream output(path, std::ios::binary);
    if (!output) {
        throw std::runtime_error("could not open output for write: " + path.string());
    }
    if (byte_count > static_cast<size_t>(std::numeric_limits<std::streamsize>::max())) {
        throw std::runtime_error("output too large for one stream write: " + path.string());
    }
    if (byte_count > 0) {
        output.write(reinterpret_cast<const char*>(bytes), static_cast<std::streamsize>(byte_count));
    }
    if (!output) {
        throw std::runtime_error("could not write output: " + path.string());
    }
}

void write_binary_file(const fs::path& path, const std::vector<uint8_t>& bytes) {
    write_binary_file(path, bytes.data(), bytes.size());
}

void write_text_file(const fs::path& path, const std::string& text) {
    const fs::path parent = path.parent_path();
    if (!parent.empty()) {
        fs::create_directories(parent);
    }
    fs::path target = path;
    std::ofstream output(target, std::ios::binary);
    if (!output) {
        const fs::path fallback = path.parent_path() / (path.stem().string() + "_writable" + path.extension().string());
        target = fallback;
        output.open(target, std::ios::binary);
        if (!output) {
            throw std::runtime_error("could not open report for write: " + path.string());
        }
    }
    if (text.size() > static_cast<size_t>(std::numeric_limits<std::streamsize>::max())) {
        throw std::runtime_error("report too large for one stream write: " + target.string());
    }
    output.write(text.data(), static_cast<std::streamsize>(text.size()));
    if (!output) {
        throw std::runtime_error("could not write report: " + target.string());
    }
}

uint32_t crc32_bytes(const uint8_t* data, size_t size) {
    uLong crc = crc32(0L, Z_NULL, 0);
    size_t offset = 0;
    while (offset < size) {
        const size_t chunk = std::min<size_t>(size - offset, std::numeric_limits<uInt>::max());
        crc = crc32(crc, reinterpret_cast<const Bytef*>(data + offset), static_cast<uInt>(chunk));
        offset += chunk;
    }
    return static_cast<uint32_t>(crc);
}

uint32_t crc32_vector(const std::vector<uint8_t>& data) {
    return crc32_bytes(data.data(), data.size());
}

std::vector<uint8_t> build_stored_lane_zip(uint16_t lane, const std::vector<uint8_t>& payload) {
    if (payload.size() != kPayloadSize) {
        throw std::runtime_error("lane ZIP output requires one full lane payload");
    }

    std::vector<uint8_t> zip;
    std::vector<uint8_t> central;
    const uint64_t local_bytes_per_entry = 30ULL + std::string_view("0x00000000.pk3").size() + kRecordSize;
    const uint64_t central_bytes_per_entry = 46ULL + std::string_view("0x00000000.pk3").size();
    zip.reserve(checked_u64_to_size(
        checked_mul_u64(kExpectedRecords, local_bytes_per_entry, "lane ZIP local headers"),
        "lane ZIP local headers"));
    central.reserve(checked_u64_to_size(
        checked_mul_u64(kExpectedRecords, central_bytes_per_entry, "lane ZIP central directory"),
        "lane ZIP central directory"));

    for (uint32_t upper = 0; upper < kExpectedRecords; ++upper) {
        const uint32_t pid = (upper << 16) | lane;
        const std::string name = hex8(pid) + ".pk3";
        const uint16_t name_size = static_cast<uint16_t>(name.size());
        const size_t record_offset = static_cast<size_t>(upper) * kRecordSize;
        const uint8_t* record = payload.data() + record_offset;
        const uint32_t record_crc = crc32_bytes(record, kRecordSize);
        const uint64_t local_offset = zip.size();
        if (local_offset > std::numeric_limits<uint32_t>::max()) {
            throw std::runtime_error("lane ZIP local header offset exceeds ZIP32 range");
        }

        append_u32(zip, 0x04034B50);
        append_u16(zip, 20);
        append_u16(zip, 0);
        append_u16(zip, kZipMethodStore);
        append_u16(zip, 0);
        append_u16(zip, 0);
        append_u32(zip, record_crc);
        append_u32(zip, kRecordSize);
        append_u32(zip, kRecordSize);
        append_u16(zip, name_size);
        append_u16(zip, 0);
        zip.insert(zip.end(), name.begin(), name.end());
        zip.insert(zip.end(), record, record + kRecordSize);

        append_u32(central, 0x02014B50);
        append_u16(central, 45);
        append_u16(central, 20);
        append_u16(central, 0);
        append_u16(central, kZipMethodStore);
        append_u16(central, 0);
        append_u16(central, 0);
        append_u32(central, record_crc);
        append_u32(central, kRecordSize);
        append_u32(central, kRecordSize);
        append_u16(central, name_size);
        append_u16(central, 0);
        append_u16(central, 0);
        append_u16(central, 0);
        append_u16(central, 0);
        append_u32(central, 0);
        append_u32(central, static_cast<uint32_t>(local_offset));
        central.insert(central.end(), name.begin(), name.end());
    }

    const uint64_t central_offset = zip.size();
    const uint64_t central_size = central.size();
    if (central_offset > std::numeric_limits<uint32_t>::max() ||
        central_size > std::numeric_limits<uint32_t>::max()) {
        throw std::runtime_error("lane ZIP central directory exceeds ZIP32 range");
    }
    zip.insert(zip.end(), central.begin(), central.end());

    const uint64_t zip64_eocd_offset = zip.size();
    append_u32(zip, 0x06064B50);
    append_u64(zip, 44);
    append_u16(zip, 45);
    append_u16(zip, 45);
    append_u32(zip, 0);
    append_u32(zip, 0);
    append_u64(zip, kExpectedRecords);
    append_u64(zip, kExpectedRecords);
    append_u64(zip, central_size);
    append_u64(zip, central_offset);

    append_u32(zip, 0x07064B50);
    append_u32(zip, 0);
    append_u64(zip, zip64_eocd_offset);
    append_u32(zip, 1);

    append_u32(zip, 0x06054B50);
    append_u16(zip, 0);
    append_u16(zip, 0);
    append_u16(zip, 0xFFFF);
    append_u16(zip, 0xFFFF);
    append_u32(zip, static_cast<uint32_t>(central_size));
    append_u32(zip, static_cast<uint32_t>(central_offset));
    append_u16(zip, 0);
    return zip;
}

uint64_t fnv1a64_bytes(const uint8_t* data, size_t size) {
    uint64_t hash = kFnv1a64Offset;
    for (size_t i = 0; i < size; ++i) {
        hash ^= data[i];
        hash *= kFnv1a64Prime;
    }
    return hash;
}

uint64_t fnv1a64_vector(const std::vector<uint8_t>& data) {
    return fnv1a64_bytes(data.data(), data.size());
}

struct ZipCentralInfo {
    uint64_t total_entries = 0;
    uint64_t central_size = 0;
    uint64_t central_offset = 0;
    bool zip64 = false;
};

struct ZipEntryInfo {
    std::string name;
    uint32_t pid = 0;
    uint32_t crc32 = 0;
    uint16_t upper = 0;
    uint16_t lower = 0;
    uint16_t flags = 0;
    uint16_t method = 0;
    uint64_t compressed_size = 0;
    uint64_t uncompressed_size = 0;
    uint64_t local_header_offset = 0;
};

size_t find_eocd(const std::vector<uint8_t>& zip_bytes) {
    constexpr uint32_t kEocdSignature = 0x06054B50;
    constexpr size_t kEocdMinSize = 22;
    constexpr size_t kMaxZipCommentSize = 0xFFFF;
    if (zip_bytes.size() < kEocdMinSize) {
        throw std::runtime_error("ZIP too small for EOCD");
    }
    // EOCD may be followed only by the ZIP comment. Match that tail by
    // subtraction so forged comments cannot rely on additive wraparound.
    constexpr size_t kEocdSearchSpan = kEocdMinSize + kMaxZipCommentSize;
    const size_t min_pos = zip_bytes.size() > kEocdSearchSpan ? zip_bytes.size() - kEocdSearchSpan : 0;
    for (size_t pos = zip_bytes.size() - kEocdMinSize;; --pos) {
        if (read_u32(zip_bytes, pos) == kEocdSignature) {
            const uint16_t comment_len = read_u16(zip_bytes, pos + 20);
            const size_t tail_size = zip_bytes.size() - pos;
            if (tail_size >= kEocdMinSize &&
                static_cast<size_t>(comment_len) == tail_size - kEocdMinSize) {
                return pos;
            }
        }
        if (pos == min_pos) {
            break;
        }
    }
    throw std::runtime_error("ZIP EOCD not found");
}

ZipCentralInfo parse_central_info(const std::vector<uint8_t>& zip_bytes) {
    const size_t eocd = find_eocd(zip_bytes);
    const uint16_t disk_number = read_u16(zip_bytes, eocd + 4);
    const uint16_t central_disk = read_u16(zip_bytes, eocd + 6);
    const uint16_t entries_on_disk = read_u16(zip_bytes, eocd + 8);
    const uint16_t total_entries_16 = read_u16(zip_bytes, eocd + 10);
    if (disk_number != 0 || central_disk != 0) {
        throw std::runtime_error("multi-disk ZIP is not supported");
    }
    if (entries_on_disk != total_entries_16) {
        throw std::runtime_error("multi-disk ZIP entry counts are not supported");
    }

    ZipCentralInfo info;
    info.total_entries = total_entries_16;
    info.central_size = read_u32(zip_bytes, eocd + 12);
    info.central_offset = read_u32(zip_bytes, eocd + 16);

    const bool needs_zip64 =
        info.total_entries == 0xFFFF ||
        info.central_size == 0xFFFFFFFFULL ||
        info.central_offset == 0xFFFFFFFFULL;

    if (!needs_zip64) {
        return info;
    }

    // Production Phase 3 lane ZIPs have exactly 65,536 entries, so central
    // directory entry counts use ZIP64 even though each PK3 payload is tiny.
    constexpr size_t kZip64LocatorSize = 20;
    if (eocd < kZip64LocatorSize || read_u32(zip_bytes, eocd - kZip64LocatorSize) != 0x07064B50) {
        throw std::runtime_error("ZIP64 locator missing");
    }
    const size_t locator = eocd - kZip64LocatorSize;
    const uint32_t zip64_eocd_disk = read_u32(zip_bytes, locator + 4);
    const uint64_t zip64_eocd_offset = read_u64(zip_bytes, locator + 8);
    const uint32_t total_disks = read_u32(zip_bytes, locator + 16);
    if (zip64_eocd_disk != 0 || total_disks != 1) {
        throw std::runtime_error("multi-disk ZIP64 locator is not supported");
    }

    const size_t zip64_eocd = checked_offset(zip_bytes, zip64_eocd_offset, 56, "ZIP64 EOCD");
    if (read_u32(zip_bytes, zip64_eocd) != 0x06064B50) {
        throw std::runtime_error("ZIP64 EOCD signature missing");
    }
    const uint64_t zip64_record_size = read_u64(zip_bytes, zip64_eocd + 4);
    if (zip64_record_size < 44) {
        throw std::runtime_error("ZIP64 EOCD record is too small");
    }
    if (zip64_record_size > std::numeric_limits<uint64_t>::max() - 12) {
        throw std::runtime_error("ZIP64 EOCD record is too large");
    }
    const uint64_t zip64_record_total = zip64_record_size + 12;
    (void)checked_offset(zip_bytes, zip64_eocd_offset, zip64_record_total, "ZIP64 EOCD record");
    if (zip64_eocd_offset > std::numeric_limits<uint64_t>::max() - zip64_record_total ||
        zip64_eocd_offset + zip64_record_total != locator) {
        throw std::runtime_error("ZIP64 EOCD locator is not adjacent to record");
    }

    const uint32_t zip64_disk_number = read_u32(zip_bytes, zip64_eocd + 16);
    const uint32_t zip64_central_disk = read_u32(zip_bytes, zip64_eocd + 20);
    const uint64_t zip64_entries_on_disk = read_u64(zip_bytes, zip64_eocd + 24);
    info.total_entries = read_u64(zip_bytes, zip64_eocd + 32);
    if (zip64_disk_number != 0 || zip64_central_disk != 0 || zip64_entries_on_disk != info.total_entries) {
        throw std::runtime_error("multi-disk ZIP64 EOCD is not supported");
    }
    info.central_size = read_u64(zip_bytes, zip64_eocd + 40);
    info.central_offset = read_u64(zip_bytes, zip64_eocd + 48);
    info.zip64 = true;
    return info;
}

struct Zip64ExtraValues {
    std::optional<uint64_t> uncompressed_size;
    std::optional<uint64_t> compressed_size;
    std::optional<uint64_t> local_header_offset;
};

void validate_zip_extra_fields(
    const std::vector<uint8_t>& zip_bytes,
    size_t extra_begin,
    size_t extra_end,
    const std::string& label)
{
    size_t pos = extra_begin;
    while (extra_end - pos >= 4) {
        const uint16_t size = read_u16(zip_bytes, pos + 2);
        pos += 4;
        if (size > extra_end - pos) {
            throw std::runtime_error(label + " extra field is truncated");
        }
        pos += size;
    }
    if (pos != extra_end) {
        throw std::runtime_error(label + " extra field has trailing header bytes");
    }
}

Zip64ExtraValues read_zip64_extra_values(
    const std::vector<uint8_t>& zip_bytes,
    size_t extra_begin,
    size_t extra_end,
    bool need_uncompressed_size,
    bool need_compressed_size,
    bool need_local_header_offset)
{
    size_t pos = extra_begin;
    while (extra_end - pos >= 4) {
        const uint16_t tag = read_u16(zip_bytes, pos);
        const uint16_t size = read_u16(zip_bytes, pos + 2);
        pos += 4;
        if (size > extra_end - pos) {
            break;
        }
        if (tag == 0x0001) {
            Zip64ExtraValues values;
            size_t value_pos = pos;
            const size_t value_end = pos + size;
            auto read_next = [&](const char* label) -> uint64_t {
                if (value_end - value_pos < 8) {
                    throw std::runtime_error(std::string("short ZIP64 extra field for ") + label);
                }
                const uint64_t value = read_u64(zip_bytes, value_pos);
                value_pos += 8;
                return value;
            };

            // ZIP64 extra values are present only for central-directory fields
            // set to 0xFFFF/0xFFFFFFFF, and always appear in this fixed order.
            if (need_uncompressed_size) {
                values.uncompressed_size = read_next("uncompressed size");
            }
            if (need_compressed_size) {
                values.compressed_size = read_next("compressed size");
            }
            if (need_local_header_offset) {
                values.local_header_offset = read_next("local header offset");
            }
            return values;
        }
        pos += size;
    }
    throw std::runtime_error("needed ZIP64 extra value missing");
}

std::vector<ZipEntryInfo> parse_central_entries(
    const std::vector<uint8_t>& zip_bytes,
    const ZipCentralInfo& central,
    uint16_t expected_lane)
{
    if (central.total_entries > kExpectedRecords) {
        throw std::runtime_error("central directory entry count exceeds Phase 3 lane size");
    }
    const size_t central_begin =
        checked_offset(zip_bytes, central.central_offset, central.central_size, "central directory");
    const size_t central_size = checked_u64_to_size(central.central_size, "central directory size");

    std::vector<ZipEntryInfo> entries;
    entries.reserve(static_cast<size_t>(std::min<uint64_t>(central.total_entries, kExpectedRecords)));

    size_t pos = central_begin;
    const size_t end = central_begin + central_size;
    for (uint64_t index = 0; index < central.total_entries; ++index) {
        if (pos + 46 > end || read_u32(zip_bytes, pos) != 0x02014B50) {
            throw std::runtime_error("bad central directory entry");
        }

        const uint16_t flags = read_u16(zip_bytes, pos + 8);
        const uint16_t method = read_u16(zip_bytes, pos + 10);
        const uint32_t expected_crc32 = read_u32(zip_bytes, pos + 16);
        uint64_t compressed_size = read_u32(zip_bytes, pos + 20);
        uint64_t uncompressed_size = read_u32(zip_bytes, pos + 24);
        const uint16_t name_len = read_u16(zip_bytes, pos + 28);
        const uint16_t extra_len = read_u16(zip_bytes, pos + 30);
        const uint16_t comment_len = read_u16(zip_bytes, pos + 32);
        uint64_t local_header_offset = read_u32(zip_bytes, pos + 42);
        const size_t name_begin = pos + 46;
        if (name_len > end - name_begin) {
            throw std::runtime_error("central entry filename extends beyond directory");
        }
        const size_t extra_begin = name_begin + name_len;
        if (extra_len > end - extra_begin) {
            throw std::runtime_error("central entry extra extends beyond directory");
        }
        const size_t extra_end = extra_begin + extra_len;
        if (comment_len > end - extra_end) {
            throw std::runtime_error("central entry extends beyond directory");
        }
        const size_t next = extra_end + comment_len;
        validate_zip_extra_fields(zip_bytes, extra_begin, extra_end, "central entry");

        const bool need_zip64_uncompressed = uncompressed_size == 0xFFFFFFFFULL;
        const bool need_zip64_compressed = compressed_size == 0xFFFFFFFFULL;
        const bool need_zip64_offset = local_header_offset == 0xFFFFFFFFULL;
        if (need_zip64_uncompressed || need_zip64_compressed || need_zip64_offset) {
            const Zip64ExtraValues zip64 = read_zip64_extra_values(
                zip_bytes,
                extra_begin,
                extra_end,
                need_zip64_uncompressed,
                need_zip64_compressed,
                need_zip64_offset);
            if (need_zip64_uncompressed) {
                uncompressed_size = *zip64.uncompressed_size;
            }
            if (need_zip64_compressed) {
                compressed_size = *zip64.compressed_size;
            }
            if (need_zip64_offset) {
                local_header_offset = *zip64.local_header_offset;
            }
        }

        std::string name(reinterpret_cast<const char*>(zip_bytes.data() + name_begin), name_len);
        const auto pid = parse_pk3_entry_name(name);
        if (!pid) {
            throw std::runtime_error("bad PK3 entry name: " + name);
        }
        const uint16_t lower = static_cast<uint16_t>(*pid & 0xFFFF);
        if (lower != expected_lane) {
            throw std::runtime_error("entry lane mismatch: " + name);
        }
        if (uncompressed_size != kRecordSize) {
            throw std::runtime_error("bad PK3 size in " + name);
        }
        if (method != kZipMethodStore && method != kZipMethodDeflate) {
            throw std::runtime_error("unsupported ZIP method in " + name);
        }
        if ((flags & kZipFlagEncrypted) != 0 || (flags & kZipFlagStrongEncrypted) != 0) {
            throw std::runtime_error("encrypted ZIP entry is not supported: " + name);
        }
        if ((flags & kZipFlagDataDescriptor) != 0) {
            throw std::runtime_error("data descriptor ZIP entry is not supported: " + name);
        }

        ZipEntryInfo entry;
        entry.name = std::move(name);
        entry.pid = *pid;
        entry.crc32 = expected_crc32;
        entry.upper = static_cast<uint16_t>(*pid >> 16);
        entry.lower = lower;
        entry.flags = flags;
        entry.method = method;
        entry.compressed_size = compressed_size;
        entry.uncompressed_size = uncompressed_size;
        entry.local_header_offset = local_header_offset;
        entries.push_back(std::move(entry));

        pos = next;
    }

    if (pos != end) {
        throw std::runtime_error("central directory has trailing bytes");
    }
    return entries;
}

class RawDeflateInflator {
public:
    RawDeflateInflator() {
        const int init = inflateInit2(&stream_, -MAX_WBITS);
        if (init != Z_OK) {
            throw std::runtime_error("inflateInit2 failed");
        }
        initialized_ = true;
    }

    RawDeflateInflator(const RawDeflateInflator&) = delete;
    RawDeflateInflator& operator=(const RawDeflateInflator&) = delete;

    ~RawDeflateInflator() {
        if (initialized_) {
            inflateEnd(&stream_);
        }
    }

    void inflate_record(const uint8_t* src, uint64_t compressed_size, uint8_t* out, const std::string& name) {
        if (compressed_size > std::numeric_limits<uInt>::max()) {
            throw std::runtime_error("compressed PK3 too large for zlib stream in " + name);
        }

        stream_.next_in = const_cast<Bytef*>(reinterpret_cast<const Bytef*>(src));
        stream_.avail_in = static_cast<uInt>(compressed_size);
        stream_.next_out = reinterpret_cast<Bytef*>(out);
        stream_.avail_out = kRecordSize;

        const int result = inflate(&stream_, Z_FINISH);
        const uLong total_out = stream_.total_out;
        const uInt remaining_in = stream_.avail_in;
        const int reset = inflateReset(&stream_);
        if (result != Z_STREAM_END || reset != Z_OK || total_out != kRecordSize || remaining_in != 0) {
            throw std::runtime_error("inflate failed for " + name);
        }
    }

private:
    z_stream stream_{};
    bool initialized_ = false;
};

void inflate_zip_entry_to_record(
    const std::vector<uint8_t>& zip_bytes,
    const ZipEntryInfo& entry,
    RawDeflateInflator& inflator,
    uint8_t* out_record)
{
    const size_t local = checked_offset(zip_bytes, entry.local_header_offset, 30, "local header for " + entry.name);
    if (read_u32(zip_bytes, local) != 0x04034B50) {
        throw std::runtime_error("bad local header for " + entry.name);
    }
    const uint16_t local_flags = read_u16(zip_bytes, local + 6);
    const uint16_t local_method = read_u16(zip_bytes, local + 8);
    const uint32_t local_crc32 = read_u32(zip_bytes, local + 14);
    uint64_t local_compressed_size = read_u32(zip_bytes, local + 18);
    uint64_t local_uncompressed_size = read_u32(zip_bytes, local + 22);
    const uint16_t name_len = read_u16(zip_bytes, local + 26);
    const uint16_t extra_len = read_u16(zip_bytes, local + 28);

    if (local_method != entry.method) {
        throw std::runtime_error("local header method mismatch for " + entry.name);
    }
    if ((local_flags & kZipFlagEncrypted) != 0 || (local_flags & kZipFlagStrongEncrypted) != 0) {
        throw std::runtime_error("encrypted local ZIP entry is not supported: " + entry.name);
    }
    if (local_flags != entry.flags) {
        throw std::runtime_error("local header flag mismatch for " + entry.name);
    }
    if ((local_flags & kZipFlagDataDescriptor) != 0) {
        throw std::runtime_error("local data descriptor ZIP entry is not supported: " + entry.name);
    }

    const size_t local_name_begin = local + 30;
    if (local_name_begin > zip_bytes.size() ||
        name_len > zip_bytes.size() - local_name_begin ||
        extra_len > zip_bytes.size() - local_name_begin - name_len) {
        throw std::runtime_error("local header name/extra outside ZIP for " + entry.name);
    }
    if (name_len != entry.name.size() ||
        std::memcmp(zip_bytes.data() + local_name_begin, entry.name.data(), name_len) != 0) {
        throw std::runtime_error("local header name mismatch for " + entry.name);
    }

    const size_t local_extra_begin = local_name_begin + name_len;
    const size_t local_extra_end = local_extra_begin + extra_len;
    validate_zip_extra_fields(zip_bytes, local_extra_begin, local_extra_end, "local header");
    const bool need_zip64_local_uncompressed = local_uncompressed_size == 0xFFFFFFFFULL;
    const bool need_zip64_local_compressed = local_compressed_size == 0xFFFFFFFFULL;
    if (need_zip64_local_uncompressed || need_zip64_local_compressed) {
        const Zip64ExtraValues zip64 = read_zip64_extra_values(
            zip_bytes,
            local_extra_begin,
            local_extra_end,
            need_zip64_local_uncompressed,
            need_zip64_local_compressed,
            false);
        if (need_zip64_local_uncompressed) {
            local_uncompressed_size = *zip64.uncompressed_size;
        }
        if (need_zip64_local_compressed) {
            local_compressed_size = *zip64.compressed_size;
        }
    }
    if (local_crc32 != entry.crc32 ||
        local_compressed_size != entry.compressed_size ||
        local_uncompressed_size != entry.uncompressed_size) {
        throw std::runtime_error("local header metadata mismatch for " + entry.name);
    }

    const uint64_t data_offset = entry.local_header_offset + 30ULL + name_len + extra_len;
    if (data_offset > zip_bytes.size() ||
        entry.compressed_size > zip_bytes.size() - static_cast<size_t>(data_offset)) {
        throw std::runtime_error("compressed data outside ZIP for " + entry.name);
    }
    const uint8_t* src = zip_bytes.data() + static_cast<size_t>(data_offset);

    if (entry.method == kZipMethodStore) {
        if (entry.compressed_size != kRecordSize) {
            throw std::runtime_error("stored PK3 has wrong compressed size for " + entry.name);
        }
        std::memcpy(out_record, src, kRecordSize);
    } else {
        inflator.inflate_record(src, entry.compressed_size, out_record, entry.name);
    }

    uLong crc = crc32(0L, Z_NULL, 0);
    crc = crc32(crc, reinterpret_cast<const Bytef*>(out_record), kRecordSize);
    if (static_cast<uint32_t>(crc) != entry.crc32) {
        throw std::runtime_error("CRC32 mismatch for " + entry.name);
    }
}

void crypto_constant_xor(uint8_t* data, uint32_t seed) {
    // Gen 3 PK3 uses PID^OID as one constant 32-bit XOR key across the 48-byte data region.
#if SPC3_USE_X86_64_ASM
    spc3_xor48_asm(data, seed);
#else
    for (size_t offset = 0x20; offset < 0x50; offset += 4) {
        const uint32_t value = load_le32(data + offset) ^ seed;
        store_le32(data + offset, value);
    }
#endif
}

void shuffle_data(uint8_t* data, const std::array<uint8_t, 4>& sv) {
    if (sv[0] == 0 && sv[1] == 1 && sv[2] == 2 && sv[3] == 3) {
        return;
    }

#if SPC3_USE_X86_64_ASM
    spc3_shuffle48_asm(data, sv.data());
#else
    std::array<uint8_t, 48> copy{};
    std::memcpy(copy.data(), data + 0x20, copy.size());
    for (size_t block = 0; block < 4; ++block) {
        std::memcpy(data + 0x20 + block * 12, copy.data() + sv[block] * 12, 12);
    }
#endif
}

void decrypt_pk3(const uint8_t* encrypted, uint8_t* decrypted) {
    std::memcpy(decrypted, encrypted, kRecordSize);
    const uint32_t pid = load_le32(decrypted);
    const uint32_t oid = load_le32(decrypted + 4);
    crypto_constant_xor(decrypted, pid ^ oid);
    shuffle_data(decrypted, kBlockPosition[pid % 24]);
}

void encrypt_pk3(const uint8_t* decrypted, uint8_t* encrypted) {
    std::memcpy(encrypted, decrypted, kRecordSize);
    const uint32_t pid = load_le32(encrypted);
    const uint32_t oid = load_le32(encrypted + 4);
    const uint8_t inverse_selector = kBlockPositionInvertSelector[pid % 24];
    shuffle_data(encrypted, kBlockPosition[inverse_selector]);
    crypto_constant_xor(encrypted, pid ^ oid);
}

uint16_t calc_pk3_checksum(const uint8_t* decrypted) {
#if SPC3_USE_X86_64_ASM
    return spc3_checksum_asm(decrypted);
#else
    uint32_t sum = 0;
    for (size_t offset = 0x20; offset < 0x50; offset += 2) {
        sum += load_le16(decrypted + offset);
    }
    return static_cast<uint16_t>(sum & 0xFFFF);
#endif
}

constexpr uint8_t kGen3LanguageEnglish = 2;
constexpr uint8_t kGen3VersionSapphire = 1;
constexpr uint8_t kGen3VersionRuby = 2;
constexpr uint8_t kGen3VersionEmerald = 3;
constexpr uint8_t kGen3VersionFireRed = 4;
constexpr uint8_t kGen3VersionLeafGreen = 5;
constexpr uint8_t kGen3HatchLocationRse = 32;
constexpr uint8_t kGen3HatchLocationFrlg = 146;
constexpr uint8_t kGen3StringTerminator = 0xFF;
constexpr uint8_t kSpindaBaseFriendship = 120;
// Gen 3 internal species id, not National Dex #327.
constexpr uint16_t kGen3SpeciesSpinda = 308;

struct TsvTrainerEntry {
    bool present = false;
    uint16_t tsv = 0;
    uint16_t trainer_id = 0;
    uint16_t secret_id = 0;
    uint8_t gender = 0;
    uint8_t language = kGen3LanguageEnglish;
    uint8_t version = kGen3VersionFireRed;
    std::string trainer_name;
};

struct TsvTrainerIndex {
    std::array<TsvTrainerEntry, 8192> entries{};
    uint32_t count = 0;
    fs::path path;
};

struct Pk3EditOptions {
    std::optional<std::string> nickname;
    std::optional<std::string> ot_name;
    std::optional<uint16_t> held_item;
    std::optional<uint32_t> experience;
    std::optional<uint8_t> friendship;
    std::optional<uint8_t> pokerus;
    std::optional<uint8_t> met_location;
    std::optional<uint8_t> met_level;
    std::optional<uint8_t> origin_game;
    std::optional<uint8_t> ball;
    std::optional<uint8_t> ot_gender;
    std::optional<uint8_t> language;
    std::optional<uint8_t> ability_bit;
    std::optional<std::array<uint16_t, 4>> moves;
    std::optional<std::array<uint8_t, 4>> pp;
    std::optional<std::array<uint8_t, 4>> pp_ups;
    std::optional<std::array<uint8_t, 6>> evs;
    std::optional<std::array<uint8_t, 6>> ivs;
    std::optional<std::array<uint8_t, 6>> contest;

    bool any() const {
        return nickname || ot_name || held_item || experience || friendship || pokerus ||
               met_location || met_level || origin_game || ball || ot_gender || language ||
               ability_bit || moves || pp || pp_ups || evs || ivs || contest;
    }
};

void skip_json_string(std::string_view text, size_t& pos) {
    if (pos >= text.size() || text[pos] != '"') {
        throw std::runtime_error("expected JSON string");
    }
    ++pos;
    while (pos < text.size()) {
        const char ch = text[pos++];
        if (ch == '\\') {
            if (pos >= text.size()) {
                throw std::runtime_error("unterminated JSON escape");
            }
            ++pos;
            continue;
        }
        if (ch == '"') {
            return;
        }
    }
    throw std::runtime_error("unterminated JSON string");
}

size_t find_json_object_end(std::string_view text, size_t object_start) {
    if (object_start >= text.size() || text[object_start] != '{') {
        throw std::runtime_error("expected JSON object");
    }
    size_t pos = object_start;
    uint32_t depth = 0;
    while (pos < text.size()) {
        const char ch = text[pos];
        if (ch == '"') {
            skip_json_string(text, pos);
            continue;
        }
        if (ch == '{') {
            ++depth;
        } else if (ch == '}') {
            if (depth == 0) {
                throw std::runtime_error("bad JSON object depth");
            }
            --depth;
            if (depth == 0) {
                return pos;
            }
        }
        ++pos;
    }
    throw std::runtime_error("unterminated JSON object");
}

void skip_json_ws(std::string_view text, size_t& pos) {
    while (pos < text.size() &&
           (text[pos] == ' ' || text[pos] == '\n' || text[pos] == '\r' || text[pos] == '\t')) {
        ++pos;
    }
}

size_t find_json_key(std::string_view object, std::string_view key) {
    const std::string quoted = "\"" + std::string(key) + "\"";
    return object.find(quoted);
}

uint64_t json_uint_field(std::string_view object, std::string_view key, bool required, uint64_t fallback = 0) {
    const size_t key_pos = find_json_key(object, key);
    if (key_pos == std::string_view::npos) {
        if (!required) {
            return fallback;
        }
        throw std::runtime_error("trainer index entry missing numeric field: " + std::string(key));
    }
    size_t pos = object.find(':', key_pos);
    if (pos == std::string_view::npos) {
        throw std::runtime_error("trainer index numeric field missing colon: " + std::string(key));
    }
    ++pos;
    skip_json_ws(object, pos);
    if (pos >= object.size() || object[pos] < '0' || object[pos] > '9') {
        throw std::runtime_error("trainer index numeric field is not unsigned decimal: " + std::string(key));
    }
    uint64_t value = 0;
    while (pos < object.size() && object[pos] >= '0' && object[pos] <= '9') {
        const uint64_t digit = static_cast<uint64_t>(object[pos] - '0');
        if (value > (std::numeric_limits<uint64_t>::max() - digit) / 10ULL) {
            throw std::runtime_error("trainer index numeric field overflow: " + std::string(key));
        }
        value = value * 10ULL + digit;
        ++pos;
    }
    return value;
}

std::string json_string_at(std::string_view text, size_t quote_pos) {
    if (quote_pos >= text.size() || text[quote_pos] != '"') {
        throw std::runtime_error("expected JSON string value");
    }
    std::string value;
    size_t pos = quote_pos + 1;
    while (pos < text.size()) {
        const char ch = text[pos++];
        if (ch == '"') {
            return value;
        }
        if (ch != '\\') {
            value.push_back(ch);
            continue;
        }
        if (pos >= text.size()) {
            throw std::runtime_error("unterminated JSON string escape");
        }
        const char esc = text[pos++];
        switch (esc) {
        case '"':
        case '\\':
        case '/':
            value.push_back(esc);
            break;
        case 'b':
            value.push_back('\b');
            break;
        case 'f':
            value.push_back('\f');
            break;
        case 'n':
            value.push_back('\n');
            break;
        case 'r':
            value.push_back('\r');
            break;
        case 't':
            value.push_back('\t');
            break;
        default:
            throw std::runtime_error("unsupported JSON string escape in trainer index");
        }
    }
    throw std::runtime_error("unterminated JSON string value");
}

std::string json_string_field(std::string_view object, std::string_view key, bool required, std::string fallback = {}) {
    const size_t key_pos = find_json_key(object, key);
    if (key_pos == std::string_view::npos) {
        if (!required) {
            return fallback;
        }
        throw std::runtime_error("trainer index entry missing string field: " + std::string(key));
    }
    size_t pos = object.find(':', key_pos);
    if (pos == std::string_view::npos) {
        throw std::runtime_error("trainer index string field missing colon: " + std::string(key));
    }
    ++pos;
    skip_json_ws(object, pos);
    return json_string_at(object, pos);
}

uint8_t gen3_version_from_name(const std::string& name) {
    if (name == "R") {
        return kGen3VersionRuby;
    }
    if (name == "S") {
        return kGen3VersionSapphire;
    }
    if (name == "E") {
        return kGen3VersionEmerald;
    }
    if (name == "FR") {
        return kGen3VersionFireRed;
    }
    if (name == "LG") {
        return kGen3VersionLeafGreen;
    }
    return kGen3VersionFireRed;
}

uint8_t parse_gen3_version_option(const std::string& text, std::string_view name) {
    if (text == "R" || text == "r" || text == "ruby") {
        return kGen3VersionRuby;
    }
    if (text == "S" || text == "s" || text == "sapphire") {
        return kGen3VersionSapphire;
    }
    if (text == "E" || text == "e" || text == "emerald") {
        return kGen3VersionEmerald;
    }
    if (text == "FR" || text == "fr" || text == "firered") {
        return kGen3VersionFireRed;
    }
    if (text == "LG" || text == "lg" || text == "leafgreen") {
        return kGen3VersionLeafGreen;
    }
    return static_cast<uint8_t>(parse_u32_range_option(text, name, 0x0F));
}

uint8_t parse_ot_gender_option(const std::string& text, std::string_view name) {
    if (text == "0" || text == "m" || text == "M" || text == "male") {
        return 0;
    }
    if (text == "1" || text == "f" || text == "F" || text == "female") {
        return 1;
    }
    throw std::runtime_error(std::string(name) + " must be 0/male or 1/female");
}

uint8_t parse_ability_number_option(const std::string& text, std::string_view name) {
    if (text == "slot0" || text == "Slot0" || text == "ability0" || text == "Ability0") {
        return 0;
    }
    if (text == "slot1" || text == "Slot1" || text == "ability1" || text == "Ability1") {
        return 1;
    }
    const uint32_t value = parse_u32_range_option(text, name, 2);
    if (value <= 1) {
        return static_cast<uint8_t>(value);
    }
    return 1; // legacy GUI/CLI compatibility: old ability number 2 meant slot 1.
}

TsvTrainerIndex load_tsv_trainer_index(const fs::path& path) {
    TsvTrainerIndex index;
    index.path = path;
    const std::vector<uint8_t> bytes = read_file_bytes(path);
    const std::string text(reinterpret_cast<const char*>(bytes.data()), bytes.size());
    const size_t entries_key = text.find("\"entries\"");
    if (entries_key == std::string::npos) {
        throw std::runtime_error("trainer index JSON has no entries array: " + path.string());
    }
    size_t pos = text.find('[', entries_key);
    if (pos == std::string::npos) {
        throw std::runtime_error("trainer index JSON entries field is not an array: " + path.string());
    }
    ++pos;
    while (pos < text.size()) {
        skip_json_ws(text, pos);
        if (pos < text.size() && text[pos] == ']') {
            break;
        }
        if (pos < text.size() && text[pos] == ',') {
            ++pos;
            continue;
        }
        if (pos >= text.size() || text[pos] != '{') {
            throw std::runtime_error("trainer index entries array contains a non-object");
        }
        const size_t object_start = pos;
        const size_t object_end = find_json_object_end(text, object_start);
        const std::string_view object(text.data() + object_start, object_end - object_start + 1);

        const uint64_t tsv64 = json_uint_field(object, "tsv", true);
        const uint64_t tid64 = json_uint_field(object, "trainer_id", true);
        const uint64_t sid64 = json_uint_field(object, "secret_id", true);
        const uint64_t gender64 = json_uint_field(object, "gender", true);
        uint64_t language64 = json_uint_field(object, "language", false, kGen3LanguageEnglish);
        if (language64 == 0) {
            language64 = kGen3LanguageEnglish;
        }
        const std::string trainer_name = json_string_field(object, "trainer_name", false, "Togami");
        const std::string version_name = json_string_field(object, "version", false, "FR");
        const uint64_t computed_tsv64 = json_uint_field(object, "computed_tsv", false, tsv64);

        if (tsv64 >= index.entries.size() || tid64 > 0xFFFF || sid64 > 0xFFFF ||
            gender64 > 1 || language64 > 0xFF || computed_tsv64 != tsv64) {
            throw std::runtime_error("trainer index entry has out-of-range fields");
        }
        const uint16_t tsv = static_cast<uint16_t>(tsv64);
        const uint16_t tid = static_cast<uint16_t>(tid64);
        const uint16_t sid = static_cast<uint16_t>(sid64);
        if (((tid ^ sid) >> 3) != tsv) {
            throw std::runtime_error("trainer index TSV does not match TID/SID at TSV " + hex4(tsv));
        }
        if (index.entries[tsv].present) {
            throw std::runtime_error("trainer index has duplicate TSV " + hex4(tsv));
        }
        index.entries[tsv] = {
            true,
            tsv,
            tid,
            sid,
            static_cast<uint8_t>(gender64),
            static_cast<uint8_t>(language64),
            gen3_version_from_name(version_name),
            trainer_name.empty() ? std::string("Togami") : trainer_name,
        };
        ++index.count;
        pos = object_end + 1;
    }
    if (index.count == 0) {
        throw std::runtime_error("trainer index has no usable entries: " + path.string());
    }
    return index;
}

uint8_t encode_gen3_english_char(char ch) {
    if (ch == ' ') {
        return 0x00;
    }
    if (ch >= '0' && ch <= '9') {
        return static_cast<uint8_t>(0xA1 + (ch - '0'));
    }
    if (ch == '!') {
        return 0xAB;
    }
    if (ch == '?') {
        return 0xAC;
    }
    if (ch == '.') {
        return 0xAD;
    }
    if (ch == '-') {
        return 0xAE;
    }
    if (ch == '\'') {
        return 0xB4;
    }
    if (ch == ',') {
        return 0xB8;
    }
    if (ch == '/') {
        return 0xBA;
    }
    if (ch >= 'A' && ch <= 'Z') {
        return static_cast<uint8_t>(0xBB + (ch - 'A'));
    }
    if (ch >= 'a' && ch <= 'z') {
        return static_cast<uint8_t>(0xD5 + (ch - 'a'));
    }
    throw std::runtime_error(std::string("trainer index name has unsupported Gen 3 character: ") + ch);
}

void write_gen3_english_string(uint8_t* out, size_t capacity, std::string_view text) {
    std::fill(out, out + capacity, kGen3StringTerminator);
    const size_t count = std::min(capacity, text.size());
    for (size_t i = 0; i < count; ++i) {
        out[i] = encode_gen3_english_char(text[i]);
    }
}

uint32_t pokemon_shiny_value(uint32_t pid) {
    return (((pid >> 16) ^ (pid & 0xFFFFU)) >> 3) & 0x1FFFU;
}

void apply_hatched_trainer_state(uint8_t* decrypted, const TsvTrainerEntry& trainer) {
    const uint32_t oid = static_cast<uint32_t>(trainer.trainer_id) |
                         (static_cast<uint32_t>(trainer.secret_id) << 16);
    store_le32(decrypted + 0x04, oid);
    decrypted[0x12] = trainer.language == 0 ? kGen3LanguageEnglish : trainer.language;
    decrypted[0x13] = static_cast<uint8_t>(decrypted[0x13] & ~0x04U);
    write_gen3_english_string(decrypted + 0x08, 10, "SPINDA");
    write_gen3_english_string(decrypted + 0x14, 7, trainer.trainer_name);

    decrypted[0x29] = kSpindaBaseFriendship;
    decrypted[0x45] = (trainer.version == kGen3VersionFireRed || trainer.version == kGen3VersionLeafGreen)
        ? kGen3HatchLocationFrlg
        : kGen3HatchLocationRse;

    uint16_t origins = load_le16(decrypted + 0x46);
    origins = static_cast<uint16_t>(origins & ~0x007FU); // met level = 0 when hatched
    origins = static_cast<uint16_t>((origins & ~0x0780U) | ((trainer.version & 0x0F) << 7));
    origins = static_cast<uint16_t>((origins & ~0x8000U) | ((trainer.gender & 1U) << 15));
    store_le16(decrypted + 0x46, origins);

    const uint32_t iv32 = load_le32(decrypted + 0x48) & ~0x40000000U;
    store_le32(decrypted + 0x48, iv32);
}

void apply_pk3_edit_options(uint8_t* decrypted, const Pk3EditOptions& edits) {
    if (edits.nickname) {
        write_gen3_english_string(decrypted + 0x08, 10, *edits.nickname);
    }
    if (edits.ot_name) {
        write_gen3_english_string(decrypted + 0x14, 7, *edits.ot_name);
    }
    if (edits.language) {
        decrypted[0x12] = *edits.language;
    }
    if (edits.held_item) {
        store_le16(decrypted + 0x22, *edits.held_item);
    }
    if (edits.experience) {
        store_le32(decrypted + 0x24, *edits.experience);
    }
    if (edits.pp_ups) {
        uint8_t bonuses = 0;
        for (size_t i = 0; i < edits.pp_ups->size(); ++i) {
            bonuses |= static_cast<uint8_t>((*edits.pp_ups)[i] << (i * 2));
        }
        decrypted[0x28] = bonuses;
    }
    if (edits.friendship) {
        decrypted[0x29] = *edits.friendship;
    }
    if (edits.moves) {
        for (size_t i = 0; i < edits.moves->size(); ++i) {
            store_le16(decrypted + 0x2C + i * 2, (*edits.moves)[i]);
        }
    }
    if (edits.pp) {
        for (size_t i = 0; i < edits.pp->size(); ++i) {
            decrypted[0x34 + i] = (*edits.pp)[i];
        }
    }
    if (edits.evs) {
        decrypted[0x38] = (*edits.evs)[0]; // HP
        decrypted[0x39] = (*edits.evs)[1]; // Attack
        decrypted[0x3A] = (*edits.evs)[2]; // Defense
        decrypted[0x3B] = (*edits.evs)[5]; // Speed
        decrypted[0x3C] = (*edits.evs)[3]; // Sp. Attack
        decrypted[0x3D] = (*edits.evs)[4]; // Sp. Defense
    }
    if (edits.contest) {
        for (size_t i = 0; i < edits.contest->size(); ++i) {
            decrypted[0x3E + i] = (*edits.contest)[i];
        }
    }
    if (edits.pokerus) {
        decrypted[0x44] = *edits.pokerus;
    }
    if (edits.met_location) {
        decrypted[0x45] = *edits.met_location;
    }
    uint16_t origins = load_le16(decrypted + 0x46);
    if (edits.met_level) {
        origins = static_cast<uint16_t>((origins & ~0x007FU) | (*edits.met_level & 0x7FU));
    }
    if (edits.origin_game) {
        origins = static_cast<uint16_t>((origins & ~0x0780U) | ((*edits.origin_game & 0x0FU) << 7));
    }
    if (edits.ball) {
        origins = static_cast<uint16_t>((origins & ~0x7800U) | ((*edits.ball & 0x0FU) << 11));
    }
    if (edits.ot_gender) {
        origins = static_cast<uint16_t>((origins & ~0x8000U) | ((*edits.ot_gender & 1U) << 15));
    }
    store_le16(decrypted + 0x46, origins);

    uint32_t iv32 = load_le32(decrypted + 0x48);
    if (edits.ivs) {
        iv32 = (iv32 & 0xC0000000U) |
            (static_cast<uint32_t>((*edits.ivs)[0] & 31U) << 0) |
            (static_cast<uint32_t>((*edits.ivs)[1] & 31U) << 5) |
            (static_cast<uint32_t>((*edits.ivs)[2] & 31U) << 10) |
            (static_cast<uint32_t>((*edits.ivs)[5] & 31U) << 15) |
            (static_cast<uint32_t>((*edits.ivs)[3] & 31U) << 20) |
            (static_cast<uint32_t>((*edits.ivs)[4] & 31U) << 25);
    }
    if (edits.ability_bit) {
        iv32 = (iv32 & ~0x80000000U) | (static_cast<uint32_t>(*edits.ability_bit & 1U) << 31);
    }
    store_le32(decrypted + 0x48, iv32);
}

std::vector<uint8_t> transform_lane_payload_for_corpus_state(
    uint16_t lane,
    const std::vector<uint8_t>& payload,
    Pk3CorpusState state,
    const TsvTrainerIndex* trainer_index,
    const Pk3EditOptions& edits)
{
    if (payload.size() != kPayloadSize) {
        throw std::runtime_error("PK3 state transform requires one full lane payload");
    }
    const bool needs_transform = state != Pk3CorpusState::Egg || edits.any();
    if (!needs_transform) {
        return payload;
    }
    if (state != Pk3CorpusState::Egg && (!trainer_index || trainer_index->count != trainer_index->entries.size())) {
        throw std::runtime_error("hatched PK3 state requires a complete 8192-entry trainer index");
    }

    std::vector<uint8_t> transformed(payload.size());
    for (uint32_t upper = 0; upper < kExpectedRecords; ++upper) {
        std::array<uint8_t, kRecordSize> decrypted{};
        decrypt_pk3(payload.data() + static_cast<size_t>(upper) * kRecordSize, decrypted.data());
        const uint16_t stored_checksum = load_le16(decrypted.data() + 0x1C);
        const uint16_t actual_checksum = calc_pk3_checksum(decrypted.data());
        if (stored_checksum != actual_checksum) {
            throw std::runtime_error("source PK3 checksum mismatch during output transform");
        }
        const uint32_t pid = (upper << 16) | lane;
        if (load_le16(decrypted.data() + 0x20) != kGen3SpeciesSpinda) {
            throw std::runtime_error("source PK3 species is not Gen 3 internal Spinda 308 at " + hex8(pid));
        }

        if (state != Pk3CorpusState::Egg) {
            const uint32_t psv = pokemon_shiny_value(pid);
            const uint32_t tsv = state == Pk3CorpusState::HatchedShiny
                ? psv
                : ((psv + 1U) & 0x1FFFU);
            const TsvTrainerEntry& trainer = trainer_index->entries[tsv];
            if (!trainer.present) {
                throw std::runtime_error("trainer index missing TSV " + hex4(tsv));
            }
            apply_hatched_trainer_state(decrypted.data(), trainer);
        }
        apply_pk3_edit_options(decrypted.data(), edits);
        store_le16(decrypted.data() + 0x1C, calc_pk3_checksum(decrypted.data()));
        encrypt_pk3(decrypted.data(), transformed.data() + static_cast<size_t>(upper) * kRecordSize);
    }
    return transformed;
}

bool template_constant_fields_match(const uint8_t* base, const uint8_t* candidate) {
    // Only PID, checksum, and IV32 are expected to vary in the current Phase 3 model.
    // Compare the remaining constant regions as three memcmp spans instead of a branchy byte loop.
#if SPC3_USE_X86_64_ASM
    return spc3_template_match_asm(base, candidate) != 0;
#else
    return std::memcmp(base + 0x04, candidate + 0x04, 0x1C - 0x04) == 0 &&
           std::memcmp(base + 0x1E, candidate + 0x1E, 0x48 - 0x1E) == 0 &&
           std::memcmp(base + 0x4C, candidate + 0x4C, 0x50 - 0x4C) == 0;
#endif
}

size_t zlib_compress_size(const std::vector<uint8_t>& input, int level) {
    if (input.empty()) {
        return 0;
    }
    if (input.size() > std::numeric_limits<uLong>::max()) {
        throw std::runtime_error("input too large for zlib compress2");
    }
    uLongf out_size = compressBound(static_cast<uLong>(input.size()));
    std::vector<uint8_t> output(out_size);
    const int result = compress2(
        reinterpret_cast<Bytef*>(output.data()),
        &out_size,
        reinterpret_cast<const Bytef*>(input.data()),
        static_cast<uLong>(input.size()),
        level);
    if (result != Z_OK) {
        throw std::runtime_error("compress2 failed");
    }
    return static_cast<size_t>(out_size);
}

std::vector<uint8_t> zlib_compress_data(const std::vector<uint8_t>& input, int level) {
    if (input.empty()) {
        return {};
    }
    if (input.size() > std::numeric_limits<uLong>::max()) {
        throw std::runtime_error("input too large for zlib compress2");
    }
    uLongf out_size = compressBound(static_cast<uLong>(input.size()));
    std::vector<uint8_t> output(out_size);
    const int result = compress2(
        reinterpret_cast<Bytef*>(output.data()),
        &out_size,
        reinterpret_cast<const Bytef*>(input.data()),
        static_cast<uLong>(input.size()),
        level);
    if (result != Z_OK) {
        throw std::runtime_error("compress2 failed");
    }
    output.resize(static_cast<size_t>(out_size));
    return output;
}

std::vector<uint8_t> zlib_decompress_exact(
    const uint8_t* input,
    size_t input_size,
    uint64_t expected_size,
    const std::string& label)
{
    if (expected_size > std::numeric_limits<size_t>::max()) {
        throw std::runtime_error("decompressed size too large for " + label);
    }
    if (input_size > std::numeric_limits<uLong>::max() ||
        expected_size > std::numeric_limits<uLong>::max()) {
        throw std::runtime_error("zlib size limit exceeded for " + label);
    }
    std::vector<uint8_t> output(static_cast<size_t>(expected_size));
    uLongf out_size = static_cast<uLongf>(expected_size);
    const int result = uncompress(
        reinterpret_cast<Bytef*>(output.data()),
        &out_size,
        reinterpret_cast<const Bytef*>(input),
        static_cast<uLong>(input_size));
    if (result != Z_OK || out_size != expected_size) {
        throw std::runtime_error("zlib decompression failed for " + label);
    }
    return output;
}

std::vector<uint8_t> zstd_compress_data(const std::vector<uint8_t>& input, int level) {
    if (input.empty()) {
        return {};
    }
    const size_t bound = ZSTD_compressBound(input.size());
    if (ZSTD_isError(bound)) {
        throw std::runtime_error("ZSTD_compressBound failed");
    }
    std::vector<uint8_t> output(bound);
    const size_t written = ZSTD_compress(output.data(), output.size(), input.data(), input.size(), level);
    if (ZSTD_isError(written)) {
        throw std::runtime_error(std::string("zstd compression failed: ") + ZSTD_getErrorName(written));
    }
    output.resize(written);
    return output;
}

std::vector<uint8_t> zstd_decompress_exact(
    const uint8_t* input,
    size_t input_size,
    uint64_t expected_size,
    const std::string& label)
{
    if (expected_size > std::numeric_limits<size_t>::max()) {
        throw std::runtime_error("decompressed size too large for " + label);
    }
    std::vector<uint8_t> output(static_cast<size_t>(expected_size));
    const size_t written = ZSTD_decompress(output.data(), output.size(), input, input_size);
    if (ZSTD_isError(written) || written != output.size()) {
        const char* detail = ZSTD_isError(written) ? ZSTD_getErrorName(written) : "wrong decompressed size";
        throw std::runtime_error("zstd decompression failed for " + label + ": " + detail);
    }
    return output;
}

std::vector<uint8_t> lzma2_compress_data(const std::vector<uint8_t>& input, int preset) {
    if (input.empty()) {
        return {};
    }
    const size_t bound = lzma_stream_buffer_bound(input.size());
    std::vector<uint8_t> output(bound);
    size_t out_pos = 0;
    const lzma_ret ret = lzma_easy_buffer_encode(
        static_cast<uint32_t>(preset),
        LZMA_CHECK_CRC64,
        nullptr,
        input.data(),
        input.size(),
        output.data(),
        &out_pos,
        output.size());
    if (ret != LZMA_OK) {
        throw std::runtime_error("lzma2 compression failed");
    }
    output.resize(out_pos);
    return output;
}

std::vector<uint8_t> lzma2_decompress_exact(
    const uint8_t* input,
    size_t input_size,
    uint64_t expected_size,
    const std::string& label)
{
    if (expected_size > std::numeric_limits<size_t>::max()) {
        throw std::runtime_error("decompressed size too large for " + label);
    }
    uint64_t memlimit = std::numeric_limits<uint64_t>::max();
    size_t in_pos = 0;
    size_t out_pos = 0;
    std::vector<uint8_t> output(static_cast<size_t>(expected_size));
    const lzma_ret ret = lzma_stream_buffer_decode(
        &memlimit,
        0,
        nullptr,
        input,
        &in_pos,
        input_size,
        output.data(),
        &out_pos,
        output.size());
    if (ret != LZMA_OK || in_pos != input_size || out_pos != output.size()) {
        throw std::runtime_error("lzma2 decompression failed for " + label);
    }
    return output;
}

std::array<uint16_t, 256> normalize_rans_frequencies(const std::vector<uint8_t>& input) {
    constexpr uint32_t kRansScale = 1U << 12;
    std::array<uint32_t, 256> counts{};
    for (const uint8_t byte : input) {
        ++counts[byte];
    }

    std::array<uint16_t, 256> freqs{};
    uint32_t used = 0;
    uint32_t max_symbol = 0;
    for (uint32_t symbol = 0; symbol < 256; ++symbol) {
        if (counts[symbol] == 0) {
            continue;
        }
        ++used;
        if (counts[symbol] > counts[max_symbol]) {
            max_symbol = symbol;
        }
        const uint64_t scaled =
            (static_cast<uint64_t>(counts[symbol]) * kRansScale) / input.size();
        freqs[symbol] = static_cast<uint16_t>(std::max<uint64_t>(1, scaled));
    }
    if (used == 0) {
        return freqs;
    }

    uint32_t sum = 0;
    for (const uint16_t freq : freqs) {
        sum += freq;
    }
    while (sum < kRansScale) {
        ++freqs[max_symbol];
        ++sum;
    }
    while (sum > kRansScale) {
        bool reduced = false;
        for (uint32_t symbol = 0; symbol < 256 && sum > kRansScale; ++symbol) {
            if (symbol == max_symbol) {
                continue;
            }
            if (freqs[symbol] > 1) {
                --freqs[symbol];
                --sum;
                reduced = true;
            }
        }
        if (!reduced) {
            if (freqs[max_symbol] <= 1) {
                throw std::runtime_error("rANS/FSE frequency normalization failed");
            }
            --freqs[max_symbol];
            --sum;
        }
    }
    return freqs;
}

std::vector<uint8_t> rans_compress_data(const std::vector<uint8_t>& input) {
    if (input.empty()) {
        return {};
    }
    constexpr uint32_t kRansScaleBits = 12;
    constexpr uint32_t kRansScale = 1U << kRansScaleBits;
    constexpr uint32_t kRansByteL = 1U << 23;
    const std::array<uint16_t, 256> freqs = normalize_rans_frequencies(input);

    std::array<uint16_t, 257> cumulative{};
    uint32_t running = 0;
    uint16_t used = 0;
    for (uint32_t symbol = 0; symbol < 256; ++symbol) {
        cumulative[symbol] = static_cast<uint16_t>(running);
        running += freqs[symbol];
        if (freqs[symbol] != 0) {
            ++used;
        }
    }
    cumulative[256] = static_cast<uint16_t>(running);
    if (running != kRansScale || used == 0) {
        throw std::runtime_error("rANS/FSE frequency table is invalid");
    }

    std::vector<uint8_t> encoded_reversed;
    encoded_reversed.reserve(input.size() + 4);
    uint32_t state = kRansByteL;
    for (size_t pos = input.size(); pos-- > 0;) {
        const uint32_t symbol = input[pos];
        const uint32_t freq = freqs[symbol];
        const uint32_t start = cumulative[symbol];
        const uint32_t x_max = ((kRansByteL >> kRansScaleBits) << 8) * freq;
        while (state >= x_max) {
            encoded_reversed.push_back(static_cast<uint8_t>(state & 0xFFU));
            state >>= 8;
        }
        state = ((state / freq) << kRansScaleBits) + (state % freq) + start;
    }
    for (size_t i = 0; i < 4; ++i) {
        encoded_reversed.push_back(static_cast<uint8_t>(state & 0xFFU));
        state >>= 8;
    }

    std::vector<uint8_t> output;
    output.reserve(6 + used * 3ULL + encoded_reversed.size());
    output.push_back('R');
    output.push_back('A');
    output.push_back('N');
    output.push_back('S');
    append_u16(output, used);
    for (uint32_t symbol = 0; symbol < 256; ++symbol) {
        if (freqs[symbol] == 0) {
            continue;
        }
        output.push_back(static_cast<uint8_t>(symbol));
        append_u16(output, freqs[symbol]);
    }
    for (size_t i = encoded_reversed.size(); i-- > 0;) {
        output.push_back(encoded_reversed[i]);
    }
    return output;
}

std::vector<uint8_t> rans_decompress_exact(
    const uint8_t* input,
    size_t input_size,
    uint64_t expected_size,
    const std::string& label)
{
    if (expected_size > std::numeric_limits<size_t>::max()) {
        throw std::runtime_error("decompressed size too large for " + label);
    }
    if (expected_size == 0) {
        if (input_size != 0) {
            throw std::runtime_error("empty rANS/FSE stream has data for " + label);
        }
        return {};
    }
    constexpr uint32_t kRansScaleBits = 12;
    constexpr uint32_t kRansScale = 1U << kRansScaleBits;
    constexpr uint32_t kRansByteL = 1U << 23;
    if (input_size < 10 || input[0] != 'R' || input[1] != 'A' ||
        input[2] != 'N' || input[3] != 'S') {
        throw std::runtime_error("rANS/FSE stream header is invalid for " + label);
    }
    const uint32_t used = static_cast<uint32_t>(input[4]) |
        (static_cast<uint32_t>(input[5]) << 8);
    if (used == 0 || used > 256) {
        throw std::runtime_error("rANS/FSE symbol count is invalid for " + label);
    }
    const size_t table_bytes = 6ULL + static_cast<size_t>(used) * 3ULL;
    if (table_bytes + 4 > input_size) {
        throw std::runtime_error("rANS/FSE stream is truncated for " + label);
    }

    std::array<uint16_t, 256> freqs{};
    size_t pos = 6;
    uint32_t freq_sum = 0;
    for (uint32_t i = 0; i < used; ++i) {
        const uint8_t symbol = input[pos++];
        const uint16_t freq = static_cast<uint16_t>(input[pos]) |
            static_cast<uint16_t>(input[pos + 1] << 8);
        pos += 2;
        if (freq == 0 || freqs[symbol] != 0) {
            throw std::runtime_error("rANS/FSE frequency table is invalid for " + label);
        }
        freqs[symbol] = freq;
        freq_sum += freq;
    }
    if (freq_sum != kRansScale) {
        throw std::runtime_error("rANS/FSE frequency total is invalid for " + label);
    }

    std::array<uint16_t, 256> cumulative{};
    std::array<uint8_t, kRansScale> lookup{};
    uint32_t running = 0;
    for (uint32_t symbol = 0; symbol < 256; ++symbol) {
        cumulative[symbol] = static_cast<uint16_t>(running);
        for (uint32_t i = 0; i < freqs[symbol]; ++i) {
            lookup[running + i] = static_cast<uint8_t>(symbol);
        }
        running += freqs[symbol];
    }

    if (pos + 4 > input_size) {
        throw std::runtime_error("rANS/FSE payload is missing state for " + label);
    }
    uint32_t state =
        (static_cast<uint32_t>(input[pos]) << 24) |
        (static_cast<uint32_t>(input[pos + 1]) << 16) |
        (static_cast<uint32_t>(input[pos + 2]) << 8) |
        static_cast<uint32_t>(input[pos + 3]);
    pos += 4;

    std::vector<uint8_t> output(static_cast<size_t>(expected_size));
    for (size_t out_pos = 0; out_pos < output.size(); ++out_pos) {
        const uint32_t slot = state & (kRansScale - 1U);
        const uint8_t symbol = lookup[slot];
        output[out_pos] = symbol;
        state = freqs[symbol] * (state >> kRansScaleBits) + slot - cumulative[symbol];
        while (state < kRansByteL && pos < input_size) {
            state = (state << 8) | input[pos++];
        }
    }
    if (pos != input_size) {
        throw std::runtime_error("rANS/FSE stream has trailing bytes for " + label);
    }
    return output;
}

std::vector<uint8_t> codec_compress_data(const std::vector<uint8_t>& input, const CodecSpec& codec) {
    switch (codec.id) {
    case CodecId::None:
        return input;
    case CodecId::Zlib:
        return zlib_compress_data(input, codec.level);
    case CodecId::Zstd:
        return zstd_compress_data(input, codec.level);
    case CodecId::Lzma2:
        return lzma2_compress_data(input, codec.level);
    case CodecId::Rans:
        return rans_compress_data(input);
    case CodecId::LegacyAuto:
        throw std::runtime_error("legacy_auto is not a concrete stream codec");
    }
    throw std::runtime_error("unknown stream codec");
}

std::vector<uint8_t> codec_decompress_exact(
    const uint8_t* input,
    size_t input_size,
    uint64_t expected_size,
    const CodecSpec& codec,
    const std::string& label)
{
    if (expected_size == 0) {
        if (input_size != 0) {
            throw std::runtime_error("empty stream has data for " + label);
        }
        return {};
    }
    switch (codec.id) {
    case CodecId::None:
        if (input_size != expected_size) {
            throw std::runtime_error("none codec size mismatch for " + label);
        }
        return std::vector<uint8_t>(input, input + input_size);
    case CodecId::Zlib:
        return zlib_decompress_exact(input, input_size, expected_size, label);
    case CodecId::Zstd:
        return zstd_decompress_exact(input, input_size, expected_size, label);
    case CodecId::Lzma2:
        return lzma2_decompress_exact(input, input_size, expected_size, label);
    case CodecId::Rans:
        return rans_decompress_exact(input, input_size, expected_size, label);
    case CodecId::LegacyAuto:
        throw std::runtime_error("legacy_auto is not a concrete stream codec");
    }
    throw std::runtime_error("unknown stream codec");
}

size_t zlib_compress_concat_size(
    const std::vector<uint8_t>& first,
    const std::vector<uint8_t>& second,
    int level)
{
    if (first.empty() && second.empty()) {
        return 0;
    }

    struct DeflateSession {
        z_stream stream{};
        bool initialized = false;

        ~DeflateSession() {
            if (initialized) {
                deflateEnd(&stream);
            }
        }
    } session;

    const int init = deflateInit(&session.stream, level);
    if (init != Z_OK) {
        throw std::runtime_error("deflateInit failed");
    }
    session.initialized = true;

    std::array<uint8_t, 32768> output{};
    auto pump = [&](const std::vector<uint8_t>& input, bool finish) {
        size_t offset = 0;
        do {
            const size_t remaining = input.size() - offset;
            const size_t chunk = std::min<size_t>(remaining, std::numeric_limits<uInt>::max());
            const Bytef* next_in = chunk == 0 ? nullptr : reinterpret_cast<const Bytef*>(input.data() + offset);
            session.stream.next_in = const_cast<Bytef*>(next_in);
            session.stream.avail_in = static_cast<uInt>(chunk);
            const int flush = finish && offset + chunk == input.size() ? Z_FINISH : Z_NO_FLUSH;

            int result = Z_OK;
            do {
                session.stream.next_out = reinterpret_cast<Bytef*>(output.data());
                session.stream.avail_out = static_cast<uInt>(output.size());
                result = deflate(&session.stream, flush);
                if (result != Z_OK && result != Z_STREAM_END) {
                    throw std::runtime_error("deflate failed");
                }
            } while (session.stream.avail_out == 0 ||
                     session.stream.avail_in != 0 ||
                     (finish && result != Z_STREAM_END));

            if (finish && offset + chunk == input.size() && result != Z_STREAM_END) {
                throw std::runtime_error("deflate did not finish");
            }
            offset += chunk;
        } while (offset < input.size());
    };

    if (!first.empty()) {
        pump(first, false);
    }
    // The exception stream is serialized as two adjacent buffers. Streaming the
    // probe avoids allocating and copying a temporary bitmap+values vector.
    pump(second, true);
    return static_cast<size_t>(session.stream.total_out);
}

struct PredictorTable {
    bool loaded = false;
    std::array<uint32_t, kExpectedRecords> iv32{};
};

std::vector<uint8_t> serialize_predictor_raw(const PredictorTable& predictor) {
    if (!predictor.loaded) {
        throw std::runtime_error("level 3 pack requires loaded predictor table");
    }
    std::vector<uint8_t> out(kExpectedRecords * 4);
    for (uint32_t upper = 0; upper < kExpectedRecords; ++upper) {
        store_le32(out.data() + static_cast<size_t>(upper) * 4, predictor.iv32[upper]);
    }
    return out;
}

PredictorTable predictor_from_raw(const std::vector<uint8_t>& raw) {
    if (raw.size() != kExpectedRecords * 4) {
        throw std::runtime_error("embedded predictor has wrong size");
    }
    PredictorTable predictor;
    predictor.loaded = true;
    for (uint32_t upper = 0; upper < kExpectedRecords; ++upper) {
        predictor.iv32[upper] = load_le32(raw.data() + static_cast<size_t>(upper) * 4);
    }
    return predictor;
}

void skip_json_ws(const std::string& text, size_t& pos) {
    while (pos < text.size()) {
        const char ch = text[pos];
        if (ch != ' ' && ch != '\n' && ch != '\r' && ch != '\t') {
            return;
        }
        ++pos;
    }
}

PredictorTable load_predictor_table(const fs::path& path) {
    PredictorTable table;
    const std::vector<uint8_t> bytes = read_file_bytes(path);
    const std::string text(reinterpret_cast<const char*>(bytes.data()), bytes.size());
    const std::string key = "\"iv32_by_pid_second_half_hex\"";
    size_t pos = text.find(key);
    if (pos == std::string::npos) {
        throw std::runtime_error("predictor key missing");
    }
    pos += key.size();
    skip_json_ws(text, pos);
    if (pos >= text.size() || text[pos] != ':') {
        throw std::runtime_error("predictor key missing colon");
    }
    ++pos;
    skip_json_ws(text, pos);
    if (pos >= text.size() || text[pos] != '[') {
        throw std::runtime_error("predictor array missing");
    }
    ++pos;
    for (size_t i = 0; i < table.iv32.size(); ++i) {
        skip_json_ws(text, pos);
        if (pos >= text.size() || text[pos] != '"') {
            throw std::runtime_error("predictor string missing");
        }
        const size_t q1 = pos;
        const size_t q2 = text.find('"', q1 + 1);
        if (q2 == std::string::npos || q2 - q1 - 1 != 8) {
            throw std::runtime_error("predictor IV32 string has bad length");
        }
        const std::string_view token(text.data() + q1 + 1, 8);
        const auto value = parse_hex_fixed(token, 0, 8);
        if (!value) {
            throw std::runtime_error("predictor IV32 string has bad hex");
        }
        table.iv32[i] = *value;
        pos = q2 + 1;
        skip_json_ws(text, pos);
        if (i + 1 != table.iv32.size()) {
            if (pos >= text.size() || text[pos] != ',') {
                throw std::runtime_error("predictor comma missing");
            }
            ++pos;
        }
    }
    skip_json_ws(text, pos);
    // Keep this small parser bounded to the predictor array. Earlier versions
    // could keep scanning and accidentally accept unrelated strings after the
    // closing bracket of a short array.
    if (pos >= text.size() || text[pos] != ']') {
        throw std::runtime_error("predictor array has extra values or missing close");
    }
    table.loaded = true;
    return table;
}

enum class Mode {
    Audit,
    Pack,
    Unpack,
    Verify,
    Inspect,
    Consolidate,
    Bench,
};

Mode parse_mode(std::string_view text) {
    if (text == "audit") {
        return Mode::Audit;
    }
    if (text == "pack") {
        return Mode::Pack;
    }
    if (text == "unpack") {
        return Mode::Unpack;
    }
    if (text == "verify") {
        return Mode::Verify;
    }
    if (text == "inspect") {
        return Mode::Inspect;
    }
    if (text == "consolidate") {
        return Mode::Consolidate;
    }
    if (text == "bench") {
        return Mode::Bench;
    }
    throw std::runtime_error("unknown mode: " + std::string(text));
}

std::vector<uint32_t> parse_bench_limits(std::string_view text) {
    std::vector<uint32_t> limits;
    size_t start = 0;
    while (start <= text.size()) {
        const size_t comma = text.find(',', start);
        const size_t end = comma == std::string_view::npos ? text.size() : comma;
        const std::string token(text.substr(start, end - start));
        const uint32_t value = parse_u32_option(token, "--bench-limits");
        if (value == 0) {
            throw std::runtime_error("--bench-limits values must be greater than zero");
        }
        limits.push_back(value);
        if (comma == std::string_view::npos) {
            break;
        }
        start = comma + 1;
    }
    if (limits.empty()) {
        throw std::runtime_error("--bench-limits needs at least one value");
    }
    return limits;
}

std::vector<uint32_t> parse_bench_levels(std::string_view text) {
    std::vector<uint32_t> levels = parse_bench_limits(text);
    for (const uint32_t level : levels) {
        if (level == 0 || level > 3) {
            throw std::runtime_error("--bench-levels values must be 1, 2, or 3");
        }
    }
    return levels;
}

std::vector<CodecSpec> parse_bench_codecs(std::string_view text) {
    std::vector<CodecSpec> codecs;
    size_t start = 0;
    while (start <= text.size()) {
        const size_t comma = text.find(',', start);
        const size_t end = comma == std::string_view::npos ? text.size() : comma;
        const std::string token(text.substr(start, end - start));
        if (token.empty()) {
            throw std::runtime_error("--bench-codecs contains empty codec token");
        }
        const size_t dash = token.rfind('-');
        std::string codec_name_text = token;
        int level = 0;
        bool level_set = false;
        if (dash != std::string::npos) {
            codec_name_text = token.substr(0, dash);
            level = static_cast<int>(parse_u32_option(token.substr(dash + 1), "--bench-codecs"));
            level_set = true;
        }
        CodecSpec codec;
        codec.id = parse_codec_id(codec_name_text);
        codec.level = level;
        if (codec.id == CodecId::LegacyAuto || codec.id == CodecId::None || codec.id == CodecId::Rans) {
            throw std::runtime_error("--bench-codecs supports zlib, zstd, and lzma2 only");
        }
        codec = resolve_pack_codec(3, codec, level_set);
        codecs.push_back(codec);
        if (comma == std::string_view::npos) {
            break;
        }
        start = comma + 1;
    }
    if (codecs.empty()) {
        throw std::runtime_error("--bench-codecs needs at least one codec");
    }
    return codecs;
}

struct Options {
    std::string exe_name = "spc3_prototype";
    Mode mode = Mode::Audit;
    fs::path root = "Phase3SpindaBlocks";
    fs::path predictor = "Phase3SpindaBlocks/_phase3_pid_second_half_iv_reference.json";
    fs::path report = "Phase3SpindaBlocks/_spc3_prototype_report.json";
    fs::path input;
    fs::path output = "Phase3SpindaBlocks/output.spc3";
    fs::path unpack_dir = "Phase3SpindaBlocks/_spc3_unpacked_zips";
    fs::path consolidate_root = "Phase3SpindaBlocks";
    fs::path trainer_index = "TSVs/_spinda_tsv_trainer_index_tid_0x0000.json";
    UnpackFormat unpack_format = UnpackFormat::Zip;
    LaneSelectMode lane_select_mode = LaneSelectMode::All;
    Pk3CorpusState pk3_state = Pk3CorpusState::Egg;
    Pk3EditOptions pk3_edits;
    uint16_t lane_hex = 0;
    uint16_t lane_from = 0;
    uint16_t lane_to = 0;
    uint32_t limit_zips = 20;
    bool all_zips = false;
    uint32_t level = 3;
    std::vector<uint32_t> bench_limits = {1, 4, 20, 64};
    std::vector<uint32_t> bench_levels = {1, 2, 3};
    std::vector<CodecSpec> bench_codecs;
    CodecSpec codec;
    CodecProfile codec_profile = CodecProfile::None;
    bool no_predictor = false;
    bool no_entropy_probe = false;
    bool show_help = false;
    bool self_test = false;
    bool no_source_compare = false;
    bool bench_external = false;
    bool bench_native_codecs = false;
    bool bench_streaming = false;
    bool bench_typed_level3 = false;
    bool bench_gpu = false;
    bool bench_rans_fse = false;
    bool gpu_rebuild = false;
    bool typed_level3 = false;
    bool typed_exceptions_only = false;
    bool external_predictor = false;
    bool codec_level_set = false;
    bool codec_profile_set = false;
    bool bench_levels_set = false;
    bool bench_codecs_set = false;
    bool lane_hex_set = false;
    bool lane_from_set = false;
    bool lane_to_set = false;
    // Streaming-pack threading. 0 = auto (std::thread::hardware_concurrency); 1 = serial.
    int threads = 0;
};

void print_usage(const char* exe) {
    std::cout
        << "Usage: " << exe << " [options]\n"
        << "  --mode audit|pack|unpack|verify|inspect|consolidate|bench\n"
        << "  --root PATH              Phase3SpindaBlocks directory\n"
        << "  --predictor PATH         PID-second-half IV32 predictor JSON\n"
        << "  --report PATH            JSON report path\n"
        << "  --input PATH             input .spc3 path for unpack/verify\n"
        << "  --output PATH            output .spc3 path for pack\n"
        << "  --unpack-dir PATH        output directory for unpacked lane ZIPs or raw payloads\n"
        << "  --unpack-format zip|raw  unpack as lane .spinda80.zip files or .pk3raw payloads (default zip)\n"
        << "  --pk3-state egg|hatched-shiny|hatched-not-shiny  corpus state for unpack output (default egg)\n"
        << "  --trainer-index PATH     TSV trainer index JSON for hatched unpack output\n"
        << "  --set-nickname TEXT      unpack edit: Gen 3 English nickname, max 10 chars\n"
        << "  --set-ot-name TEXT       unpack edit: Gen 3 English OT name, max 7 chars\n"
        << "  --set-held-item N        unpack edit: held item id 0..65535\n"
        << "  --set-experience N       unpack edit: experience 0..4294967295\n"
        << "  --set-friendship N       unpack edit: friendship/hatch counter byte 0..255\n"
        << "  --set-pokerus N          unpack edit: Pokerus byte 0..255\n"
        << "  --set-moves A,B,C,D      unpack edit: four move ids 0..65535\n"
        << "  --set-pp A,B,C,D         unpack edit: four current PP bytes 0..255\n"
        << "  --set-pp-ups A,B,C,D     unpack edit: PP Ups for four moves, each 0..3\n"
        << "  --set-evs HP,ATK,DEF,SPA,SPD,SPE  unpack edit: EV bytes 0..255\n"
        << "  --set-ivs HP,ATK,DEF,SPA,SPD,SPE  unpack edit: IV values 0..31\n"
        << "  --set-contest COOL,BEAUTY,CUTE,SMART,TOUGH,FEEL  unpack edit: contest bytes 0..255\n"
        << "  --set-met-location N     unpack edit: met/hatch location byte 0..255\n"
        << "  --set-met-level N        unpack edit: met level 0..100\n"
        << "  --set-origin-game G      unpack edit: R,S,E,FR,LG or nibble 0..15\n"
        << "  --set-ball N             unpack edit: ball nibble 0..15\n"
        << "  --set-ot-gender G        unpack edit: 0/male or 1/female\n"
        << "  --set-language N         unpack edit: language byte 0..255\n"
        << "  --set-ability-number N   unpack edit: ability slot 0 or 1; legacy 2 maps to slot 1\n"
        << "  --lane-select all|one|range  unpack lane selection mode (default all)\n"
        << "  --lane HEX               unpack one shared PID half, e.g. 00A5 or 0x00A5\n"
        << "  --lane-from HEX          unpack range start shared PID half\n"
        << "  --lane-to HEX            unpack range end shared PID half\n"
        << "  --consolidate-root PATH  directory of existing .spc3 shards for consolidate\n"
        << "  --level N                SPC3 level 0..3 for pack (default 3)\n"
        << "  --codec NAME             auto|none|zlib|zstd|lzma2|rans (default auto)\n"
        << "  --codec-level N          pack levels 1..3: zlib 1..9, zstd 1..22, or lzma2 preset 0..9\n"
        << "  --codec-profile NAME     pack levels 1..3 shortcut: compat=zlib-9, fast=zstd-9, small=lzma2-9\n"
        << "  --typed-level3           pack level 3 as SPC3 v2 template/bitmap/XOR substreams\n"
        << "  --typed-exceptions-only  with --typed-level3, leave template raw and codec bitmap/XOR\n"
        << "  --external-predictor     do not embed level 3 predictor; require --predictor when decoding\n"
        << "  --limit-zips N|all       number of sorted lane ZIPs to use (default 20; 0/all = every found ZIP)\n"
        << "  --all-zips               use every valid 0xLLLL.spinda80.zip in --root, sparse lanes allowed\n"
        << "  --threads N              worker threads for pack and verify (default 0 = auto from CPU count; 1 = serial)\n"
        << "  --bench-limits LIST      comma list for bench samples (default 1,4,20,64)\n"
        << "  --bench-streaming        bench one lane at a time without building giant containers\n"
        << "  --bench-typed-level3     streaming bench split template/bitmap/XOR level-3 streams\n"
        << "  --bench-gpu              streaming bench CUDA typed level-3 rebuild offload\n"
        << "  --bench-rans-fse         streaming bench experimental rANS/FSE typed bitmap/XOR policy\n"
        << "  --gpu-rebuild            unpack/verify: try CUDA typed level-3 rebuild, then CPU fallback\n"
        << "  --bench-levels LIST      native codec SPC3 levels to test (default 1,2,3)\n"
        << "  --bench-codecs LIST      native codecs like zlib-9,zstd-9,lzma2-9\n"
        << "  --bench-native-codecs    compare native zlib/zstd/lzma2 SPC3 stream codecs\n"
        << "  --bench-external         run disk-backed external 7z/zstd comparisons when tools exist\n"
        << "  --no-source-compare      verify .spc3 internal hashes only\n"
        << "  --no-predictor           skip predictor/exceptions pass\n"
        << "  --no-entropy-probe       skip zlib in-memory compression probes\n"
        << "  --self-test              run internal parser and PK3 crypto tests\n"
        << "  --server                 run newline-framed worker protocol for native GUI reuse\n"
        << "  --help                   show this help\n";
}

Options parse_args(int argc, char** argv) {
    Options options;
    if (argc > 0 && argv[0]) {
        options.exe_name = argv[0];
    }
    for (int i = 1; i < argc; ++i) {
        const std::string arg = argv[i];
        auto require_value = [&](std::string_view name) -> std::string {
            if (i + 1 >= argc) {
                throw std::runtime_error(std::string(name) + " needs value");
            }
            return argv[++i];
        };
        if (arg == "--root") {
            options.root = require_value(arg);
        } else if (arg == "--mode") {
            options.mode = parse_mode(require_value(arg));
        } else if (arg == "--predictor") {
            options.predictor = require_value(arg);
        } else if (arg == "--report") {
            options.report = require_value(arg);
        } else if (arg == "--input") {
            options.input = require_value(arg);
        } else if (arg == "--output") {
            options.output = require_value(arg);
        } else if (arg == "--unpack-dir") {
            options.unpack_dir = require_value(arg);
        } else if (arg == "--unpack-format") {
            options.unpack_format = parse_unpack_format(require_value(arg));
        } else if (arg == "--pk3-state" || arg == "--corpus-state") {
            options.pk3_state = parse_pk3_corpus_state(require_value(arg));
        } else if (arg == "--trainer-index") {
            options.trainer_index = require_value(arg);
        } else if (arg == "--set-nickname") {
            options.pk3_edits.nickname = require_value(arg);
        } else if (arg == "--set-ot-name") {
            options.pk3_edits.ot_name = require_value(arg);
        } else if (arg == "--set-held-item") {
            options.pk3_edits.held_item = static_cast<uint16_t>(parse_u32_range_option(require_value(arg), arg, 0xFFFF));
        } else if (arg == "--set-experience") {
            options.pk3_edits.experience = parse_u32_option(require_value(arg), arg);
        } else if (arg == "--set-friendship") {
            options.pk3_edits.friendship = static_cast<uint8_t>(parse_u32_range_option(require_value(arg), arg, 0xFF));
        } else if (arg == "--set-pokerus") {
            options.pk3_edits.pokerus = static_cast<uint8_t>(parse_u32_range_option(require_value(arg), arg, 0xFF));
        } else if (arg == "--set-moves") {
            options.pk3_edits.moves = parse_u16_list4(require_value(arg), arg);
        } else if (arg == "--set-pp") {
            options.pk3_edits.pp = parse_u8_list4(require_value(arg), arg);
        } else if (arg == "--set-pp-ups") {
            options.pk3_edits.pp_ups = parse_u8_list4(require_value(arg), arg, 3);
        } else if (arg == "--set-evs") {
            options.pk3_edits.evs = parse_u8_list6(require_value(arg), arg);
        } else if (arg == "--set-ivs") {
            options.pk3_edits.ivs = parse_u8_list6(require_value(arg), arg, 31);
        } else if (arg == "--set-contest") {
            options.pk3_edits.contest = parse_u8_list6(require_value(arg), arg);
        } else if (arg == "--set-met-location") {
            options.pk3_edits.met_location = static_cast<uint8_t>(parse_u32_range_option(require_value(arg), arg, 0xFF));
        } else if (arg == "--set-met-level") {
            options.pk3_edits.met_level = static_cast<uint8_t>(parse_u32_range_option(require_value(arg), arg, 100));
        } else if (arg == "--set-origin-game") {
            options.pk3_edits.origin_game = parse_gen3_version_option(require_value(arg), arg);
        } else if (arg == "--set-ball") {
            options.pk3_edits.ball = static_cast<uint8_t>(parse_u32_range_option(require_value(arg), arg, 0x0F));
        } else if (arg == "--set-ot-gender") {
            options.pk3_edits.ot_gender = parse_ot_gender_option(require_value(arg), arg);
        } else if (arg == "--set-language") {
            options.pk3_edits.language = static_cast<uint8_t>(parse_u32_range_option(require_value(arg), arg, 0xFF));
        } else if (arg == "--set-ability-number") {
            options.pk3_edits.ability_bit = parse_ability_number_option(require_value(arg), arg);
        } else if (arg == "--lane-select") {
            options.lane_select_mode = parse_lane_select_mode(require_value(arg));
        } else if (arg == "--lane") {
            options.lane_hex = parse_lane_hex_option(require_value(arg), arg);
            options.lane_hex_set = true;
            options.lane_select_mode = LaneSelectMode::One;
        } else if (arg == "--lane-from") {
            options.lane_from = parse_lane_hex_option(require_value(arg), arg);
            options.lane_from_set = true;
            options.lane_select_mode = LaneSelectMode::Range;
        } else if (arg == "--lane-to") {
            options.lane_to = parse_lane_hex_option(require_value(arg), arg);
            options.lane_to_set = true;
            options.lane_select_mode = LaneSelectMode::Range;
        } else if (arg == "--consolidate-root") {
            options.consolidate_root = require_value(arg);
        } else if (arg == "--level") {
            options.level = parse_u32_option(require_value(arg), arg);
        } else if (arg == "--codec") {
            options.codec.id = parse_codec_id(require_value(arg));
        } else if (arg == "--codec-level") {
            options.codec.level = static_cast<int>(parse_u32_option(require_value(arg), arg));
            options.codec_level_set = true;
        } else if (arg == "--codec-profile") {
            options.codec_profile = parse_codec_profile(require_value(arg));
            options.codec_profile_set = true;
        } else if (arg == "--limit-zips") {
            const std::string value = require_value(arg);
            std::string lowered = value;
            std::transform(lowered.begin(), lowered.end(), lowered.begin(), [](unsigned char ch) {
                return static_cast<char>(std::tolower(ch));
            });
            if (lowered == "all") {
                options.limit_zips = 0;
                options.all_zips = true;
            } else {
                options.limit_zips = parse_u32_option(value, arg);
                options.all_zips = options.limit_zips == 0;
            }
        } else if (arg == "--all-zips") {
            options.limit_zips = 0;
            options.all_zips = true;
        } else if (arg == "--threads") {
            const std::string value = require_value(arg);
            const long parsed = std::stol(value);
            if (parsed < 0 || parsed > 1024) {
                throw std::runtime_error("--threads must be in [0, 1024]");
            }
            options.threads = static_cast<int>(parsed);
        } else if (arg == "--bench-limits") {
            options.bench_limits = parse_bench_limits(require_value(arg));
        } else if (arg == "--bench-streaming") {
            options.bench_streaming = true;
        } else if (arg == "--bench-typed-level3") {
            options.bench_typed_level3 = true;
        } else if (arg == "--bench-gpu") {
            options.bench_gpu = true;
            options.bench_streaming = true;
        } else if (arg == "--bench-rans-fse") {
            options.bench_rans_fse = true;
            options.bench_typed_level3 = true;
            options.bench_streaming = true;
        } else if (arg == "--gpu-rebuild" || arg == "--gpu") {
            options.gpu_rebuild = true;
        } else if (arg == "--typed-level3") {
            options.typed_level3 = true;
        } else if (arg == "--typed-exceptions-only") {
            options.typed_exceptions_only = true;
        } else if (arg == "--bench-levels") {
            options.bench_levels = parse_bench_levels(require_value(arg));
            options.bench_levels_set = true;
            options.bench_native_codecs = true;
        } else if (arg == "--bench-codecs") {
            options.bench_codecs = parse_bench_codecs(require_value(arg));
            options.bench_codecs_set = true;
            options.bench_native_codecs = true;
        } else if (arg == "--bench-native-codecs") {
            options.bench_native_codecs = true;
        } else if (arg == "--bench-external") {
            options.bench_external = true;
        } else if (arg == "--external-predictor") {
            options.external_predictor = true;
        } else if (arg == "--no-source-compare") {
            options.no_source_compare = true;
        } else if (arg == "--no-predictor") {
            options.no_predictor = true;
        } else if (arg == "--no-entropy-probe") {
            options.no_entropy_probe = true;
        } else if (arg == "--self-test") {
            options.self_test = true;
        } else if (arg == "--help" || arg == "-h") {
            options.show_help = true;
            return options;
        } else {
            throw std::runtime_error("unknown argument: " + arg);
        }
    }
    if (options.level > 3) {
        throw std::runtime_error("--level must be 0, 1, 2, or 3");
    }
    const bool bench_option_used =
        options.bench_streaming ||
        options.bench_typed_level3 ||
        options.bench_gpu ||
        options.bench_rans_fse ||
        options.bench_external ||
        options.bench_native_codecs ||
        options.bench_levels_set ||
        options.bench_codecs_set;
    if (bench_option_used && options.mode != Mode::Bench) {
        throw std::runtime_error("--bench options only apply to --mode bench");
    }
    if ((options.codec.id != CodecId::LegacyAuto || options.codec_level_set) && options.mode != Mode::Pack) {
        throw std::runtime_error("--codec and --codec-level only apply to --mode pack");
    }
    if (options.external_predictor && (options.mode != Mode::Pack || options.level != 3)) {
        throw std::runtime_error("--external-predictor only applies to --mode pack --level 3");
    }
    if (options.no_source_compare && options.mode != Mode::Verify) {
        throw std::runtime_error("--no-source-compare only applies to --mode verify");
    }
    if (options.typed_level3 && (options.mode != Mode::Pack || options.level != 3)) {
        throw std::runtime_error("--typed-level3 only applies to --mode pack --level 3");
    }
    if (options.typed_exceptions_only && !options.typed_level3) {
        throw std::runtime_error("--typed-exceptions-only requires --typed-level3");
    }
    if (options.gpu_rebuild && options.mode != Mode::Unpack && options.mode != Mode::Verify) {
        throw std::runtime_error("--gpu-rebuild only applies to --mode unpack or --mode verify");
    }
    if (options.pk3_state != Pk3CorpusState::Egg && options.mode != Mode::Unpack) {
        throw std::runtime_error("--pk3-state only applies to --mode unpack");
    }
    if (options.pk3_edits.any() && options.mode != Mode::Unpack) {
        throw std::runtime_error("--set-* PK3 edit options only apply to --mode unpack");
    }
    if (options.pk3_edits.nickname && options.pk3_edits.nickname->size() > 10) {
        throw std::runtime_error("--set-nickname accepts at most 10 Gen 3 English characters");
    }
    if (options.pk3_edits.ot_name && options.pk3_edits.ot_name->size() > 7) {
        throw std::runtime_error("--set-ot-name accepts at most 7 Gen 3 English characters");
    }
    if (options.trainer_index != fs::path("TSVs/_spinda_tsv_trainer_index_tid_0x0000.json") &&
        options.mode != Mode::Unpack) {
        throw std::runtime_error("--trainer-index only applies to --mode unpack");
    }
    if (options.codec_profile_set && options.mode != Mode::Pack) {
        throw std::runtime_error("--codec-profile only applies to --mode pack");
    }
    if (options.codec_profile_set && options.level == 0) {
        throw std::runtime_error("--codec-profile only applies to pack levels 1..3; level 0 is raw");
    }
    if (options.codec_level_set && options.level == 0) {
        throw std::runtime_error("--codec-level only applies to pack levels 1..3; level 0 is raw");
    }
    if (options.mode != Mode::Consolidate &&
        options.consolidate_root != fs::path("Phase3SpindaBlocks")) {
        throw std::runtime_error("--consolidate-root only applies to --mode consolidate");
    }
    if (options.codec_profile_set &&
        (options.codec.id != CodecId::LegacyAuto || options.codec_level_set)) {
        throw std::runtime_error("--codec-profile cannot combine with --codec or --codec-level");
    }
    if (options.codec_level_set && options.codec.id == CodecId::LegacyAuto) {
        throw std::runtime_error("--codec-level requires --codec");
    }
    if (options.mode == Mode::Pack && !(options.typed_level3 && options.codec.id == CodecId::Rans)) {
        const CodecSpec requested = options.codec_profile_set
            ? codec_for_profile(options.codec_profile, options.level)
            : options.codec;
        (void)resolve_pack_codec(options.level, requested, options.codec_profile_set || options.codec_level_set);
    }
    if (options.bench_streaming && options.bench_external) {
        throw std::runtime_error("--bench-streaming cannot combine with --bench-external");
    }
    if (options.bench_typed_level3 && !options.bench_streaming) {
        throw std::runtime_error("--bench-typed-level3 requires --bench-streaming");
    }
    if (options.bench_typed_level3 && options.no_predictor) {
        throw std::runtime_error("--bench-typed-level3 requires predictor data");
    }
    if (options.bench_gpu && options.no_predictor) {
        throw std::runtime_error("--bench-gpu requires predictor data");
    }
    return options;
}

CodecSpec requested_pack_codec(const Options& options) {
    if (options.codec_profile_set) {
        return codec_for_profile(options.codec_profile, options.level);
    }
    return options.codec;
}

bool requested_pack_codec_level_set(const Options& options) {
    return options.codec_profile_set || options.codec_level_set;
}

struct LanePath {
    uint16_t lane = 0;
    fs::path path;
};

std::vector<LanePath> find_lane_zips(const fs::path& root, uint32_t limit) {
    if (!fs::is_directory(root)) {
        throw std::runtime_error("root is not directory: " + root.string());
    }
    std::vector<LanePath> lanes;
    for (const auto& item : fs::directory_iterator(root)) {
        if (!item.is_regular_file()) {
            continue;
        }
        const std::string name = item.path().filename().string();
        const auto lane = parse_lane_zip_name(name);
        if (!lane) {
            continue;
        }
        lanes.push_back({*lane, item.path()});
    }
    std::sort(lanes.begin(), lanes.end(), [](const LanePath& a, const LanePath& b) {
        return a.lane < b.lane;
    });
    if (limit != 0 && lanes.size() > limit) {
        lanes.resize(limit);
    }
    return lanes;
}

void self_test_expect(bool condition, const std::string& label) {
    if (!condition) {
        throw std::runtime_error("self-test failed: " + label);
    }
}

uint64_t expand_iv32_exceptions(
    uint32_t* out,
    const PredictorTable& predictor,
    const uint8_t* bitmap,
    size_t bitmap_size,
    const uint8_t* values,
    size_t values_size);

void run_self_tests() {
    self_test_expect(parse_lane_zip_name("0x0001.spinda80.zip").value_or(0) == 1, "lane name parse");
    self_test_expect(!parse_lane_zip_name("0x0001.bad.zip").has_value(), "lane name reject");
    self_test_expect(parse_pk3_entry_name("0x1234ABCD.pk3").value_or(0) == 0x1234ABCDU, "entry name parse");
    self_test_expect(!parse_pk3_entry_name("1234ABCD.pk3").has_value(), "entry name reject");
    self_test_expect(kGen3SpeciesSpinda == 308, "Gen 3 internal Spinda species id");
    self_test_expect(parse_ability_number_option("0", "--set-ability-number") == 0, "ability slot 0 parse");
    self_test_expect(parse_ability_number_option("1", "--set-ability-number") == 1, "ability slot 1 parse");
    self_test_expect(parse_ability_number_option("2", "--set-ability-number") == 1, "legacy ability number 2 parse");
    {
        std::array<uint8_t, 10> nickname{};
        write_gen3_english_string(nickname.data(), nickname.size(), "SPINDA");
        self_test_expect(nickname[0] == encode_gen3_english_char('S') &&
                         nickname[5] == encode_gen3_english_char('A'),
                         "Gen 3 nickname encode");
        self_test_expect(std::all_of(nickname.begin() + 6, nickname.end(), [](uint8_t value) {
            return value == kGen3StringTerminator;
        }), "Gen 3 nickname trash bytes");
    }

    for (uint32_t selector = 0; selector < 24; ++selector) {
        std::array<uint8_t, kRecordSize> decrypted{};
        std::array<uint8_t, kRecordSize> encrypted{};
        std::array<uint8_t, kRecordSize> roundtrip{};

        const uint32_t pid = selector;
        store_le32(decrypted.data(), pid);
        store_le32(decrypted.data() + 4, 0x12345678U ^ selector);
        for (size_t offset = 0x20; offset < 0x50; ++offset) {
            decrypted[offset] = static_cast<uint8_t>((offset * 17 + selector * 29) & 0xFF);
        }
        store_le32(decrypted.data() + 0x48, 0xA5A50000U | selector);
        store_le16(decrypted.data() + 0x1C, calc_pk3_checksum(decrypted.data()));

        encrypt_pk3(decrypted.data(), encrypted.data());
        decrypt_pk3(encrypted.data(), roundtrip.data());
        self_test_expect(std::memcmp(decrypted.data(), roundtrip.data(), kRecordSize) == 0,
                         "PK3 encrypt/decrypt selector " + std::to_string(selector));
        self_test_expect(calc_pk3_checksum(roundtrip.data()) == load_le16(roundtrip.data() + 0x1C),
                         "PK3 checksum selector " + std::to_string(selector));
    }

    const std::vector<uint8_t> compress_probe = {0, 1, 1, 2, 3, 5, 8, 13, 21};
    self_test_expect(zlib_compress_size(compress_probe, 1) > 0, "zlib compress probe");
    for (const CodecSpec codec : std::array<CodecSpec, 5>{{
             {CodecId::None, 0, 0},
             {CodecId::Zlib, kZlibDefaultLevel, 0},
             {CodecId::Zstd, kZstdDefaultLevel, 0},
             {CodecId::Lzma2, 1, 0},
             {CodecId::Rans, 0, 0},
         }}) {
        const std::vector<uint8_t> encoded = codec_compress_data(compress_probe, codec);
        const std::vector<uint8_t> decoded = codec_decompress_exact(
            encoded.data(),
            encoded.size(),
            compress_probe.size(),
            codec,
            std::string("self-test ") + codec_name(codec.id));
        self_test_expect(decoded == compress_probe, std::string("codec roundtrip ") + codec_name(codec.id));
    }
    PredictorTable predictor;
    predictor.loaded = true;
    std::vector<uint8_t> bitmap(kExpectedRecords / 8, 0);
    std::vector<uint8_t> values;
    for (uint32_t upper = 0; upper < kExpectedRecords; ++upper) {
        predictor.iv32[upper] = 0xA5000000U ^ upper;
        if ((upper % 4096) == 0) {
            bitmap[upper / 8] |= static_cast<uint8_t>(1U << (upper % 8));
            const uint32_t xored = 0x01020304U ^ upper;
            const size_t old_size = values.size();
            values.resize(old_size + 4);
            store_le32(values.data() + old_size, xored);
        }
    }
    std::vector<uint32_t> expanded(kExpectedRecords);
    const uint64_t expanded_values = expand_iv32_exceptions(
        expanded.data(),
        predictor,
        bitmap.data(),
        bitmap.size(),
        values.data(),
        values.size());
    self_test_expect(expanded_values == values.size() / 4, "ASM IV32 exception value count");
    for (uint32_t upper = 0; upper < kExpectedRecords; ++upper) {
        uint32_t expected = predictor.iv32[upper];
        if ((upper % 4096) == 0) {
            expected ^= (0x01020304U ^ upper);
        }
        self_test_expect(expanded[upper] == expected, "ASM IV32 exception expand");
    }
    self_test_expect(pack_entry_codec_flags({CodecId::Zstd, 19, 0}) == 0x00001303U, "codec flag pack");
    self_test_expect(codec_from_entry_flags(0, 3).id == CodecId::Zlib, "legacy codec flag decode");
}

struct LaneMetrics {
    uint16_t lane = 0;
    std::string zip_path;
    uint64_t zip_size_bytes = 0;
    uint64_t entry_count = 0;
    bool zip64 = false;
    uint64_t deflate_entries = 0;
    uint64_t stored_entries = 0;

    uint64_t checksum_failures = 0;
    uint64_t duplicate_entries = 0;
    uint64_t missing_entries = 0;
    uint64_t content_pid_mismatches = 0;
    uint64_t species_mismatches = 0;
    uint64_t template_mismatches = 0;
    uint64_t predictor_exceptions = 0;
    uint64_t predictor_matches = 0;
    uint64_t rebuild_mismatches = 0;
    uint64_t predictor_roundtrip_mismatches = 0;

    uint64_t raw_payload_bytes = 0;
    uint64_t iv32_stream_bytes = 0;
    uint64_t exception_bitmap_bytes = 0;
    uint64_t exception_value_bytes = 0;
    uint64_t predictor_exception_raw_bytes = 0;
    uint64_t iv32_stream_zlib1_bytes = 0;
    uint64_t iv32_stream_zlib9_bytes = 0;
    uint64_t exception_stream_zlib1_bytes = 0;
    uint64_t exception_stream_zlib9_bytes = 0;

    double read_zip_ms = 0;
    double parse_zip_ms = 0;
    double inflate_ms = 0;
    double decrypt_model_ms = 0;
    double rebuild_ms = 0;
    double entropy_probe_ms = 0;
    double total_ms = 0;

    std::vector<std::string> errors;
};

void add_error(LaneMetrics& metrics, const std::string& error) {
    if (metrics.errors.size() < 12) {
        metrics.errors.push_back(error);
    }
}

struct LaneModel {
    LaneMetrics metrics;
    std::vector<uint8_t> encrypted;
    std::array<uint8_t, kRecordSize> base_template{};
    std::vector<uint8_t> iv32_stream;
    std::vector<uint8_t> exception_bitmap;
    std::vector<uint8_t> exception_values;
    uint32_t zip_crc32 = 0;
    uint64_t zip_fnv64 = 0;
    uint32_t encrypted_crc32 = 0;
    uint64_t encrypted_fnv64 = 0;
};

struct TypedLevel3Policy {
    std::string id;
    CodecSpec template_codec;
    CodecSpec bitmap_codec;
    CodecSpec values_codec;
};

struct Spc3TypedSubstreamEntry {
    uint32_t kind = 0;
    uint32_t flags = 0;
    uint64_t offset = 0;
    uint64_t stream_size = 0;
    uint64_t raw_size = 0;
};

struct Spc3TableEntry {
    uint32_t lane = 0;
    uint32_t level = 0;
    uint32_t stream_kind = 0;
    uint32_t flags = 0;
    uint64_t source_zip_size = 0;
    uint64_t source_zip_crc32 = 0;
    uint64_t source_zip_fnv64 = 0;
    uint64_t original_payload_crc32 = 0;
    uint64_t rebuilt_payload_crc32 = 0;
    uint64_t stream_offset = 0;
    uint64_t stream_size = 0;
    uint64_t uncompressed_model_size = 0;
    uint64_t predictor_matches = 0;
    uint64_t predictor_exceptions = 0;
    bool typed_level3 = false;
    std::array<Spc3TypedSubstreamEntry, kSpc3TypedLevel3SubstreamCount> typed_substreams{};
};

struct Spc3Container {
    uint32_t version = 0;
    uint32_t level = 0;
    uint32_t flags = 0;
    uint64_t predictor_offset = 0;
    uint64_t predictor_size = 0;
    uint64_t table_offset = 0;
    uint64_t table_entry_size = 0;
    uint64_t data_offset = 0;
    uint64_t data_size = 0;
    std::vector<Spc3TableEntry> entries;
    PredictorTable predictor;
};

bool entry_is_typed_level3(const Spc3TableEntry& entry) {
    return entry.level == 3 && entry.stream_kind == kSpc3StreamKindTypedLevel3;
}

const char* stream_kind_name(const Spc3TableEntry& entry) {
    if (entry_is_typed_level3(entry)) {
        return "typed_level3";
    }
    switch (entry.stream_kind) {
    case 0:
        return "level0_raw";
    case 1:
        return "level1_decrypted";
    case 2:
        return "level2_template_iv32";
    case 3:
        return "level3_template_exceptions_fused";
    default:
        return "unknown";
    }
}

const char* typed_substream_name(uint32_t kind) {
    switch (kind) {
    case kSpc3TypedSubstreamTemplate:
        return "template";
    case kSpc3TypedSubstreamBitmap:
        return "exception_bitmap";
    case kSpc3TypedSubstreamValues:
        return "xor_values";
    default:
        return "unknown";
    }
}

const Spc3TypedSubstreamEntry& typed_substream_by_kind(
    const Spc3TableEntry& entry,
    uint32_t kind)
{
    for (const Spc3TypedSubstreamEntry& substream : entry.typed_substreams) {
        if (substream.kind == kind) {
            return substream;
        }
    }
    throw std::runtime_error("typed level 3 substream missing");
}

void validate_unpack_lane_selection(const Options& options) {
    if (options.lane_select_mode == LaneSelectMode::One && !options.lane_hex_set) {
        throw std::runtime_error("--lane-select one requires --lane HEX");
    }
    if (options.lane_select_mode == LaneSelectMode::Range) {
        if (!options.lane_from_set || !options.lane_to_set) {
            throw std::runtime_error("--lane-select range requires --lane-from HEX and --lane-to HEX");
        }
        if (options.lane_from > options.lane_to) {
            throw std::runtime_error("--lane-from must be less than or equal to --lane-to");
        }
    }
}

bool unpack_lane_selected(const Options& options, uint32_t lane) {
    switch (options.lane_select_mode) {
    case LaneSelectMode::All:
        return true;
    case LaneSelectMode::One:
        return lane == options.lane_hex;
    case LaneSelectMode::Range:
        return lane >= options.lane_from && lane <= options.lane_to;
    }
    return false;
}

Spc3Container select_unpack_lanes(const Options& options, const Spc3Container& container) {
    validate_unpack_lane_selection(options);
    Spc3Container selected = container;
    selected.entries.clear();
    selected.entries.reserve(container.entries.size());
    for (const Spc3TableEntry& entry : container.entries) {
        if (unpack_lane_selected(options, entry.lane)) {
            selected.entries.push_back(entry);
        }
    }
    if (selected.entries.empty()) {
        throw std::runtime_error("unpack lane selection matched no lanes");
    }
    return selected;
}

LaneModel build_lane_model(
    const LanePath& lane_path,
    const PredictorTable* predictor,
    bool entropy_probe)
{
    LaneModel model;
    LaneMetrics& metrics = model.metrics;
    metrics.lane = lane_path.lane;
    metrics.zip_path = lane_path.path.string();
    Stopwatch total_watch;

    try {
        std::vector<uint8_t> zip_bytes;
        {
            ScopedTimer timer(metrics.read_zip_ms);
            zip_bytes = read_file_bytes(lane_path.path);
        }
        metrics.zip_size_bytes = zip_bytes.size();
        model.zip_crc32 = crc32_vector(zip_bytes);
        model.zip_fnv64 = fnv1a64_vector(zip_bytes);

        ZipCentralInfo central;
        std::vector<ZipEntryInfo> entries;
        {
            ScopedTimer timer(metrics.parse_zip_ms);
            central = parse_central_info(zip_bytes);
            entries = parse_central_entries(zip_bytes, central, lane_path.lane);
            metrics.entry_count = entries.size();
            metrics.raw_payload_bytes = metrics.entry_count * kRecordSize;
            metrics.zip64 = central.zip64;
            for (const auto& entry : entries) {
                if (entry.method == kZipMethodDeflate) {
                    ++metrics.deflate_entries;
                } else if (entry.method == kZipMethodStore) {
                    ++metrics.stored_entries;
                }
            }
        }

        model.encrypted.assign(kPayloadSize, 0);
        std::array<uint8_t, kExpectedRecords> seen{};
        {
            ScopedTimer timer(metrics.inflate_ms);
            RawDeflateInflator inflator;
            for (const auto& entry : entries) {
                if (seen[entry.upper]) {
                    ++metrics.duplicate_entries;
                    add_error(metrics, "duplicate upper " + hex4(entry.upper));
                    continue;
                }
                seen[entry.upper] = 1;
                uint8_t* record = model.encrypted.data() + static_cast<size_t>(entry.upper) * kRecordSize;
                inflate_zip_entry_to_record(zip_bytes, entry, inflator, record);
                const uint32_t content_pid = load_le32(record);
                if (content_pid != entry.pid) {
                    ++metrics.content_pid_mismatches;
                    add_error(metrics, "content PID mismatch in " + entry.name);
                }
            }
        }

        for (size_t i = 0; i < seen.size(); ++i) {
            if (!seen[i]) {
                ++metrics.missing_entries;
                if (metrics.errors.size() < 12) {
                    add_error(metrics, "missing upper " + hex4(static_cast<uint32_t>(i)));
                }
            }
        }
        if (metrics.entry_count != kExpectedRecords) {
            add_error(metrics, "entry count is " + std::to_string(metrics.entry_count));
        }
        if (metrics.missing_entries || metrics.duplicate_entries || metrics.content_pid_mismatches ||
            metrics.entry_count != kExpectedRecords) {
            add_error(metrics, "structural validation failed; skipped decrypt/model/rebuild pass");
            metrics.total_ms = total_watch.elapsed_ms();
            return model;
        }

        const bool predictor_enabled = predictor != nullptr && predictor->loaded;
        metrics.raw_payload_bytes = kPayloadSize;
        metrics.iv32_stream_bytes = kExpectedRecords * 4;
        if (predictor_enabled) {
            metrics.exception_bitmap_bytes = kExpectedRecords / 8;
        }

        model.iv32_stream.assign(kExpectedRecords * 4, 0);
        model.exception_bitmap.assign(predictor_enabled ? kExpectedRecords / 8 : 0, 0);
        if (predictor_enabled) {
            model.exception_values.reserve(kExpectedRecords * 4);
        }

        {
            ScopedTimer timer(metrics.decrypt_model_ms);
            std::array<uint8_t, kRecordSize> decrypted_record{};

            for (uint32_t upper = 0; upper < kExpectedRecords; ++upper) {
                const uint8_t* src = model.encrypted.data() + static_cast<size_t>(upper) * kRecordSize;
                uint8_t* dst = decrypted_record.data();
                decrypt_pk3(src, dst);
                if (upper == 0) {
                    model.base_template = decrypted_record;
                }

                const uint32_t expected_pid = (upper << 16) | lane_path.lane;
                if (load_le32(dst) != expected_pid) {
                    ++metrics.content_pid_mismatches;
                }

                const uint16_t stored_checksum = load_le16(dst + 0x1C);
                const uint16_t actual_checksum = calc_pk3_checksum(dst);
                if (stored_checksum != actual_checksum) {
                    ++metrics.checksum_failures;
                    add_error(metrics, "checksum mismatch at " + hex8(expected_pid));
                }
                if (load_le16(dst + 0x20) != kGen3SpeciesSpinda) {
                    ++metrics.species_mismatches;
                    add_error(metrics, "species is not Gen 3 internal Spinda 308 at " + hex8(expected_pid));
                }

                const uint32_t iv32 = load_le32(dst + 0x48);
                store_le32(model.iv32_stream.data() + static_cast<size_t>(upper) * 4, iv32);

                if (upper > 0 && !template_constant_fields_match(model.base_template.data(), dst)) {
                    ++metrics.template_mismatches;
                }

                if (predictor_enabled) {
                    const uint32_t predicted = predictor->iv32[upper];
                    if (predicted == iv32) {
                        ++metrics.predictor_matches;
                    } else {
                        ++metrics.predictor_exceptions;
                        model.exception_bitmap[upper / 8] |= static_cast<uint8_t>(1U << (upper % 8));
                        const uint32_t xored = iv32 ^ predicted;
                        const size_t old_size = model.exception_values.size();
                        model.exception_values.resize(old_size + 4);
                        store_le32(model.exception_values.data() + old_size, xored);
                    }
                }
            }
        }

        metrics.exception_value_bytes = model.exception_values.size();
        metrics.predictor_exception_raw_bytes = metrics.exception_bitmap_bytes + metrics.exception_value_bytes;

        {
            ScopedTimer timer(metrics.rebuild_ms);
            std::vector<uint8_t> rebuilt = model.encrypted;
            if (predictor_enabled) {
                rebuilt.assign(kPayloadSize, 0);
                uint64_t exception_cursor = 0;
                for (uint32_t upper = 0; upper < kExpectedRecords; ++upper) {
                    const uint32_t pid = (upper << 16) | lane_path.lane;
                    std::array<uint8_t, kRecordSize> work{};
                    std::array<uint8_t, kRecordSize> rebuilt_record{};
                    std::memcpy(work.data(), model.base_template.data(), kRecordSize);
                    store_le32(work.data(), pid);

                    uint32_t iv32 = predictor->iv32[upper];
                    if ((model.exception_bitmap[upper / 8] & static_cast<uint8_t>(1U << (upper % 8))) != 0) {
                        if (exception_cursor + 4 > model.exception_values.size()) {
                            ++metrics.predictor_roundtrip_mismatches;
                        } else {
                            iv32 ^= load_le32(model.exception_values.data() + exception_cursor);
                            exception_cursor += 4;
                        }
                    }
                    const uint32_t actual_iv32 = load_le32(model.iv32_stream.data() + static_cast<size_t>(upper) * 4);
                    if (iv32 != actual_iv32) {
                        ++metrics.predictor_roundtrip_mismatches;
                    }
                    store_le32(work.data() + 0x48, iv32);
                    store_le16(work.data() + 0x1C, calc_pk3_checksum(work.data()));
                    encrypt_pk3(work.data(), rebuilt_record.data());
                    std::memcpy(rebuilt.data() + static_cast<size_t>(upper) * kRecordSize, rebuilt_record.data(), kRecordSize);
                }
                if (exception_cursor != model.exception_values.size()) {
                    add_error(metrics, "unused exception bytes after predictor roundtrip");
                    ++metrics.predictor_roundtrip_mismatches;
                }
            } else {
                for (uint32_t upper = 0; upper < kExpectedRecords; ++upper) {
                    const uint32_t pid = (upper << 16) | lane_path.lane;
                    const uint8_t* original = model.encrypted.data() + static_cast<size_t>(upper) * kRecordSize;
                    std::array<uint8_t, kRecordSize> work{};
                    std::array<uint8_t, kRecordSize> rebuilt_record{};
                    std::memcpy(work.data(), model.base_template.data(), kRecordSize);
                    store_le32(work.data(), pid);
                    const uint32_t iv32 = load_le32(model.iv32_stream.data() + static_cast<size_t>(upper) * 4);
                    store_le32(work.data() + 0x48, iv32);
                    store_le16(work.data() + 0x1C, calc_pk3_checksum(work.data()));
                    encrypt_pk3(work.data(), rebuilt_record.data());
                    if (std::memcmp(rebuilt_record.data(), original, kRecordSize) != 0) {
                        ++metrics.rebuild_mismatches;
                        add_error(metrics, "rebuild mismatch at " + hex8(pid));
                    }
                }
            }

            if (predictor_enabled && std::memcmp(rebuilt.data(), model.encrypted.data(), kPayloadSize) != 0) {
                ++metrics.rebuild_mismatches;
                add_error(metrics, "predictor rebuild payload mismatch");
            }
        }

        if (entropy_probe) {
            ScopedTimer timer(metrics.entropy_probe_ms);
            metrics.iv32_stream_zlib1_bytes = zlib_compress_size(model.iv32_stream, 1);
            metrics.iv32_stream_zlib9_bytes = zlib_compress_size(model.iv32_stream, 9);
            if (predictor_enabled) {
                metrics.exception_stream_zlib1_bytes = zlib_compress_concat_size(model.exception_bitmap, model.exception_values, 1);
                metrics.exception_stream_zlib9_bytes = zlib_compress_concat_size(model.exception_bitmap, model.exception_values, 9);
            }
        }

        model.encrypted_crc32 = crc32_vector(model.encrypted);
        model.encrypted_fnv64 = fnv1a64_vector(model.encrypted);
    } catch (const std::exception& error) {
        add_error(metrics, error.what());
    }

    metrics.total_ms = total_watch.elapsed_ms();
    return model;
}

LaneMetrics process_lane(
    const LanePath& lane_path,
    const PredictorTable* predictor,
    bool entropy_probe)
{
    LaneMetrics metrics;
    metrics.lane = lane_path.lane;
    metrics.zip_path = lane_path.path.string();
    Stopwatch total_watch;

    try {
        std::vector<uint8_t> zip_bytes;
        {
            ScopedTimer timer(metrics.read_zip_ms);
            zip_bytes = read_file_bytes(lane_path.path);
        }
        metrics.zip_size_bytes = zip_bytes.size();

        ZipCentralInfo central;
        std::vector<ZipEntryInfo> entries;
        {
            ScopedTimer timer(metrics.parse_zip_ms);
            central = parse_central_info(zip_bytes);
            entries = parse_central_entries(zip_bytes, central, lane_path.lane);
            metrics.entry_count = entries.size();
            metrics.raw_payload_bytes = metrics.entry_count * kRecordSize;
            metrics.zip64 = central.zip64;
            for (const auto& entry : entries) {
                if (entry.method == kZipMethodDeflate) {
                    ++metrics.deflate_entries;
                } else if (entry.method == kZipMethodStore) {
                    ++metrics.stored_entries;
                }
            }
        }

        std::vector<uint8_t> encrypted(kPayloadSize);
        std::array<uint8_t, kExpectedRecords> seen{};
        {
            ScopedTimer timer(metrics.inflate_ms);
            RawDeflateInflator inflator;
            for (const auto& entry : entries) {
                if (seen[entry.upper]) {
                    ++metrics.duplicate_entries;
                    add_error(metrics, "duplicate upper " + hex4(entry.upper));
                    continue;
                }
                seen[entry.upper] = 1;
                uint8_t* record = encrypted.data() + static_cast<size_t>(entry.upper) * kRecordSize;
                inflate_zip_entry_to_record(zip_bytes, entry, inflator, record);
                const uint32_t content_pid = load_le32(record);
                if (content_pid != entry.pid) {
                    ++metrics.content_pid_mismatches;
                    add_error(metrics, "content PID mismatch in " + entry.name);
                }
            }
        }

        for (size_t i = 0; i < seen.size(); ++i) {
            if (!seen[i]) {
                ++metrics.missing_entries;
                if (metrics.errors.size() < 12) {
                    add_error(metrics, "missing upper " + hex4(static_cast<uint32_t>(i)));
                }
            }
        }
        if (metrics.entry_count != kExpectedRecords) {
            add_error(metrics, "entry count is " + std::to_string(metrics.entry_count));
        }
        // A malformed lane should fail loudly instead of flowing zero-filled
        // placeholder records through the compression model and creating bogus
        // predictor/checksum statistics.
        if (metrics.missing_entries || metrics.duplicate_entries || metrics.content_pid_mismatches ||
            metrics.entry_count != kExpectedRecords) {
            add_error(metrics, "structural validation failed; skipped decrypt/model/rebuild pass");
            metrics.total_ms = total_watch.elapsed_ms();
            return metrics;
        }

        const bool predictor_enabled = predictor != nullptr && predictor->loaded;
        metrics.raw_payload_bytes = kPayloadSize;
        metrics.iv32_stream_bytes = kExpectedRecords * 4;
        if (predictor_enabled) {
            // The exception layer needs both a fixed bitmap and the XOR values.
            // Counting only values hid the per-lane bitmap cost in earlier reports.
            metrics.exception_bitmap_bytes = kExpectedRecords / 8;
        }

        std::vector<uint8_t> iv32_stream(kExpectedRecords * 4);
        std::vector<uint8_t> exception_bitmap(predictor_enabled ? kExpectedRecords / 8 : 0);
        std::vector<uint8_t> exception_values;
        if (predictor_enabled) {
            exception_values.reserve(kExpectedRecords * 4);
        }
        std::array<uint8_t, kRecordSize> base_template{};

        {
            ScopedTimer timer(metrics.decrypt_model_ms);
            std::array<uint8_t, kRecordSize> decrypted_record{};

            for (uint32_t upper = 0; upper < kExpectedRecords; ++upper) {
                const uint8_t* src = encrypted.data() + static_cast<size_t>(upper) * kRecordSize;
                uint8_t* dst = decrypted_record.data();
                decrypt_pk3(src, dst);
                if (upper == 0) {
                    // The compression model stores one logical PK3 template per
                    // lane. Rebuild must not borrow per-record constant bytes.
                    base_template = decrypted_record;
                }

                const uint32_t expected_pid = (upper << 16) | lane_path.lane;
                if (load_le32(dst) != expected_pid) {
                    ++metrics.content_pid_mismatches;
                }

                const uint16_t stored_checksum = load_le16(dst + 0x1C);
                const uint16_t actual_checksum = calc_pk3_checksum(dst);
                if (stored_checksum != actual_checksum) {
                    ++metrics.checksum_failures;
                    if (metrics.errors.size() < 12) {
                        add_error(metrics, "checksum mismatch at " + hex8(expected_pid));
                    }
                }
                if (load_le16(dst + 0x20) != kGen3SpeciesSpinda) {
                    ++metrics.species_mismatches;
                    add_error(metrics, "species is not Gen 3 internal Spinda 308 at " + hex8(expected_pid));
                }

                const uint32_t iv32 = load_le32(dst + 0x48);
                store_le32(iv32_stream.data() + static_cast<size_t>(upper) * 4, iv32);

                if (upper > 0) {
                    if (!template_constant_fields_match(base_template.data(), dst)) {
                        ++metrics.template_mismatches;
                    }
                }

                if (predictor_enabled) {
                    const uint32_t predicted = predictor->iv32[upper];
                    if (predicted == iv32) {
                        ++metrics.predictor_matches;
                    } else {
                        ++metrics.predictor_exceptions;
                        exception_bitmap[upper / 8] |= static_cast<uint8_t>(1U << (upper % 8));
                        const uint32_t xored = iv32 ^ predicted;
                        const size_t old_size = exception_values.size();
                        exception_values.resize(old_size + 4);
                        store_le32(exception_values.data() + old_size, xored);
                    }
                }
            }
        }

        metrics.exception_value_bytes = exception_values.size();
        metrics.predictor_exception_raw_bytes = metrics.exception_bitmap_bytes + metrics.exception_value_bytes;

        {
            ScopedTimer timer(metrics.rebuild_ms);
            std::array<uint8_t, kRecordSize> rebuilt_record{};
            std::array<uint8_t, kRecordSize> work{};
            uint64_t exception_cursor = 0;

            for (uint32_t upper = 0; upper < kExpectedRecords; ++upper) {
                const uint32_t pid = (upper << 16) | lane_path.lane;
                const uint8_t* original = encrypted.data() + static_cast<size_t>(upper) * kRecordSize;
                std::memcpy(work.data(), base_template.data(), kRecordSize);
                store_le32(work.data(), pid);

                uint32_t iv32_from_predictor = load_le32(iv32_stream.data() + static_cast<size_t>(upper) * 4);
                if (predictor_enabled) {
                    iv32_from_predictor = predictor->iv32[upper];
                    if ((exception_bitmap[upper / 8] & static_cast<uint8_t>(1U << (upper % 8))) != 0) {
                        if (exception_cursor + 4 > exception_values.size()) {
                            ++metrics.predictor_roundtrip_mismatches;
                        } else {
                            iv32_from_predictor ^= load_le32(exception_values.data() + exception_cursor);
                            exception_cursor += 4;
                        }
                    }
                    const uint32_t actual_iv32 = load_le32(iv32_stream.data() + static_cast<size_t>(upper) * 4);
                    if (iv32_from_predictor != actual_iv32) {
                        ++metrics.predictor_roundtrip_mismatches;
                    }
                }
                store_le32(work.data() + 0x48, iv32_from_predictor);
                store_le16(work.data() + 0x1C, calc_pk3_checksum(work.data()));

                encrypt_pk3(work.data(), rebuilt_record.data());
                if (std::memcmp(rebuilt_record.data(), original, kRecordSize) != 0) {
                    ++metrics.rebuild_mismatches;
                    if (metrics.errors.size() < 12) {
                        add_error(metrics, "rebuild mismatch at " + hex8(pid));
                    }
                }
            }
            if (predictor_enabled && exception_cursor != exception_values.size()) {
                add_error(metrics, "unused exception bytes after predictor roundtrip");
                ++metrics.predictor_roundtrip_mismatches;
            }
        }

        if (entropy_probe) {
            ScopedTimer timer(metrics.entropy_probe_ms);
            metrics.iv32_stream_zlib1_bytes = zlib_compress_size(iv32_stream, 1);
            metrics.iv32_stream_zlib9_bytes = zlib_compress_size(iv32_stream, 9);
            if (predictor_enabled) {
                metrics.exception_stream_zlib1_bytes = zlib_compress_concat_size(exception_bitmap, exception_values, 1);
                metrics.exception_stream_zlib9_bytes = zlib_compress_concat_size(exception_bitmap, exception_values, 9);
            }
        }
    } catch (const std::exception& error) {
        add_error(metrics, error.what());
    }

    metrics.total_ms = total_watch.elapsed_ms();
    return metrics;
}

double sum_field(const std::vector<LaneMetrics>& lanes, double LaneMetrics::*field) {
    double total = 0.0;
    for (const auto& lane : lanes) {
        total += lane.*field;
    }
    return total;
}

uint64_t sum_field_u64(const std::vector<LaneMetrics>& lanes, uint64_t LaneMetrics::*field) {
    uint64_t total = 0;
    for (const auto& lane : lanes) {
        total += lane.*field;
    }
    return total;
}

bool lane_has_failure(const LaneMetrics& lane) {
    return !lane.errors.empty() ||
           lane.checksum_failures != 0 ||
           lane.duplicate_entries != 0 ||
           lane.missing_entries != 0 ||
           lane.content_pid_mismatches != 0 ||
           lane.species_mismatches != 0 ||
           lane.template_mismatches != 0 ||
           lane.predictor_roundtrip_mismatches != 0 ||
           lane.rebuild_mismatches != 0;
}

bool any_lane_has_failure(const std::vector<LaneMetrics>& lanes) {
    return std::any_of(lanes.begin(), lanes.end(), lane_has_failure);
}

std::vector<std::pair<std::string, double>> sorted_hotspots(const std::vector<LaneMetrics>& lanes) {
    std::vector<std::pair<std::string, double>> items = {
        {"read_zip_ms", sum_field(lanes, &LaneMetrics::read_zip_ms)},
        {"parse_zip_ms", sum_field(lanes, &LaneMetrics::parse_zip_ms)},
        {"inflate_ms", sum_field(lanes, &LaneMetrics::inflate_ms)},
        {"decrypt_model_ms", sum_field(lanes, &LaneMetrics::decrypt_model_ms)},
        {"rebuild_ms", sum_field(lanes, &LaneMetrics::rebuild_ms)},
        {"entropy_probe_ms", sum_field(lanes, &LaneMetrics::entropy_probe_ms)},
    };
    std::sort(items.begin(), items.end(), [](const auto& a, const auto& b) {
        return a.second > b.second;
    });
    return items;
}

std::string build_report_json(
    const Options& options,
    const std::vector<LaneMetrics>& lanes,
    const std::vector<LanePath>& lane_paths,
    bool predictor_loaded,
    double predictor_load_ms)
{
    const uint64_t total_errors = [&]() {
        uint64_t count = 0;
        for (const auto& lane : lanes) {
            count += lane.errors.empty() ? 0 : 1;
        }
        return count;
    }();
    const uint64_t total_records = sum_field_u64(lanes, &LaneMetrics::entry_count);
    const double total_ms = sum_field(lanes, &LaneMetrics::total_ms) + predictor_load_ms;
    const auto hotspots = sorted_hotspots(lanes);
    const bool audit_failed = any_lane_has_failure(lanes);

    std::ostringstream out;
    out << std::fixed << std::setprecision(3);
    out << "{\n";
    out << "  \"schema\": \"spc3_phase3_cpu_prototype_report.v1\",\n";
    out << "  \"status\": {\n";
    out << "    \"current_status\": \"CPU C++ prototype with x86-64 assembly hot loops for Phase 3 PK3 custom compression experiments.\",\n";
    out << "    \"evidence_level\": \"Observed once for this local run; rerun after source, compiler, corpus, or predictor changes.\",\n";
    out << "    \"known_gaps\": \"SPC3 files can be emitted, inspected, unpacked, verified, benchmarked, and driven from the native GUI; final entropy coding and release packaging still depend on corpus proof.\",\n";
    out << "    \"next_action\": \"Use v0.2 typed level-3 profiles, codec gates, and report summaries to decide CRC policy before adding more assembly.\"\n";
    out << "  },\n";
    out << "  \"config\": {\n";
    out << "    \"root\": \"" << json_escape(options.root.string()) << "\",\n";
    out << "    \"predictor\": \"" << json_escape(options.predictor.string()) << "\",\n";
    out << "    \"predictor_loaded\": " << (predictor_loaded ? "true" : "false") << ",\n";
    out << "    \"hotloop_backend\": \"" << hotloop_backend() << "\",\n";
    out << "    \"limit_zips\": " << options.limit_zips << ",\n";
    out << "    \"all_zips\": " << (options.all_zips ? "true" : "false") << ",\n";
    out << "    \"zips_found_for_run\": " << lane_paths.size() << ",\n";
    out << "    \"loose_pk3_written\": false,\n";
    out << "    \"spc3_written\": false,\n";
    out << "    \"file_handling\": \"ZIP bytes, inflated records, decrypted records, model streams, and one rebuilt-record scratch buffer are held in RAM per lane; only this report is written.\"\n";
    out << "  },\n";
    out << "  \"totals\": {\n";
    out << "    \"lanes_processed\": " << lanes.size() << ",\n";
    out << "    \"audit_failed\": " << (audit_failed ? "true" : "false") << ",\n";
    out << "    \"records_processed\": " << total_records << ",\n";
    out << "    \"lane_error_count\": " << total_errors << ",\n";
    out << "    \"duplicate_entries\": " << sum_field_u64(lanes, &LaneMetrics::duplicate_entries) << ",\n";
    out << "    \"missing_entries\": " << sum_field_u64(lanes, &LaneMetrics::missing_entries) << ",\n";
    out << "    \"checksum_failures\": " << sum_field_u64(lanes, &LaneMetrics::checksum_failures) << ",\n";
    out << "    \"content_pid_mismatches\": " << sum_field_u64(lanes, &LaneMetrics::content_pid_mismatches) << ",\n";
    out << "    \"species_mismatches\": " << sum_field_u64(lanes, &LaneMetrics::species_mismatches) << ",\n";
    out << "    \"template_mismatches\": " << sum_field_u64(lanes, &LaneMetrics::template_mismatches) << ",\n";
    out << "    \"predictor_matches\": " << sum_field_u64(lanes, &LaneMetrics::predictor_matches) << ",\n";
    out << "    \"predictor_exceptions\": " << sum_field_u64(lanes, &LaneMetrics::predictor_exceptions) << ",\n";
    out << "    \"predictor_roundtrip_mismatches\": " << sum_field_u64(lanes, &LaneMetrics::predictor_roundtrip_mismatches) << ",\n";
    out << "    \"rebuild_mismatches\": " << sum_field_u64(lanes, &LaneMetrics::rebuild_mismatches) << ",\n";
    out << "    \"zip_size_bytes\": " << sum_field_u64(lanes, &LaneMetrics::zip_size_bytes) << ",\n";
    out << "    \"raw_payload_bytes\": " << sum_field_u64(lanes, &LaneMetrics::raw_payload_bytes) << ",\n";
    out << "    \"iv32_stream_bytes\": " << sum_field_u64(lanes, &LaneMetrics::iv32_stream_bytes) << ",\n";
    out << "    \"predictor_exception_raw_bytes\": " << sum_field_u64(lanes, &LaneMetrics::predictor_exception_raw_bytes) << ",\n";
    out << "    \"iv32_stream_zlib1_bytes\": " << sum_field_u64(lanes, &LaneMetrics::iv32_stream_zlib1_bytes) << ",\n";
    out << "    \"iv32_stream_zlib9_bytes\": " << sum_field_u64(lanes, &LaneMetrics::iv32_stream_zlib9_bytes) << ",\n";
    out << "    \"exception_stream_zlib1_bytes\": " << sum_field_u64(lanes, &LaneMetrics::exception_stream_zlib1_bytes) << ",\n";
    out << "    \"exception_stream_zlib9_bytes\": " << sum_field_u64(lanes, &LaneMetrics::exception_stream_zlib9_bytes) << "\n";
    out << "  },\n";
    out << "  \"timings_ms\": {\n";
    out << "    \"predictor_load_ms\": " << predictor_load_ms << ",\n";
    out << "    \"read_zip_ms\": " << sum_field(lanes, &LaneMetrics::read_zip_ms) << ",\n";
    out << "    \"parse_zip_ms\": " << sum_field(lanes, &LaneMetrics::parse_zip_ms) << ",\n";
    out << "    \"inflate_ms\": " << sum_field(lanes, &LaneMetrics::inflate_ms) << ",\n";
    out << "    \"decrypt_model_ms\": " << sum_field(lanes, &LaneMetrics::decrypt_model_ms) << ",\n";
    out << "    \"rebuild_ms\": " << sum_field(lanes, &LaneMetrics::rebuild_ms) << ",\n";
    out << "    \"entropy_probe_ms\": " << sum_field(lanes, &LaneMetrics::entropy_probe_ms) << ",\n";
    out << "    \"total_ms\": " << total_ms << "\n";
    out << "  },\n";
    out << "  \"hotspots_desc\": [\n";
    for (size_t i = 0; i < hotspots.size(); ++i) {
        out << "    {\"name\": \"" << hotspots[i].first << "\", \"ms\": " << hotspots[i].second << "}";
        out << (i + 1 == hotspots.size() ? "\n" : ",\n");
    }
    out << "  ],\n";
    out << "  \"gpu_decision_hint\": {\n";
    out << "    \"rule\": \"Use optional CUDA offload only if inflate/decrypt/rebuild dominates enough across many lanes to repay transfer and startup costs.\",\n";
    out << "    \"current_cpu_side_entropy_policy\": \"Keep LZMA2/zstd/deflate entropy coding CPU-side unless a lower-ratio GPU-friendly stream is deliberately chosen.\"\n";
    out << "  },\n";
    out << "  \"lanes\": [\n";
    for (size_t i = 0; i < lanes.size(); ++i) {
        const auto& lane = lanes[i];
        out << "    {\n";
        out << "      \"lane\": \"" << hex4(lane.lane) << "\",\n";
        out << "      \"zip_path\": \"" << json_escape(lane.zip_path) << "\",\n";
        out << "      \"zip_size_bytes\": " << lane.zip_size_bytes << ",\n";
        out << "      \"entry_count\": " << lane.entry_count << ",\n";
        out << "      \"zip64\": " << (lane.zip64 ? "true" : "false") << ",\n";
        out << "      \"deflate_entries\": " << lane.deflate_entries << ",\n";
        out << "      \"stored_entries\": " << lane.stored_entries << ",\n";
        out << "      \"audit_failed\": " << (lane_has_failure(lane) ? "true" : "false") << ",\n";
        out << "      \"duplicate_entries\": " << lane.duplicate_entries << ",\n";
        out << "      \"missing_entries\": " << lane.missing_entries << ",\n";
        out << "      \"checksum_failures\": " << lane.checksum_failures << ",\n";
        out << "      \"content_pid_mismatches\": " << lane.content_pid_mismatches << ",\n";
        out << "      \"species_mismatches\": " << lane.species_mismatches << ",\n";
        out << "      \"template_mismatches\": " << lane.template_mismatches << ",\n";
        out << "      \"predictor_matches\": " << lane.predictor_matches << ",\n";
        out << "      \"predictor_exceptions\": " << lane.predictor_exceptions << ",\n";
        out << "      \"predictor_roundtrip_mismatches\": " << lane.predictor_roundtrip_mismatches << ",\n";
        out << "      \"rebuild_mismatches\": " << lane.rebuild_mismatches << ",\n";
        out << "      \"size_model\": {\n";
        out << "        \"raw_payload_bytes\": " << lane.raw_payload_bytes << ",\n";
        out << "        \"iv32_stream_bytes\": " << lane.iv32_stream_bytes << ",\n";
        out << "        \"exception_bitmap_bytes\": " << lane.exception_bitmap_bytes << ",\n";
        out << "        \"exception_value_bytes\": " << lane.exception_value_bytes << ",\n";
        out << "        \"predictor_exception_raw_bytes\": " << lane.predictor_exception_raw_bytes << ",\n";
        out << "        \"iv32_stream_zlib1_bytes\": " << lane.iv32_stream_zlib1_bytes << ",\n";
        out << "        \"iv32_stream_zlib9_bytes\": " << lane.iv32_stream_zlib9_bytes << ",\n";
        out << "        \"exception_stream_zlib1_bytes\": " << lane.exception_stream_zlib1_bytes << ",\n";
        out << "        \"exception_stream_zlib9_bytes\": " << lane.exception_stream_zlib9_bytes << "\n";
        out << "      },\n";
        out << "      \"timings_ms\": {\n";
        out << "        \"read_zip_ms\": " << lane.read_zip_ms << ",\n";
        out << "        \"parse_zip_ms\": " << lane.parse_zip_ms << ",\n";
        out << "        \"inflate_ms\": " << lane.inflate_ms << ",\n";
        out << "        \"decrypt_model_ms\": " << lane.decrypt_model_ms << ",\n";
        out << "        \"rebuild_ms\": " << lane.rebuild_ms << ",\n";
        out << "        \"entropy_probe_ms\": " << lane.entropy_probe_ms << ",\n";
        out << "        \"total_ms\": " << lane.total_ms << "\n";
        out << "      },\n";
        out << "      \"errors\": [";
        for (size_t j = 0; j < lane.errors.size(); ++j) {
            out << "\"" << json_escape(lane.errors[j]) << "\"";
            if (j + 1 != lane.errors.size()) {
                out << ", ";
            }
        }
        out << "]\n";
        out << "    }" << (i + 1 == lanes.size() ? "\n" : ",\n");
    }
    out << "  ]\n";
    out << "}\n";
    return out.str();
}

std::vector<uint8_t> decrypted_stream_from_model(const LaneModel& model) {
    std::vector<uint8_t> decrypted(kPayloadSize);
    for (uint32_t upper = 0; upper < kExpectedRecords; ++upper) {
        decrypt_pk3(
            model.encrypted.data() + static_cast<size_t>(upper) * kRecordSize,
            decrypted.data() + static_cast<size_t>(upper) * kRecordSize);
    }
    return decrypted;
}

std::vector<uint8_t> make_template_iv32_model(const LaneModel& model) {
    std::vector<uint8_t> raw;
    raw.reserve(kRecordSize + model.iv32_stream.size());
    raw.insert(raw.end(), model.base_template.begin(), model.base_template.end());
    raw.insert(raw.end(), model.iv32_stream.begin(), model.iv32_stream.end());
    return raw;
}

std::vector<uint8_t> make_template_exception_model(const LaneModel& model) {
    std::vector<uint8_t> raw;
    raw.reserve(kRecordSize + model.exception_bitmap.size() + model.exception_values.size());
    raw.insert(raw.end(), model.base_template.begin(), model.base_template.end());
    raw.insert(raw.end(), model.exception_bitmap.begin(), model.exception_bitmap.end());
    raw.insert(raw.end(), model.exception_values.begin(), model.exception_values.end());
    return raw;
}

TypedLevel3Policy typed_level3_policy_for_pack(const Options& options) {
    CodecSpec codec = requested_pack_codec(options);
    const bool codec_level_set = requested_pack_codec_level_set(options);
    if (codec.id == CodecId::Rans) {
        if (codec_level_set) {
            throw std::runtime_error("rANS/FSE typed streams do not take --codec-level");
        }
        codec = {CodecId::Rans, 0, 0};
    } else {
        codec = resolve_pack_codec(3, codec, codec_level_set);
    }
    const CodecSpec none{CodecId::None, 0, 0};
    if (options.typed_exceptions_only || codec.id == CodecId::Rans) {
        return {"exceptions-" + codec_display_name(codec), none, codec, codec};
    }
    return {"all-" + codec_display_name(codec), codec, codec, codec};
}

struct TypedLevel3StreamBuild {
    std::vector<uint8_t> stream;
    std::array<Spc3TypedSubstreamEntry, kSpc3TypedLevel3SubstreamCount> substreams{};
    uint64_t raw_size = 0;
};

struct CpuDecodeProfile {
    bool used = false;
    std::string backend = "cpu";
    std::string crc_backend = "zlib_crc32";
    uint32_t lane_count = 0;
    uint32_t typed_lanes = 0;
    uint32_t legacy_lanes = 0;
    uint64_t crc_bytes = 0;
    double stream_decode_ms = 0;
    double iv_expand_ms = 0;
    double rebuild_encrypt_ms = 0;
    double crc_ms = 0;
    double total_ms = 0;
};

TypedLevel3StreamBuild build_typed_level3_stream(
    const LaneModel& model,
    const TypedLevel3Policy& policy)
{
    TypedLevel3StreamBuild built;
    const std::vector<uint8_t> template_raw(model.base_template.begin(), model.base_template.end());
    const std::vector<uint8_t>& bitmap_raw = model.exception_bitmap;
    const std::vector<uint8_t>& values_raw = model.exception_values;
    if (template_raw.size() != kRecordSize || bitmap_raw.size() != kExpectedRecords / 8ULL ||
        values_raw.size() % 4 != 0) {
        throw std::runtime_error("typed level 3 raw stream sizes are invalid");
    }

    std::array<std::vector<uint8_t>, kSpc3TypedLevel3SubstreamCount> compressed = {{
        codec_compress_data(template_raw, policy.template_codec),
        codec_compress_data(bitmap_raw, policy.bitmap_codec),
        codec_compress_data(values_raw, policy.values_codec),
    }};
    const std::array<uint64_t, kSpc3TypedLevel3SubstreamCount> raw_sizes = {{
        template_raw.size(),
        bitmap_raw.size(),
        values_raw.size(),
    }};
    const std::array<uint32_t, kSpc3TypedLevel3SubstreamCount> kinds = {{
        kSpc3TypedSubstreamTemplate,
        kSpc3TypedSubstreamBitmap,
        kSpc3TypedSubstreamValues,
    }};
    const std::array<CodecSpec, kSpc3TypedLevel3SubstreamCount> codecs = {{
        policy.template_codec,
        policy.bitmap_codec,
        policy.values_codec,
    }};

    built.raw_size = raw_sizes[0] + raw_sizes[1] + raw_sizes[2];
    const uint64_t table_size = kSpc3TypedLevel3SubstreamCount * kSpc3TypedLevel3SubstreamEntrySize;
    built.stream.resize(static_cast<size_t>(table_size), 0);
    uint64_t offset = table_size;
    for (size_t i = 0; i < kSpc3TypedLevel3SubstreamCount; ++i) {
        Spc3TypedSubstreamEntry& sub = built.substreams[i];
        sub.kind = kinds[i];
        sub.flags = pack_entry_codec_flags(codecs[i]);
        sub.offset = offset;
        sub.stream_size = compressed[i].size();
        sub.raw_size = raw_sizes[i];

        const size_t pos = i * static_cast<size_t>(kSpc3TypedLevel3SubstreamEntrySize);
        patch_u32(built.stream, pos + 0, sub.kind);
        patch_u32(built.stream, pos + 4, sub.flags);
        patch_u64(built.stream, pos + 8, sub.offset);
        patch_u64(built.stream, pos + 16, sub.stream_size);
        patch_u64(built.stream, pos + 24, sub.raw_size);

        built.stream.insert(built.stream.end(), compressed[i].begin(), compressed[i].end());
        offset = checked_add_u64(offset, sub.stream_size, "typed level 3 stream size");
    }
    return built;
}

std::vector<uint8_t> rebuild_payload_from_template_iv32(
    uint32_t lane,
    const uint8_t* template_record,
    const uint8_t* iv32_stream,
    size_t iv32_size,
    CpuDecodeProfile* profile = nullptr)
{
    if (iv32_size != kExpectedRecords * 4) {
        throw std::runtime_error("IV32 stream has wrong size");
    }
    std::vector<uint8_t> encrypted(kPayloadSize);
    {
        std::optional<ScopedTimer> timer;
        if (profile != nullptr) {
            timer.emplace(profile->rebuild_encrypt_ms);
        }
        std::array<uint8_t, kRecordSize> work{};
        std::array<uint8_t, kRecordSize> rebuilt{};
        for (uint32_t upper = 0; upper < kExpectedRecords; ++upper) {
            const uint32_t pid = (upper << 16) | (lane & 0xFFFFU);
            std::memcpy(work.data(), template_record, kRecordSize);
            store_le32(work.data(), pid);
            const uint32_t iv32 = load_le32(iv32_stream + static_cast<size_t>(upper) * 4);
            store_le32(work.data() + 0x48, iv32);
            store_le16(work.data() + 0x1C, calc_pk3_checksum(work.data()));
            encrypt_pk3(work.data(), rebuilt.data());
            std::memcpy(encrypted.data() + static_cast<size_t>(upper) * kRecordSize, rebuilt.data(), kRecordSize);
        }
    }
    return encrypted;
}

uint64_t expand_iv32_exceptions(
    uint32_t* out,
    const PredictorTable& predictor,
    const uint8_t* bitmap,
    size_t bitmap_size,
    const uint8_t* values,
    size_t values_size)
{
    if (!predictor.loaded) {
        throw std::runtime_error("exception expansion requires predictor");
    }
    if (bitmap_size != kExpectedRecords / 8) {
        throw std::runtime_error("exception bitmap has wrong size");
    }
    if (values_size % 4 != 0) {
        throw std::runtime_error("exception value stream has partial u32");
    }
    uint64_t bitmap_exception_count = 0;
    for (size_t i = 0; i < bitmap_size; ++i) {
        uint8_t byte = bitmap[i];
        while (byte) {
            bitmap_exception_count += byte & 1U;
            byte >>= 1;
        }
    }
    if (bitmap_exception_count > values_size / 4) {
        throw std::runtime_error("exception value stream ended early");
    }
#if SPC3_USE_X86_64_ASM
    (void)values_size;
    return spc3_expand_iv32_exceptions_asm(
        out,
        predictor.iv32.data(),
        bitmap,
        reinterpret_cast<const uint32_t*>(values));
#else
    size_t value_cursor = 0;
    for (uint32_t upper = 0; upper < kExpectedRecords; ++upper) {
        uint32_t iv32 = predictor.iv32[upper];
        if ((bitmap[upper / 8] & static_cast<uint8_t>(1U << (upper % 8))) != 0) {
            if (value_cursor + 4 > values_size) {
                throw std::runtime_error("exception value stream ended early");
            }
            iv32 ^= load_le32(values + value_cursor);
            value_cursor += 4;
        }
        out[upper] = iv32;
    }
    return value_cursor / 4;
#endif
}

std::vector<uint8_t> rebuild_payload_from_template_exceptions(
    uint32_t lane,
    const uint8_t* template_record,
    const uint8_t* bitmap,
    size_t bitmap_size,
    const uint8_t* values,
    size_t values_size,
    const PredictorTable& predictor,
    CpuDecodeProfile* profile = nullptr)
{
    if (!predictor.loaded) {
        throw std::runtime_error("level 3 rebuild requires predictor");
    }
    if (bitmap_size != kExpectedRecords / 8) {
        throw std::runtime_error("exception bitmap has wrong size");
    }
    if (values_size % 4 != 0) {
        throw std::runtime_error("exception value stream has partial u32");
    }
    std::vector<uint32_t> iv32_stream(kExpectedRecords);
    uint64_t value_count = 0;
    {
        std::optional<ScopedTimer> timer;
        if (profile != nullptr) {
            timer.emplace(profile->iv_expand_ms);
        }
        value_count = expand_iv32_exceptions(
            iv32_stream.data(),
            predictor,
            bitmap,
            bitmap_size,
            values,
            values_size);
    }
    if (value_count * 4 != values_size) {
        throw std::runtime_error("exception value stream has trailing bytes");
    }
    std::vector<uint8_t> encrypted(kPayloadSize);
    {
        std::optional<ScopedTimer> timer;
        if (profile != nullptr) {
            timer.emplace(profile->rebuild_encrypt_ms);
        }
        std::array<uint8_t, kRecordSize> work{};
        std::array<uint8_t, kRecordSize> rebuilt{};
        for (uint32_t upper = 0; upper < kExpectedRecords; ++upper) {
            const uint32_t pid = (upper << 16) | (lane & 0xFFFFU);
            std::memcpy(work.data(), template_record, kRecordSize);
            store_le32(work.data(), pid);
            store_le32(work.data() + 0x48, iv32_stream[upper]);
            store_le16(work.data() + 0x1C, calc_pk3_checksum(work.data()));
            encrypt_pk3(work.data(), rebuilt.data());
            std::memcpy(encrypted.data() + static_cast<size_t>(upper) * kRecordSize, rebuilt.data(), kRecordSize);
        }
    }
    return encrypted;
}

std::vector<uint8_t> rebuild_payload_from_stream_data(
    const uint8_t* stream,
    size_t stream_size,
    const Spc3TableEntry& entry,
    const PredictorTable& predictor,
    CpuDecodeProfile* profile = nullptr)
{
    if (entry_is_typed_level3(entry)) {
        if (!predictor.loaded) {
            throw std::runtime_error("typed level 3 rebuild requires predictor");
        }
        const auto decode_substream = [&](uint32_t kind, const std::string& label) {
            const Spc3TypedSubstreamEntry& sub = typed_substream_by_kind(entry, kind);
            const uint64_t sub_end = checked_add_u64(sub.offset, sub.stream_size, label + " end");
            if (sub_end > stream_size) {
                throw std::runtime_error(label + " outside typed stream");
            }
            const CodecSpec codec = codec_from_entry_flags(sub.flags, 3, true);
            std::optional<ScopedTimer> timer;
            if (profile != nullptr) {
                timer.emplace(profile->stream_decode_ms);
            }
            return codec_decompress_exact(
                stream + static_cast<size_t>(sub.offset),
                static_cast<size_t>(sub.stream_size),
                sub.raw_size,
                codec,
                label);
        };

        const std::vector<uint8_t> template_raw =
            decode_substream(kSpc3TypedSubstreamTemplate, "typed level 3 template stream");
        const std::vector<uint8_t> bitmap_raw =
            decode_substream(kSpc3TypedSubstreamBitmap, "typed level 3 bitmap stream");
        const std::vector<uint8_t> values_raw =
            decode_substream(kSpc3TypedSubstreamValues, "typed level 3 XOR value stream");
        if (template_raw.size() != kRecordSize ||
            bitmap_raw.size() != kExpectedRecords / 8ULL ||
            values_raw.size() % 4 != 0) {
            throw std::runtime_error("typed level 3 decoded raw sizes are invalid");
        }
        return rebuild_payload_from_template_exceptions(
            entry.lane,
            template_raw.data(),
            bitmap_raw.data(),
            bitmap_raw.size(),
            values_raw.data(),
            values_raw.size(),
            predictor,
            profile);
    }

    const CodecSpec codec = codec_from_entry_flags(entry.flags, entry.level);
    if (entry.level == 0) {
        if (stream_size != kPayloadSize) {
            throw std::runtime_error("level 0 stream has wrong size");
        }
        std::optional<ScopedTimer> timer;
        if (profile != nullptr) {
            timer.emplace(profile->stream_decode_ms);
        }
        return std::vector<uint8_t>(stream, stream + kPayloadSize);
    }
    if (entry.level == 1) {
        std::vector<uint8_t> decrypted;
        {
            std::optional<ScopedTimer> timer;
            if (profile != nullptr) {
                timer.emplace(profile->stream_decode_ms);
            }
            decrypted = codec_decompress_exact(
                stream,
                stream_size,
                entry.uncompressed_model_size,
                codec,
                "level 1 decrypted stream");
        }
        if (decrypted.size() != kPayloadSize) {
            throw std::runtime_error("level 1 decrypted stream has wrong size");
        }
        std::vector<uint8_t> encrypted(kPayloadSize);
        {
            std::optional<ScopedTimer> timer;
            if (profile != nullptr) {
                timer.emplace(profile->rebuild_encrypt_ms);
            }
            for (uint32_t upper = 0; upper < kExpectedRecords; ++upper) {
                encrypt_pk3(
                    decrypted.data() + static_cast<size_t>(upper) * kRecordSize,
                    encrypted.data() + static_cast<size_t>(upper) * kRecordSize);
            }
        }
        return encrypted;
    }
    if (entry.level == 2) {
        std::vector<uint8_t> raw;
        {
            std::optional<ScopedTimer> timer;
            if (profile != nullptr) {
                timer.emplace(profile->stream_decode_ms);
            }
            raw = codec_decompress_exact(
                stream,
                stream_size,
                entry.uncompressed_model_size,
                codec,
                "level 2 IV32 stream");
        }
        if (raw.size() != kRecordSize + kExpectedRecords * 4) {
            throw std::runtime_error("level 2 model has wrong size");
        }
        return rebuild_payload_from_template_iv32(
            entry.lane,
            raw.data(),
            raw.data() + kRecordSize,
            raw.size() - kRecordSize,
            profile);
    }
    if (entry.level == 3) {
        std::vector<uint8_t> raw;
        {
            std::optional<ScopedTimer> timer;
            if (profile != nullptr) {
                timer.emplace(profile->stream_decode_ms);
            }
            raw = codec_decompress_exact(
                stream,
                stream_size,
                entry.uncompressed_model_size,
                codec,
                "level 3 exception stream");
        }
        if (raw.size() < kLevel3ModelMinSize) {
            throw std::runtime_error("level 3 model is too small");
        }
        const uint8_t* bitmap = raw.data() + kRecordSize;
        const uint8_t* values = bitmap + kLevel3ExceptionBitmapBytes;
        const size_t values_size = raw.size() - kRecordSize - kLevel3ExceptionBitmapBytes;
        return rebuild_payload_from_template_exceptions(
            entry.lane,
            raw.data(),
            bitmap,
            kLevel3ExceptionBitmapBytes,
            values,
            values_size,
            predictor,
            profile);
    }
    throw std::runtime_error("unknown SPC3 level in stream");
}

std::vector<uint8_t> rebuild_payload_from_spc3_stream(
    const std::vector<uint8_t>& spc3_bytes,
    const Spc3TableEntry& entry,
    const PredictorTable& predictor,
    CpuDecodeProfile* profile = nullptr)
{
    const size_t stream_offset = checked_offset(spc3_bytes, entry.stream_offset, entry.stream_size, "SPC3 lane stream");
    return rebuild_payload_from_stream_data(
        spc3_bytes.data() + stream_offset,
        static_cast<size_t>(entry.stream_size),
        entry,
        predictor,
        profile);
}

struct Spc3BuildResult {
    std::vector<uint8_t> bytes;
    std::vector<Spc3TableEntry> entries;
    uint64_t roundtrip_mismatches = 0;
    double build_ms = 0;
};

// Slim per-pack accumulator used by run_pack_mode's streaming impl. Holds only
// table entries and running totals; never accumulates LaneModels or raw streams.
struct StreamingPackResult {
    std::vector<Spc3TableEntry> entries;
    uint64_t source_zip_bytes = 0;
    uint64_t raw_payload_bytes = 0;
    uint64_t roundtrip_mismatches = 0;
    uint64_t total_size = 0;
    double build_ms = 0;
};

Spc3BuildResult build_spc3_file(
    const std::vector<LaneModel>& models,
    size_t model_count,
    uint32_t level,
    const PredictorTable* predictor,
    bool embed_predictor,
    const CodecSpec& requested_codec,
    bool codec_level_set,
    bool typed_level3 = false,
    const TypedLevel3Policy* typed_policy = nullptr)
{
    if (model_count > models.size()) {
        throw std::runtime_error("SPC3 model count exceeds loaded models");
    }
    if (model_count == 0) {
        throw std::runtime_error("cannot build empty SPC3 file");
    }
    if (model_count > std::numeric_limits<uint32_t>::max()) {
        throw std::runtime_error("SPC3 lane count exceeds u32 header limit");
    }
    if (level > 3) {
        throw std::runtime_error("SPC3 level must be 0..3");
    }
    if (level == 3 && (predictor == nullptr || !predictor->loaded)) {
        throw std::runtime_error("SPC3 level 3 requires predictor table");
    }
    if (typed_level3 && (level != 3 || typed_policy == nullptr)) {
        throw std::runtime_error("typed level 3 pack requires level 3 policy");
    }

    Stopwatch watch;
    Spc3BuildResult result;
    std::vector<uint8_t> predictor_stream;
    if (level == 3 && embed_predictor) {
        predictor_stream = zlib_compress_data(serialize_predictor_raw(*predictor), 9);
    }

    std::vector<std::vector<uint8_t>> lane_streams;
    lane_streams.reserve(model_count);
    result.entries.reserve(model_count);

    for (size_t i = 0; i < model_count; ++i) {
        const LaneModel& model = models[i];
        if (lane_has_failure(model.metrics)) {
            throw std::runtime_error("cannot pack failed lane " + hex4(model.metrics.lane));
        }

        std::vector<uint8_t> stream;
        uint64_t uncompressed_model_size = 0;
        std::array<Spc3TypedSubstreamEntry, kSpc3TypedLevel3SubstreamCount> typed_substreams{};
        const CodecSpec lane_codec = typed_level3
            ? CodecSpec{CodecId::None, 0, 0}
            : resolve_pack_codec(level, requested_codec, codec_level_set);
        if (level == 0) {
            stream = model.encrypted;
            uncompressed_model_size = model.encrypted.size();
        } else if (level == 1) {
            const std::vector<uint8_t> decrypted = decrypted_stream_from_model(model);
            uncompressed_model_size = decrypted.size();
            stream = codec_compress_data(decrypted, lane_codec);
        } else if (level == 2) {
            const std::vector<uint8_t> raw = make_template_iv32_model(model);
            uncompressed_model_size = raw.size();
            stream = codec_compress_data(raw, lane_codec);
        } else if (typed_level3) {
            const TypedLevel3StreamBuild typed = build_typed_level3_stream(model, *typed_policy);
            uncompressed_model_size = typed.raw_size;
            stream = typed.stream;
            typed_substreams = typed.substreams;
        } else {
            const std::vector<uint8_t> raw = make_template_exception_model(model);
            uncompressed_model_size = raw.size();
            stream = codec_compress_data(raw, lane_codec);
        }

        Spc3TableEntry entry;
        entry.lane = model.metrics.lane;
        entry.level = level;
        entry.stream_kind = typed_level3 ? kSpc3StreamKindTypedLevel3 : level;
        entry.flags = typed_level3 ? 0 : pack_entry_codec_flags(lane_codec);
        entry.source_zip_size = model.metrics.zip_size_bytes;
        entry.source_zip_crc32 = model.zip_crc32;
        entry.source_zip_fnv64 = model.zip_fnv64;
        entry.original_payload_crc32 = model.encrypted_crc32;
        entry.uncompressed_model_size = uncompressed_model_size;
        entry.predictor_matches = model.metrics.predictor_matches;
        entry.predictor_exceptions = model.metrics.predictor_exceptions;
        if (typed_level3) {
            entry.typed_level3 = true;
            entry.typed_substreams = typed_substreams;
        }

        lane_streams.push_back(std::move(stream));
        result.entries.push_back(entry);
    }

    const uint64_t predictor_offset = kSpc3HeaderSize;
    const uint64_t predictor_size = predictor_stream.size();
    const uint64_t table_offset = checked_add_u64(predictor_offset, predictor_size, "SPC3 predictor/table offset");
    const uint64_t table_size = checked_mul_u64(model_count, kSpc3TableEntrySize, "SPC3 table size");
    const uint64_t data_offset = checked_add_u64(table_offset, table_size, "SPC3 table/data offset");
    uint64_t data_size = 0;
    for (const auto& stream : lane_streams) {
        data_size = checked_add_u64(data_size, stream.size(), "SPC3 data size");
    }
    const uint64_t total_size = checked_add_u64(data_offset, data_size, "SPC3 file size");

    result.bytes.reserve(checked_u64_to_size(total_size, "SPC3 file size"));
    result.bytes.insert(result.bytes.end(), {'S', 'P', 'C', '3'});
    append_u32(result.bytes, typed_level3 ? kSpc3VersionV2 : kSpc3VersionV1);
    append_u32(result.bytes, level);
    append_u32(result.bytes, static_cast<uint32_t>(model_count));
    append_u32(result.bytes, kExpectedRecords);
    append_u32(result.bytes, kRecordSize);
    append_u32(result.bytes, level == 3 && embed_predictor ? kSpc3FlagPredictorEmbedded : 0);
    append_u32(result.bytes, kSpc3HeaderSize);
    append_u64(result.bytes, predictor_offset);
    append_u64(result.bytes, predictor_size);
    append_u64(result.bytes, table_offset);
    append_u64(result.bytes, kSpc3TableEntrySize);
    append_u64(result.bytes, data_offset);
    append_u64(result.bytes, data_size);
    if (result.bytes.size() != kSpc3HeaderSize) {
        throw std::runtime_error("internal SPC3 header size mismatch");
    }
    result.bytes.insert(result.bytes.end(), predictor_stream.begin(), predictor_stream.end());

    const size_t table_begin = result.bytes.size();
    result.bytes.resize(result.bytes.size() + checked_u64_to_size(table_size, "SPC3 table size"), 0);
    uint64_t stream_offset = data_offset;
    for (size_t i = 0; i < result.entries.size(); ++i) {
        Spc3TableEntry& entry = result.entries[i];
        entry.stream_offset = stream_offset;
        entry.stream_size = lane_streams[i].size();
        const std::vector<uint8_t> rebuilt = rebuild_payload_from_stream_data(
            lane_streams[i].data(),
            lane_streams[i].size(),
            entry,
            predictor ? *predictor : PredictorTable{});
        entry.rebuilt_payload_crc32 = crc32_vector(rebuilt);
        if (rebuilt.size() != models[i].encrypted.size() ||
            std::memcmp(rebuilt.data(), models[i].encrypted.data(), kPayloadSize) != 0) {
            ++result.roundtrip_mismatches;
        }
        stream_offset = checked_add_u64(stream_offset, lane_streams[i].size(), "SPC3 stream layout");

        const size_t pos = table_begin + i * static_cast<size_t>(kSpc3TableEntrySize);
        patch_u32(result.bytes, pos + 0, entry.lane);
        patch_u32(result.bytes, pos + 4, entry.level);
        patch_u32(result.bytes, pos + 8, entry.stream_kind);
        patch_u32(result.bytes, pos + 12, entry.flags);
        patch_u64(result.bytes, pos + 16, entry.source_zip_size);
        patch_u64(result.bytes, pos + 24, entry.source_zip_crc32);
        patch_u64(result.bytes, pos + 32, entry.source_zip_fnv64);
        patch_u64(result.bytes, pos + 40, entry.original_payload_crc32);
        patch_u64(result.bytes, pos + 48, entry.rebuilt_payload_crc32);
        patch_u64(result.bytes, pos + 56, entry.stream_offset);
        patch_u64(result.bytes, pos + 64, entry.stream_size);
        patch_u64(result.bytes, pos + 72, entry.uncompressed_model_size);
        patch_u64(result.bytes, pos + 80, entry.predictor_matches);
        patch_u64(result.bytes, pos + 88, entry.predictor_exceptions);
    }

    for (const auto& stream : lane_streams) {
        result.bytes.insert(result.bytes.end(), stream.begin(), stream.end());
    }
    result.build_ms = watch.elapsed_ms();
    return result;
}

void parse_typed_level3_substreams(
    const std::vector<uint8_t>& bytes,
    Spc3TableEntry& entry)
{
    if (!entry_is_typed_level3(entry)) {
        return;
    }
    const uint64_t substream_table_size =
        kSpc3TypedLevel3SubstreamCount * kSpc3TypedLevel3SubstreamEntrySize;
    if (entry.stream_size < substream_table_size) {
        throw std::runtime_error("typed level 3 stream is missing substream table");
    }

    std::array<bool, kSpc3TypedLevel3SubstreamCount + 1> seen{};
    uint64_t expected_offset = substream_table_size;
    uint64_t raw_total = 0;
    for (size_t i = 0; i < kSpc3TypedLevel3SubstreamCount; ++i) {
        const uint64_t table_offset = checked_add_u64(
            entry.stream_offset,
            static_cast<uint64_t>(i) * kSpc3TypedLevel3SubstreamEntrySize,
            "typed level 3 substream table offset");
        const size_t pos = checked_offset(bytes, table_offset, kSpc3TypedLevel3SubstreamEntrySize,
            "typed level 3 substream table");

        Spc3TypedSubstreamEntry sub;
        sub.kind = read_u32(bytes, pos + 0);
        sub.flags = read_u32(bytes, pos + 4);
        sub.offset = read_u64(bytes, pos + 8);
        sub.stream_size = read_u64(bytes, pos + 16);
        sub.raw_size = read_u64(bytes, pos + 24);

        if (sub.kind < kSpc3TypedSubstreamTemplate || sub.kind > kSpc3TypedSubstreamValues ||
            seen[sub.kind]) {
            throw std::runtime_error("typed level 3 substream kind is invalid");
        }
        seen[sub.kind] = true;
        (void)codec_from_entry_flags(sub.flags, 3, true);
        if (sub.offset != expected_offset) {
            throw std::runtime_error("typed level 3 substream layout has gap or overlap");
        }
        const uint64_t sub_end = checked_add_u64(sub.offset, sub.stream_size, "typed level 3 substream end");
        if (sub_end > entry.stream_size) {
            throw std::runtime_error("typed level 3 substream outside lane stream");
        }
        const uint64_t file_offset = checked_add_u64(
            entry.stream_offset,
            sub.offset,
            "typed level 3 substream file offset");
        (void)checked_offset(bytes, file_offset, sub.stream_size, "typed level 3 substream bytes");

        if (sub.kind == kSpc3TypedSubstreamTemplate && sub.raw_size != kRecordSize) {
            throw std::runtime_error("typed level 3 template raw size is invalid");
        }
        if (sub.kind == kSpc3TypedSubstreamBitmap && sub.raw_size != kLevel3ExceptionBitmapBytes) {
            throw std::runtime_error("typed level 3 bitmap raw size is invalid");
        }
        if (sub.kind == kSpc3TypedSubstreamValues &&
            (sub.raw_size > kLevel3ExceptionValueMaxBytes || sub.raw_size % 4ULL != 0)) {
            throw std::runtime_error("typed level 3 value raw size is invalid");
        }

        entry.typed_substreams[i] = sub;
        raw_total = checked_add_u64(raw_total, sub.raw_size, "typed level 3 raw size total");
        expected_offset = sub_end;
    }
    if (expected_offset != entry.stream_size) {
        throw std::runtime_error("typed level 3 stream has trailing bytes");
    }
    if (!seen[kSpc3TypedSubstreamTemplate] || !seen[kSpc3TypedSubstreamBitmap] ||
        !seen[kSpc3TypedSubstreamValues]) {
        throw std::runtime_error("typed level 3 substream table is incomplete");
    }
    if (raw_total != entry.uncompressed_model_size) {
        throw std::runtime_error("typed level 3 uncompressed size mismatch");
    }
    entry.typed_level3 = true;
}

Spc3Container parse_spc3_file(const std::vector<uint8_t>& bytes) {
    if (bytes.size() < kSpc3HeaderSize ||
        bytes[0] != 'S' || bytes[1] != 'P' || bytes[2] != 'C' || bytes[3] != '3') {
        throw std::runtime_error("not an SPC3 file");
    }
    Spc3Container container;
    container.version = read_u32(bytes, 4);
    container.level = read_u32(bytes, 8);
    const uint32_t lane_count = read_u32(bytes, 12);
    const uint32_t records_per_lane = read_u32(bytes, 16);
    const uint32_t record_size = read_u32(bytes, 20);
    container.flags = read_u32(bytes, 24);
    const uint32_t header_size = read_u32(bytes, 28);
    container.predictor_offset = read_u64(bytes, 32);
    container.predictor_size = read_u64(bytes, 40);
    container.table_offset = read_u64(bytes, 48);
    container.table_entry_size = read_u64(bytes, 56);
    container.data_offset = read_u64(bytes, 64);
    container.data_size = read_u64(bytes, 72);

    if ((container.version != kSpc3VersionV1 && container.version != kSpc3VersionV2) ||
        header_size != kSpc3HeaderSize ||
        records_per_lane != kExpectedRecords || record_size != kRecordSize ||
        container.table_entry_size != kSpc3TableEntrySize || container.level > 3) {
        throw std::runtime_error("unsupported SPC3 header");
    }
    if (lane_count == 0) {
        throw std::runtime_error("SPC3 file has no lane table entries");
    }
    if ((container.flags & ~kSpc3KnownFlags) != 0) {
        throw std::runtime_error("unsupported SPC3 flags");
    }

    const bool predictor_embedded = (container.flags & kSpc3FlagPredictorEmbedded) != 0;
    if (container.predictor_offset != kSpc3HeaderSize) {
        throw std::runtime_error("SPC3 predictor stream is not adjacent to header");
    }
    if (predictor_embedded) {
        if (container.level != 3) {
            throw std::runtime_error("embedded predictor is only valid for level 3");
        }
        if (container.predictor_size == 0) {
            throw std::runtime_error("embedded predictor has no data");
        }
    } else if (container.predictor_size != 0) {
        throw std::runtime_error("SPC3 predictor stream present without embedded flag");
    }

    if (lane_count > std::numeric_limits<uint64_t>::max() / kSpc3TableEntrySize) {
        throw std::runtime_error("SPC3 table size overflows uint64");
    }
    const uint64_t table_size = static_cast<uint64_t>(lane_count) * kSpc3TableEntrySize;
    const uint64_t expected_table_offset = checked_add_u64(
        container.predictor_offset,
        container.predictor_size,
        "SPC3 predictor/table offset");
    if (container.table_offset != expected_table_offset) {
        throw std::runtime_error("SPC3 table is not adjacent to predictor stream");
    }
    const uint64_t expected_data_offset = checked_add_u64(
        container.table_offset,
        table_size,
        "SPC3 table/data offset");
    if (container.data_offset != expected_data_offset) {
        throw std::runtime_error("SPC3 data section is not adjacent to lane table");
    }
    const uint64_t expected_file_size = checked_add_u64(
        container.data_offset,
        container.data_size,
        "SPC3 file size");
    if (expected_file_size < bytes.size()) {
        throw std::runtime_error("SPC3 file has trailing bytes");
    }
    if (expected_file_size > bytes.size()) {
        throw std::runtime_error("SPC3 data section is truncated");
    }

    (void)checked_offset(bytes, container.predictor_offset, container.predictor_size, "SPC3 predictor stream");
    (void)checked_offset(bytes, container.table_offset, table_size, "SPC3 table");
    (void)checked_offset(bytes, container.data_offset, container.data_size, "SPC3 data section");

    if (predictor_embedded) {
        const size_t predictor_pos = static_cast<size_t>(container.predictor_offset);
        const std::vector<uint8_t> predictor_raw = zlib_decompress_exact(
            bytes.data() + predictor_pos,
            static_cast<size_t>(container.predictor_size),
            kExpectedRecords * 4,
            "embedded predictor");
        container.predictor = predictor_from_raw(predictor_raw);
    }

    container.entries.reserve(lane_count);
    uint64_t expected_stream_offset = container.data_offset;
    const uint64_t data_end = checked_add_u64(container.data_offset, container.data_size, "SPC3 data end");
    for (uint32_t i = 0; i < lane_count; ++i) {
        const uint64_t table_entry_offset = checked_add_u64(
            container.table_offset,
            static_cast<uint64_t>(i) * kSpc3TableEntrySize,
            "SPC3 table entry offset");
        const size_t pos = checked_offset(bytes, table_entry_offset, kSpc3TableEntrySize, "SPC3 table entry");
        Spc3TableEntry entry;
        entry.lane = read_u32(bytes, pos + 0);
        entry.level = read_u32(bytes, pos + 4);
        entry.stream_kind = read_u32(bytes, pos + 8);
        entry.flags = read_u32(bytes, pos + 12);
        entry.source_zip_size = read_u64(bytes, pos + 16);
        entry.source_zip_crc32 = read_u64(bytes, pos + 24);
        entry.source_zip_fnv64 = read_u64(bytes, pos + 32);
        entry.original_payload_crc32 = read_u64(bytes, pos + 40);
        entry.rebuilt_payload_crc32 = read_u64(bytes, pos + 48);
        entry.stream_offset = read_u64(bytes, pos + 56);
        entry.stream_size = read_u64(bytes, pos + 64);
        entry.uncompressed_model_size = read_u64(bytes, pos + 72);
        entry.predictor_matches = read_u64(bytes, pos + 80);
        entry.predictor_exceptions = read_u64(bytes, pos + 88);
        const bool typed_level3_entry =
            entry.level == 3 && entry.stream_kind == kSpc3StreamKindTypedLevel3;
        if (entry.lane > 0xFFFF || entry.level != container.level) {
            throw std::runtime_error("bad SPC3 table entry");
        }
        if (typed_level3_entry) {
            if (container.version < kSpc3VersionV2 || entry.flags != 0) {
                throw std::runtime_error("bad SPC3 typed level 3 table entry");
            }
        } else {
            if (entry.stream_kind != entry.level) {
                throw std::runtime_error("bad SPC3 table entry");
            }
            (void)codec_from_entry_flags(entry.flags, entry.level);
        }
        if (entry.original_payload_crc32 > 0xFFFFFFFFULL || entry.rebuilt_payload_crc32 > 0xFFFFFFFFULL ||
            entry.source_zip_crc32 > 0xFFFFFFFFULL) {
            throw std::runtime_error("SPC3 table CRC field is out of range");
        }
        if (entry.stream_offset != expected_stream_offset) {
            throw std::runtime_error("SPC3 stream layout has gap or overlap");
        }
        const uint64_t stream_end = checked_add_u64(entry.stream_offset, entry.stream_size, "SPC3 stream end");
        if (entry.stream_offset < container.data_offset || stream_end > data_end) {
            throw std::runtime_error("SPC3 table stream outside data section");
        }
        if (entry.level == 0 && (entry.stream_size != kPayloadSize ||
            entry.uncompressed_model_size != kPayloadSize)) {
            throw std::runtime_error("level 0 table sizes are invalid");
        }
        if (entry.level == 1 && entry.uncompressed_model_size != kPayloadSize) {
            throw std::runtime_error("level 1 table uncompressed size is invalid");
        }
        if (entry.level == 2 && entry.uncompressed_model_size != kRecordSize + kExpectedRecords * 4ULL) {
            throw std::runtime_error("level 2 table uncompressed size is invalid");
        }
        if (entry.level == 3) {
            if (entry.uncompressed_model_size < kLevel3ModelMinSize ||
                entry.uncompressed_model_size > kLevel3ModelMaxSize ||
                ((entry.uncompressed_model_size - kLevel3ModelMinSize) % 4ULL) != 0) {
                throw std::runtime_error("level 3 table uncompressed size is invalid");
            }
        }
        (void)checked_offset(bytes, entry.stream_offset, entry.stream_size, "SPC3 table stream");
        if (typed_level3_entry) {
            parse_typed_level3_substreams(bytes, entry);
        }
        expected_stream_offset = stream_end;
        container.entries.push_back(entry);
    }
    if (expected_stream_offset != data_end) {
        throw std::runtime_error("SPC3 data section has trailing stream bytes");
    }
    return container;
}

struct Spc3DecodedLane {
    uint32_t lane = 0;
    std::vector<uint8_t> payload;
    uint32_t payload_crc32 = 0;
};

struct Spc3DecodedLaneSink {
    void* user = nullptr;
    void (*emit)(
        void* user,
        size_t lane_index,
        uint16_t lane,
        const uint8_t* payload,
        size_t payload_size,
        uint32_t payload_crc32) = nullptr;
};

void decode_spc3_lanes_streaming(
    const std::vector<uint8_t>& bytes,
    const Spc3Container& container,
    CpuDecodeProfile* profile,
    std::vector<Spc3DecodedLane>* decoded_lanes,
    const Spc3DecodedLaneSink* lane_sink);

void emit_decoded_lane(
    const Spc3DecodedLaneSink* sink,
    size_t lane_index,
    uint16_t lane,
    const std::vector<uint8_t>& payload,
    uint32_t payload_crc32)
{
    if (sink != nullptr && sink->emit != nullptr) {
        sink->emit(
            sink->user,
            lane_index,
            lane,
        payload.data(),
        payload.size(),
        payload_crc32);
    }
}

struct GpuLevel3LaneInput {
    uint16_t lane = 0;
    std::array<uint8_t, kRecordSize> template_record{};
    std::vector<uint8_t> exception_bitmap;
    std::vector<uint8_t> exception_values;
};

struct LaneSinkOffsetState {
    const Spc3DecodedLaneSink* sink = nullptr;
    size_t lane_index_offset = 0;
};

void emit_decoded_lane_with_offset(
    void* user,
    size_t lane_index,
    uint16_t lane,
    const uint8_t* payload,
    size_t payload_size,
    uint32_t payload_crc32)
{
    const LaneSinkOffsetState* state = static_cast<LaneSinkOffsetState*>(user);
    if (state == nullptr || state->sink == nullptr || state->sink->emit == nullptr) {
        return;
    }
    state->sink->emit(
        state->sink->user,
        lane_index + state->lane_index_offset,
        lane,
        payload,
        payload_size,
        payload_crc32);
}

uint64_t estimate_gpu_level3_lane_bytes(const GpuLevel3LaneInput& input) {
    uint64_t bytes = checked_add_u64(
        kRecordSize,
        kLevel3ExceptionBitmapBytes,
        "GPU level-3 input estimate (template + bitmap)");
    bytes = checked_add_u64(bytes, input.exception_values.size(), "GPU level-3 input estimate values");
    bytes = checked_add_u64(bytes, sizeof(uint16_t), "GPU level-3 input lane id");
    bytes = checked_add_u64(bytes, sizeof(uint32_t), "GPU level-3 input estimate value offset");
    bytes = checked_add_u64(
        bytes,
        checked_mul_u64(kExpectedRecords / 8ULL + 1ULL, sizeof(uint32_t), "GPU level-3 input estimate prefixes"),
        "GPU level-3 input estimate prefixes");
    bytes = checked_add_u64(bytes, kPayloadSize, "GPU level-3 input estimate output");
    return bytes;
}

struct GpuOffloadBenchResult {
    std::string status = "not_requested";
    std::string backend = "cuda_driver_nvrtc";
    std::string device_name;
    std::string compare_mode = "none";
    std::string fallback_reason;
    std::string download_mode = "none";
    bool used = false;
    bool runtime_cache_hit = false;
    bool runtime_failure_cached = false;
    uint32_t lane_count = 0;
    uint64_t runtime_initializations = 0;
    uint64_t output_bytes = 0;
    uint64_t value_count = 0;
    uint64_t mismatched_lanes = 0;
    uint64_t mismatched_bytes = 0;
    double upload_ms = 0;
    double compile_ms = 0;
    double kernel_ms = 0;
    double download_ms = 0;
    double host_crc_ms = 0;
    double compare_ms = 0;
    double total_ms = 0;
};

GpuLevel3LaneInput make_gpu_level3_input_from_model(const LaneModel& model) {
    GpuLevel3LaneInput input;
    input.lane = model.metrics.lane;
    input.template_record = model.base_template;
    input.exception_bitmap = model.exception_bitmap;
    input.exception_values = model.exception_values;
    return input;
}

std::vector<Spc3DecodedLane> decode_spc3_lanes(
    const std::vector<uint8_t>& bytes,
    const Spc3Container& container,
    CpuDecodeProfile* profile = nullptr)
{
    std::vector<Spc3DecodedLane> lanes;
    lanes.reserve(container.entries.size());
    // For legacy callers that expect materialized payloads, keep the full return
    // vector. GPU-capable callers should prefer the callback sink overload for
    // peak-memory safety.
    decode_spc3_lanes_streaming(bytes, container, profile, &lanes, nullptr);
    return lanes;
}

void decode_spc3_lanes_streaming(
    const std::vector<uint8_t>& bytes,
    const Spc3Container& container,
    CpuDecodeProfile* profile,
    std::vector<Spc3DecodedLane>* decoded_lanes,
    const Spc3DecodedLaneSink* lane_sink)
{
    CpuDecodeProfile local_profile;
    CpuDecodeProfile* active_profile = profile != nullptr ? profile : &local_profile;
    *active_profile = CpuDecodeProfile{};
    active_profile->used = true;
    active_profile->lane_count = static_cast<uint32_t>(container.entries.size());
    Stopwatch total_watch;

    if (decoded_lanes != nullptr) {
        decoded_lanes->clear();
        decoded_lanes->reserve(container.entries.size());
    }
    size_t lane_index = 0;
    for (const auto& entry : container.entries) {
        if (entry_is_typed_level3(entry)) {
            ++active_profile->typed_lanes;
        } else {
            ++active_profile->legacy_lanes;
        }
        Spc3DecodedLane lane;
        lane.lane = entry.lane;
        lane.payload = rebuild_payload_from_spc3_stream(bytes, entry, container.predictor, active_profile);
        active_profile->crc_bytes += lane.payload.size();
        {
            ScopedTimer timer(active_profile->crc_ms);
            lane.payload_crc32 = crc32_vector(lane.payload);
        }
        emit_decoded_lane(
            lane_sink,
            lane_index,
            static_cast<uint16_t>(entry.lane),
            lane.payload,
            lane.payload_crc32);
        if (decoded_lanes != nullptr) {
            decoded_lanes->push_back(std::move(lane));
        }
        ++lane_index;
    }
    active_profile->total_ms = total_watch.elapsed_ms();
}

std::vector<Spc3DecodedLane> decode_spc3_lanes_with_optional_gpu(
    const std::vector<uint8_t>& bytes,
    const Spc3Container& container,
    const Options& options,
    GpuOffloadBenchResult* gpu_result,
    CpuDecodeProfile* cpu_profile,
    const Spc3DecodedLaneSink* lane_sink = nullptr);

void write_gpu_result_json(std::ostream& out, const GpuOffloadBenchResult& gpu) {
    const bool requested = gpu.status != "not_requested";
    const std::string fallback_reason =
        !gpu.fallback_reason.empty() ? gpu.fallback_reason :
        (!gpu.used && requested ? gpu.status : std::string{});
    out << "{"
        << "\"requested\": " << (requested ? "true" : "false")
        << ", \"used\": " << (gpu.used ? "true" : "false")
        << ", \"status\": \"" << json_escape(gpu.status)
        << "\", \"backend\": \"" << json_escape(gpu.backend)
        << "\", \"device_name\": \"" << json_escape(gpu.device_name)
        << "\", \"compare_mode\": \"" << json_escape(gpu.compare_mode)
        << "\", \"fallback_reason\": \"" << json_escape(fallback_reason)
        << "\", \"download_mode\": \"" << json_escape(gpu.download_mode)
        << "\", \"runtime_cache_hit\": " << (gpu.runtime_cache_hit ? "true" : "false")
        << ", \"runtime_failure_cached\": " << (gpu.runtime_failure_cached ? "true" : "false")
        << ", \"runtime_initializations\": " << gpu.runtime_initializations
        << ", \"lane_count\": " << gpu.lane_count
        << ", \"output_bytes\": " << gpu.output_bytes
        << ", \"value_count\": " << gpu.value_count
        << ", \"mismatched_lanes\": " << gpu.mismatched_lanes
        << ", \"mismatched_bytes\": " << gpu.mismatched_bytes
        << ", \"compile_ms\": " << gpu.compile_ms
        << ", \"upload_ms\": " << gpu.upload_ms
        << ", \"kernel_ms\": " << gpu.kernel_ms
        << ", \"download_ms\": " << gpu.download_ms
        << ", \"host_crc_ms\": " << gpu.host_crc_ms
        << ", \"compare_ms\": " << gpu.compare_ms
        << ", \"total_ms\": " << gpu.total_ms
        << "}";
}

void write_cpu_decode_profile_json(std::ostream& out, const CpuDecodeProfile& profile) {
    out << "{"
        << "\"used\": " << (profile.used ? "true" : "false")
        << ", \"backend\": \"" << json_escape(profile.backend)
        << "\", \"crc_backend\": \"" << json_escape(profile.crc_backend)
        << "\", \"lane_count\": " << profile.lane_count
        << ", \"typed_lanes\": " << profile.typed_lanes
        << ", \"legacy_lanes\": " << profile.legacy_lanes
        << ", \"crc_bytes\": " << profile.crc_bytes
        << ", \"stream_decode_ms\": " << profile.stream_decode_ms
        << ", \"iv_expand_ms\": " << profile.iv_expand_ms
        << ", \"rebuild_encrypt_ms\": " << profile.rebuild_encrypt_ms
        << ", \"crc_ms\": " << profile.crc_ms
        << ", \"total_ms\": " << profile.total_ms
        << "}";
}

std::string cpu_decode_largest_slice(const CpuDecodeProfile& profile, double& ms) {
    ms = profile.stream_decode_ms;
    std::string slice = "stream_decode";
    if (profile.iv_expand_ms > ms) {
        ms = profile.iv_expand_ms;
        slice = "iv_expand";
    }
    if (profile.rebuild_encrypt_ms > ms) {
        ms = profile.rebuild_encrypt_ms;
        slice = "rebuild_encrypt";
    }
    if (profile.crc_ms > ms) {
        ms = profile.crc_ms;
        slice = "crc";
    }
    return slice;
}

void write_asm_recommendation_json(std::ostream& out, const CpuDecodeProfile& profile) {
    double largest_ms = 0;
    const std::string largest_slice = cpu_decode_largest_slice(profile, largest_ms);
    std::string decision = "profile_required_before_new_asm";
    std::string next_action = "run CPU typed decode profile before choosing the next assembly target";
    if (profile.used) {
        if (largest_slice == "crc") {
            decision = "crc_is_next_profiled_target";
            next_action = "evaluate CRC reduction or a same-polynomial CRC32 acceleration path before more PK3 assembly";
        } else if (largest_slice == "rebuild_encrypt") {
            decision = "pk3_rebuild_encrypt_is_candidate";
            next_action = "extend the targeted PK3 shuffle/rebuild assembly path if this repeats on a real gate";
        } else if (largest_slice == "iv_expand") {
            decision = "iv_expand_is_candidate";
            next_action = "extend IV expansion assembly only if this remains the largest real-gate slice";
        } else {
            decision = "stream_decode_is_candidate";
            next_action = "review codec stream decode before adding more PK3-specific assembly";
        }
    }
    out << "{"
        << "\"policy\": \"targeted_asm_unpaused_profile_guided\""
        << ", \"implemented_target\": \"pk3_shuffle48_x86_64_asm\""
        << ", \"profile_used\": " << (profile.used ? "true" : "false")
        << ", \"crc_backend\": \"" << json_escape(profile.crc_backend)
        << "\", \"largest_slice\": \"" << json_escape(profile.used ? largest_slice : "unknown")
        << "\", \"largest_slice_ms\": " << (profile.used ? largest_ms : 0.0)
        << ", \"decision\": \"" << json_escape(decision)
        << "\", \"next_action\": \"" << json_escape(next_action)
        << "\"}";
}

bool spc3_predictor_embedded(const Spc3Container& container) {
    return (container.flags & kSpc3FlagPredictorEmbedded) != 0;
}

void ensure_spc3_predictor_for_decode(Spc3Container& container, const Options& options) {
    if (container.level == 3 && !container.predictor.loaded) {
        container.predictor = load_predictor_table(options.predictor);
    }
}

// Legacy reporter for the in-RAM build_spc3_file path. Unused by streaming pack
// (run_pack_mode now calls build_pack_report_json_streaming) but kept available
// for any caller that still builds via build_spc3_file (e.g. bench mode).
[[maybe_unused]] std::string build_pack_report_json(
    const Options& options,
    const Spc3BuildResult& built,
    const std::vector<LaneModel>& models,
    size_t model_count)
{
    uint64_t source_zip_bytes = 0;
    uint64_t raw_payload_bytes = 0;
    for (size_t i = 0; i < model_count; ++i) {
        source_zip_bytes += models[i].metrics.zip_size_bytes;
        raw_payload_bytes += models[i].encrypted.size();
    }

    std::ostringstream out;
    out << std::fixed << std::setprecision(3);
    out << "{\n";
    out << "  \"schema\": \"spc3_pack_report.v1\",\n";
    out << "  \"mode\": \"pack\",\n";
    out << "  \"ok\": " << (built.roundtrip_mismatches == 0 ? "true" : "false") << ",\n";
    out << "  \"output\": \"" << json_escape(options.output.string()) << "\",\n";
    out << "  \"root\": \"" << json_escape(options.root.string()) << "\",\n";
    out << "  \"limit_zips\": " << options.limit_zips << ",\n";
    out << "  \"all_zips\": " << (options.all_zips ? "true" : "false") << ",\n";
    out << "  \"level\": " << options.level << ",\n";
    out << "  \"codec_profile\": \"" << json_escape(codec_profile_name(options.codec_profile)) << "\",\n";
    out << "  \"codec_profile_set\": " << (options.codec_profile_set ? "true" : "false") << ",\n";
    out << "  \"codec_policy\": \"auto remains compat/zlib-9; use --codec-profile fast for v0.2 typed zstd-9 or --codec-profile small for LZMA2-9\",\n";
    if (!built.entries.empty()) {
        if (entry_is_typed_level3(built.entries.front())) {
            out << "  \"codec\": \"typed-level3\",\n";
            out << "  \"codec_level\": 0,\n";
            out << "  \"codec_settings\": 0,\n";
        } else {
            const CodecSpec codec = codec_from_entry_flags(built.entries.front().flags, built.entries.front().level);
            out << "  \"codec\": \"" << codec_name(codec.id) << "\",\n";
            out << "  \"codec_level\": " << codec.level << ",\n";
            out << "  \"codec_settings\": " << codec.settings << ",\n";
        }
    }
    out << "  \"version\": " << (built.entries.empty() || !entry_is_typed_level3(built.entries.front())
        ? kSpc3VersionV1 : kSpc3VersionV2) << ",\n";
    out << "  \"typed_level3\": " << (options.typed_level3 ? "true" : "false") << ",\n";
    out << "  \"predictor_embedded\": " << (options.level == 3 && !options.external_predictor ? "true" : "false") << ",\n";
    out << "  \"external_predictor_required\": " << (options.level == 3 && options.external_predictor ? "true" : "false") << ",\n";
    out << "  \"lane_count\": " << model_count << ",\n";
    out << "  \"spc3_size_bytes\": " << built.bytes.size() << ",\n";
    out << "  \"source_zip_bytes\": " << source_zip_bytes << ",\n";
    out << "  \"raw_payload_bytes\": " << raw_payload_bytes << ",\n";
    out << "  \"roundtrip_mismatches\": " << built.roundtrip_mismatches << ",\n";
    out << "  \"build_ms\": " << built.build_ms << ",\n";
    out << "  \"lanes\": [\n";
    for (size_t i = 0; i < built.entries.size(); ++i) {
        const auto& entry = built.entries[i];
        const bool typed = entry_is_typed_level3(entry);
        out << "    {\"lane\": \"" << hex4(entry.lane) << "\", "
            << "\"stream_kind\": \"" << stream_kind_name(entry) << "\", ";
        if (typed) {
            out << "\"codec\": \"typed-level3\", "
                << "\"codec_level\": 0, "
                << "\"codec_settings\": 0, ";
        } else {
            const CodecSpec codec = codec_from_entry_flags(entry.flags, entry.level);
            out << "\"codec\": \"" << codec_name(codec.id) << "\", "
                << "\"codec_level\": " << codec.level << ", "
                << "\"codec_settings\": " << codec.settings << ", ";
        }
        out
            << "\"source_zip_size\": " << entry.source_zip_size << ", "
            << "\"source_zip_crc32\": " << entry.source_zip_crc32 << ", "
            << "\"source_zip_fnv1a64\": " << entry.source_zip_fnv64 << ", "
            << "\"payload_crc32\": " << entry.original_payload_crc32 << ", "
            << "\"rebuilt_payload_crc32\": " << entry.rebuilt_payload_crc32 << ", "
            << "\"stream_size\": " << entry.stream_size << ", "
            << "\"uncompressed_model_size\": " << entry.uncompressed_model_size << ", "
            << "\"predictor_exceptions\": " << entry.predictor_exceptions;
        if (typed) {
            out << ", \"typed_substreams\": [";
            for (size_t j = 0; j < entry.typed_substreams.size(); ++j) {
                const auto& sub = entry.typed_substreams[j];
                const CodecSpec sub_codec = codec_from_entry_flags(sub.flags, 3, true);
                out << "{\"kind\": \"" << typed_substream_name(sub.kind)
                    << "\", \"codec\": \"" << codec_name(sub_codec.id)
                    << "\", \"codec_level\": " << sub_codec.level
                    << ", \"offset\": " << sub.offset
                    << ", \"stream_size\": " << sub.stream_size
                    << ", \"raw_size\": " << sub.raw_size << "}"
                    << (j + 1 == entry.typed_substreams.size() ? "" : ", ");
            }
            out << "]";
        }
        out << "}"
            << (i + 1 == built.entries.size() ? "\n" : ",\n");
    }
    out << "  ]\n";
    out << "}\n";
    return out.str();
}

// Streaming variant of build_pack_report_json. Identical schema/field shape; reads
// everything from the StreamingPackResult instead of LaneModels.
std::string build_pack_report_json_streaming(
    const Options& options,
    const StreamingPackResult& result)
{
    std::ostringstream out;
    out << std::fixed << std::setprecision(3);
    out << "{\n";
    out << "  \"schema\": \"spc3_pack_report.v1\",\n";
    out << "  \"mode\": \"pack\",\n";
    out << "  \"ok\": " << (result.roundtrip_mismatches == 0 ? "true" : "false") << ",\n";
    out << "  \"output\": \"" << json_escape(options.output.string()) << "\",\n";
    out << "  \"root\": \"" << json_escape(options.root.string()) << "\",\n";
    out << "  \"limit_zips\": " << options.limit_zips << ",\n";
    out << "  \"all_zips\": " << (options.all_zips ? "true" : "false") << ",\n";
    out << "  \"level\": " << options.level << ",\n";
    out << "  \"codec_profile\": \"" << json_escape(codec_profile_name(options.codec_profile)) << "\",\n";
    out << "  \"codec_profile_set\": " << (options.codec_profile_set ? "true" : "false") << ",\n";
    out << "  \"codec_policy\": \"auto remains compat/zlib-9; use --codec-profile fast for v0.2 typed zstd-9 or --codec-profile small for LZMA2-9\",\n";
    if (!result.entries.empty()) {
        if (entry_is_typed_level3(result.entries.front())) {
            out << "  \"codec\": \"typed-level3\",\n";
            out << "  \"codec_level\": 0,\n";
            out << "  \"codec_settings\": 0,\n";
        } else {
            const CodecSpec codec = codec_from_entry_flags(result.entries.front().flags, result.entries.front().level);
            out << "  \"codec\": \"" << codec_name(codec.id) << "\",\n";
            out << "  \"codec_level\": " << codec.level << ",\n";
            out << "  \"codec_settings\": " << codec.settings << ",\n";
        }
    }
    out << "  \"version\": " << (result.entries.empty() || !entry_is_typed_level3(result.entries.front())
        ? kSpc3VersionV1 : kSpc3VersionV2) << ",\n";
    out << "  \"typed_level3\": " << (options.typed_level3 ? "true" : "false") << ",\n";
    out << "  \"predictor_embedded\": " << (options.level == 3 && !options.external_predictor ? "true" : "false") << ",\n";
    out << "  \"external_predictor_required\": " << (options.level == 3 && options.external_predictor ? "true" : "false") << ",\n";
    out << "  \"lane_count\": " << result.entries.size() << ",\n";
    out << "  \"spc3_size_bytes\": " << result.total_size << ",\n";
    out << "  \"source_zip_bytes\": " << result.source_zip_bytes << ",\n";
    out << "  \"raw_payload_bytes\": " << result.raw_payload_bytes << ",\n";
    out << "  \"roundtrip_mismatches\": " << result.roundtrip_mismatches << ",\n";
    out << "  \"build_ms\": " << result.build_ms << ",\n";
    out << "  \"lanes\": [\n";
    for (size_t i = 0; i < result.entries.size(); ++i) {
        const auto& entry = result.entries[i];
        const bool typed = entry_is_typed_level3(entry);
        out << "    {\"lane\": \"" << hex4(entry.lane) << "\", "
            << "\"stream_kind\": \"" << stream_kind_name(entry) << "\", ";
        if (typed) {
            out << "\"codec\": \"typed-level3\", "
                << "\"codec_level\": 0, "
                << "\"codec_settings\": 0, ";
        } else {
            const CodecSpec codec = codec_from_entry_flags(entry.flags, entry.level);
            out << "\"codec\": \"" << codec_name(codec.id) << "\", "
                << "\"codec_level\": " << codec.level << ", "
                << "\"codec_settings\": " << codec.settings << ", ";
        }
        out
            << "\"source_zip_size\": " << entry.source_zip_size << ", "
            << "\"source_zip_crc32\": " << entry.source_zip_crc32 << ", "
            << "\"source_zip_fnv1a64\": " << entry.source_zip_fnv64 << ", "
            << "\"payload_crc32\": " << entry.original_payload_crc32 << ", "
            << "\"rebuilt_payload_crc32\": " << entry.rebuilt_payload_crc32 << ", "
            << "\"stream_size\": " << entry.stream_size << ", "
            << "\"uncompressed_model_size\": " << entry.uncompressed_model_size << ", "
            << "\"predictor_exceptions\": " << entry.predictor_exceptions;
        if (typed) {
            out << ", \"typed_substreams\": [";
            for (size_t j = 0; j < entry.typed_substreams.size(); ++j) {
                const auto& sub = entry.typed_substreams[j];
                const CodecSpec sub_codec = codec_from_entry_flags(sub.flags, 3, true);
                out << "{\"kind\": \"" << typed_substream_name(sub.kind)
                    << "\", \"codec\": \"" << codec_name(sub_codec.id)
                    << "\", \"codec_level\": " << sub_codec.level
                    << ", \"offset\": " << sub.offset
                    << ", \"stream_size\": " << sub.stream_size
                    << ", \"raw_size\": " << sub.raw_size << "}"
                    << (j + 1 == entry.typed_substreams.size() ? "" : ", ");
            }
            out << "]";
        }
        out << "}"
            << (i + 1 == result.entries.size() ? "\n" : ",\n");
    }
    out << "  ]\n";
    out << "}\n";
    return out.str();
}

struct UnpackOutputReport {
    uint32_t lane = 0;
    fs::path path;
    uint64_t bytes = 0;
    uint32_t payload_crc32 = 0;
    uint32_t output_payload_crc32 = 0;
    uint32_t output_crc32 = 0;
};

template <typename T, size_t N>
void write_json_array(std::ostringstream& out, const std::array<T, N>& values) {
    out << "[";
    for (size_t i = 0; i < N; ++i) {
        if (i != 0) {
            out << ", ";
        }
        out << static_cast<uint32_t>(values[i]);
    }
    out << "]";
}

void write_pk3_edits_json(std::ostringstream& out, const Pk3EditOptions& edits) {
    out << "{";
    bool first = true;
    const auto field_prefix = [&](const char* name) {
        if (!first) {
            out << ", ";
        }
        first = false;
        out << "\"" << name << "\": ";
    };
    if (edits.nickname) {
        field_prefix("nickname");
        out << "\"" << json_escape(*edits.nickname) << "\"";
    }
    if (edits.ot_name) {
        field_prefix("ot_name");
        out << "\"" << json_escape(*edits.ot_name) << "\"";
    }
    if (edits.held_item) {
        field_prefix("held_item");
        out << *edits.held_item;
    }
    if (edits.experience) {
        field_prefix("experience");
        out << *edits.experience;
    }
    if (edits.friendship) {
        field_prefix("friendship");
        out << static_cast<uint32_t>(*edits.friendship);
    }
    if (edits.pokerus) {
        field_prefix("pokerus");
        out << static_cast<uint32_t>(*edits.pokerus);
    }
    if (edits.moves) {
        field_prefix("moves");
        write_json_array(out, *edits.moves);
    }
    if (edits.pp) {
        field_prefix("pp");
        write_json_array(out, *edits.pp);
    }
    if (edits.pp_ups) {
        field_prefix("pp_ups");
        write_json_array(out, *edits.pp_ups);
    }
    if (edits.evs) {
        field_prefix("evs_hp_atk_def_spa_spd_spe");
        write_json_array(out, *edits.evs);
    }
    if (edits.ivs) {
        field_prefix("ivs_hp_atk_def_spa_spd_spe");
        write_json_array(out, *edits.ivs);
    }
    if (edits.contest) {
        field_prefix("contest_cool_beauty_cute_smart_tough_feel");
        write_json_array(out, *edits.contest);
    }
    if (edits.met_location) {
        field_prefix("met_location");
        out << static_cast<uint32_t>(*edits.met_location);
    }
    if (edits.met_level) {
        field_prefix("met_level");
        out << static_cast<uint32_t>(*edits.met_level);
    }
    if (edits.origin_game) {
        field_prefix("origin_game");
        out << static_cast<uint32_t>(*edits.origin_game);
    }
    if (edits.ball) {
        field_prefix("ball");
        out << static_cast<uint32_t>(*edits.ball);
    }
    if (edits.ot_gender) {
        field_prefix("ot_gender");
        out << static_cast<uint32_t>(*edits.ot_gender);
    }
    if (edits.language) {
        field_prefix("language");
        out << static_cast<uint32_t>(*edits.language);
    }
    if (edits.ability_bit) {
        field_prefix("ability_slot");
        out << static_cast<uint32_t>(*edits.ability_bit);
        field_prefix("ability_number_legacy");
        out << (static_cast<uint32_t>(*edits.ability_bit) + 1U);
    }
    out << "}";
}

std::string build_unpack_report_json(
    const Options& options,
    const Spc3Container& container,
    const std::vector<UnpackOutputReport>& outputs,
    uint64_t crc_mismatches,
    const GpuOffloadBenchResult& gpu_result,
    const CpuDecodeProfile& cpu_profile,
    double ms)
{
    std::ostringstream out;
    out << std::fixed << std::setprecision(3);
    out << "{\n";
    out << "  \"schema\": \"spc3_unpack_report.v1\",\n";
    out << "  \"mode\": \"unpack\",\n";
    out << "  \"ok\": " << (crc_mismatches == 0 ? "true" : "false") << ",\n";
    out << "  \"input\": \"" << json_escape(options.input.string()) << "\",\n";
    out << "  \"unpack_dir\": \"" << json_escape(options.unpack_dir.string()) << "\",\n";
    out << "  \"unpack_format\": \"" << unpack_format_name(options.unpack_format) << "\",\n";
    out << "  \"pk3_state\": \"" << pk3_corpus_state_name(options.pk3_state) << "\",\n";
    out << "  \"trainer_index\": \"" << json_escape(options.trainer_index.string()) << "\",\n";
    out << "  \"pk3_edits_enabled\": " << (options.pk3_edits.any() ? "true" : "false") << ",\n";
    out << "  \"pk3_edits\": ";
    write_pk3_edits_json(out, options.pk3_edits);
    out << ",\n";
    out << "  \"lane_select_mode\": \"" << lane_select_mode_name(options.lane_select_mode) << "\",\n";
    if (options.lane_select_mode == LaneSelectMode::One) {
        out << "  \"lane_select_value\": \"" << hex4(options.lane_hex) << "\",\n";
    } else if (options.lane_select_mode == LaneSelectMode::Range) {
        out << "  \"lane_select_from\": \"" << hex4(options.lane_from) << "\",\n";
        out << "  \"lane_select_to\": \"" << hex4(options.lane_to) << "\",\n";
    }
    out << "  \"level\": " << container.level << ",\n";
    out << "  \"lane_count\": " << outputs.size() << ",\n";
    out << "  \"crc_mismatches\": " << crc_mismatches << ",\n";
    out << "  \"gpu_rebuild\": ";
    write_gpu_result_json(out, gpu_result);
    out << ",\n";
    out << "  \"cpu_decode_profile\": ";
    write_cpu_decode_profile_json(out, cpu_profile);
    out << ",\n";
    out << "  \"asm_recommendation\": ";
    write_asm_recommendation_json(out, cpu_profile);
    out << ",\n";
    out << "  \"total_ms\": " << ms << ",\n";
    out << "  \"outputs\": [\n";
    for (size_t i = 0; i < outputs.size(); ++i) {
        out << "    {\"lane\": \"" << hex4(outputs[i].lane) << "\", "
            << "\"file\": \"" << json_escape(outputs[i].path.string()) << "\", "
            << "\"bytes\": " << outputs[i].bytes << ", "
            << "\"payload_crc32\": " << outputs[i].payload_crc32 << ", "
            << "\"output_payload_crc32\": " << outputs[i].output_payload_crc32 << ", "
            << "\"output_crc32\": " << outputs[i].output_crc32 << "}"
            << (i + 1 == outputs.size() ? "\n" : ",\n");
    }
    out << "  ]\n";
    out << "}\n";
    return out.str();
}

std::string build_verify_report_json(
    const Options& options,
    const Spc3Container& container,
    const std::vector<Spc3DecodedLane>& lanes,
    uint64_t internal_crc_mismatches,
    uint64_t source_compare_mismatches,
    const GpuOffloadBenchResult& gpu_result,
    const CpuDecodeProfile& cpu_profile,
    double ms)
{
    std::ostringstream out;
    out << std::fixed << std::setprecision(3);
    out << "{\n";
    out << "  \"schema\": \"spc3_verify_report.v1\",\n";
    out << "  \"mode\": \"verify\",\n";
    out << "  \"ok\": " << (internal_crc_mismatches == 0 && source_compare_mismatches == 0 ? "true" : "false") << ",\n";
    out << "  \"input\": \"" << json_escape(options.input.string()) << "\",\n";
    out << "  \"root\": \"" << json_escape(options.root.string()) << "\",\n";
    out << "  \"level\": " << container.level << ",\n";
    out << "  \"lane_count\": " << lanes.size() << ",\n";
    out << "  \"internal_crc_mismatches\": " << internal_crc_mismatches << ",\n";
    out << "  \"source_compare_mismatches\": " << source_compare_mismatches << ",\n";
    out << "  \"source_compare_enabled\": " << (options.no_source_compare ? "false" : "true") << ",\n";
    out << "  \"gpu_rebuild\": ";
    write_gpu_result_json(out, gpu_result);
    out << ",\n";
    out << "  \"cpu_decode_profile\": ";
    write_cpu_decode_profile_json(out, cpu_profile);
    out << ",\n";
    out << "  \"asm_recommendation\": ";
    write_asm_recommendation_json(out, cpu_profile);
    out << ",\n";
    out << "  \"total_ms\": " << ms << "\n";
    out << "}\n";
    return out.str();
}

std::string build_inspect_report_json(
    const Options& options,
    const Spc3Container& container,
    uint64_t file_size,
    double ms)
{
    uint64_t source_zip_bytes = 0;
    uint64_t stream_bytes = 0;
    uint64_t uncompressed_model_bytes = 0;
    uint64_t predictor_matches = 0;
    uint64_t predictor_exceptions = 0;
    for (const auto& entry : container.entries) {
        source_zip_bytes += entry.source_zip_size;
        stream_bytes += entry.stream_size;
        uncompressed_model_bytes += entry.uncompressed_model_size;
        predictor_matches += entry.predictor_matches;
        predictor_exceptions += entry.predictor_exceptions;
    }
    const uint64_t raw_payload_bytes = static_cast<uint64_t>(container.entries.size()) * kPayloadSize;
    const auto ratio = [](uint64_t numerator, uint64_t denominator) -> double {
        return denominator == 0 ? 0.0 : static_cast<double>(numerator) / static_cast<double>(denominator);
    };

    std::ostringstream out;
    out << std::fixed << std::setprecision(6);
    out << "{\n";
    out << "  \"schema\": \"spc3_inspect_report.v1\",\n";
    out << "  \"mode\": \"inspect\",\n";
    out << "  \"ok\": true,\n";
    out << "  \"input\": \"" << json_escape(options.input.string()) << "\",\n";
    out << "  \"file_size_bytes\": " << file_size << ",\n";
    out << "  \"version\": " << container.version << ",\n";
    out << "  \"level\": " << container.level << ",\n";
    out << "  \"flags\": " << container.flags << ",\n";
    out << "  \"lane_count\": " << container.entries.size() << ",\n";
    out << "  \"inspect_ms\": " << ms << ",\n";
    out << "  \"predictor\": {\n";
    out << "    \"embedded\": " << (spc3_predictor_embedded(container) ? "true" : "false") << ",\n";
    out << "    \"loaded\": " << (container.predictor.loaded ? "true" : "false") << ",\n";
    out << "    \"external_required\": " << (container.level == 3 && !container.predictor.loaded ? "true" : "false") << ",\n";
    out << "    \"compressed_size_bytes\": " << container.predictor_size << ",\n";
    out << "    \"raw_size_bytes\": " << (container.predictor.loaded ? kExpectedRecords * 4ULL : 0ULL) << "\n";
    out << "  },\n";
    out << "  \"header\": {\n";
    out << "    \"predictor_offset\": " << container.predictor_offset << ",\n";
    out << "    \"predictor_size\": " << container.predictor_size << ",\n";
    out << "    \"table_offset\": " << container.table_offset << ",\n";
    out << "    \"table_entry_size\": " << container.table_entry_size << ",\n";
    out << "    \"data_offset\": " << container.data_offset << ",\n";
    out << "    \"data_size\": " << container.data_size << "\n";
    out << "  },\n";
    out << "  \"totals\": {\n";
    out << "    \"source_zip_bytes\": " << source_zip_bytes << ",\n";
    out << "    \"raw_payload_bytes\": " << raw_payload_bytes << ",\n";
    out << "    \"stream_bytes\": " << stream_bytes << ",\n";
    out << "    \"uncompressed_model_bytes\": " << uncompressed_model_bytes << ",\n";
    out << "    \"predictor_matches\": " << predictor_matches << ",\n";
    out << "    \"predictor_exceptions\": " << predictor_exceptions << ",\n";
    out << "    \"spc3_to_source_zip_ratio\": " << ratio(file_size, source_zip_bytes) << ",\n";
    out << "    \"spc3_to_raw_payload_ratio\": " << ratio(file_size, raw_payload_bytes) << ",\n";
    out << "    \"stream_to_uncompressed_model_ratio\": " << ratio(stream_bytes, uncompressed_model_bytes) << "\n";
    out << "  },\n";
    out << "  \"lanes\": [\n";
    for (size_t i = 0; i < container.entries.size(); ++i) {
        const auto& entry = container.entries[i];
        const bool typed = entry_is_typed_level3(entry);
        out << "    {\"lane\": \"" << hex4(entry.lane) << "\", "
            << "\"level\": " << entry.level << ", "
            << "\"stream_kind\": \"" << stream_kind_name(entry) << "\", ";
        if (typed) {
            out << "\"codec\": \"typed-level3\", "
                << "\"codec_level\": 0, "
                << "\"codec_settings\": 0, ";
        } else {
            const CodecSpec codec = codec_from_entry_flags(entry.flags, entry.level);
            out << "\"codec\": \"" << codec_name(codec.id) << "\", "
                << "\"codec_level\": " << codec.level << ", "
                << "\"codec_settings\": " << codec.settings << ", ";
        }
        out
            << "\"table_flags\": " << entry.flags << ", "
            << "\"source_zip_size\": " << entry.source_zip_size << ", "
            << "\"source_zip_crc32\": " << entry.source_zip_crc32 << ", "
            << "\"source_zip_fnv1a64\": " << entry.source_zip_fnv64 << ", "
            << "\"payload_crc32\": " << entry.original_payload_crc32 << ", "
            << "\"rebuilt_payload_crc32\": " << entry.rebuilt_payload_crc32 << ", "
            << "\"stream_offset\": " << entry.stream_offset << ", "
            << "\"stream_size\": " << entry.stream_size << ", "
            << "\"uncompressed_model_size\": " << entry.uncompressed_model_size << ", "
            << "\"stream_to_source_zip_ratio\": " << ratio(entry.stream_size, entry.source_zip_size) << ", "
            << "\"stream_to_uncompressed_model_ratio\": " << ratio(entry.stream_size, entry.uncompressed_model_size) << ", "
            << "\"predictor_matches\": " << entry.predictor_matches << ", "
            << "\"predictor_exceptions\": " << entry.predictor_exceptions;
        if (typed) {
            out << ", \"typed_substreams\": [";
            for (size_t j = 0; j < entry.typed_substreams.size(); ++j) {
                const auto& sub = entry.typed_substreams[j];
                const CodecSpec sub_codec = codec_from_entry_flags(sub.flags, 3, true);
                out << "{\"kind\": \"" << typed_substream_name(sub.kind)
                    << "\", \"codec\": \"" << codec_name(sub_codec.id)
                    << "\", \"codec_level\": " << sub_codec.level
                    << ", \"offset\": " << sub.offset
                    << ", \"stream_size\": " << sub.stream_size
                    << ", \"raw_size\": " << sub.raw_size << "}"
                    << (j + 1 == entry.typed_substreams.size() ? "" : ", ");
            }
            out << "]";
        }
        out << "}"
            << (i + 1 == container.entries.size() ? "\n" : ",\n");
    }
    out << "  ]\n";
    out << "}\n";
    return out.str();
}

struct ConsolidateInputReport {
    fs::path path;
    uint64_t size_bytes = 0;
    uint32_t version = 0;
    uint32_t level = 0;
    uint32_t lane_count = 0;
};

struct ConsolidatedLaneStream {
    Spc3TableEntry entry;
    fs::path source_path;
};

struct Spc3ConsolidateResult {
    std::vector<Spc3TableEntry> entries;
    std::vector<ConsolidateInputReport> inputs;
    uint32_t version = 0;
    uint32_t level = 0;
    uint32_t flags = 0;
    uint64_t source_zip_bytes = 0;
    uint64_t raw_payload_bytes = 0;
    uint64_t total_size = 0;
    double build_ms = 0.0;
};

bool paths_refer_to_same_file(const fs::path& left, const fs::path& right) {
    std::error_code ec;
    if (fs::exists(left, ec) && fs::exists(right, ec) && fs::equivalent(left, right, ec)) {
        return true;
    }
    return fs::absolute(left).lexically_normal() == fs::absolute(right).lexically_normal();
}

std::vector<fs::path> find_spc3_shards(const fs::path& root, const fs::path& output) {
    if (!fs::is_directory(root)) {
        throw std::runtime_error("consolidate root is not directory: " + root.string());
    }
    std::vector<fs::path> paths;
    for (const auto& item : fs::directory_iterator(root)) {
        if (!item.is_regular_file()) {
            continue;
        }
        const fs::path path = item.path();
        if (path.extension() != ".spc3") {
            continue;
        }
        if (!output.empty() && paths_refer_to_same_file(path, output)) {
            continue;
        }
        paths.push_back(path);
    }
    std::sort(paths.begin(), paths.end(), [](const fs::path& a, const fs::path& b) {
        return a.filename().string() < b.filename().string();
    });
    if (paths.empty()) {
        throw std::runtime_error("no .spc3 shards found in " + root.string());
    }
    return paths;
}

std::vector<uint8_t> spc3_predictor_stream_bytes(const std::vector<uint8_t>& bytes, const Spc3Container& container) {
    if (container.predictor_size == 0) {
        return {};
    }
    const size_t pos = checked_offset(bytes, container.predictor_offset, container.predictor_size, "SPC3 predictor stream");
    return std::vector<uint8_t>(
        bytes.begin() + static_cast<std::ptrdiff_t>(pos),
        bytes.begin() + static_cast<std::ptrdiff_t>(pos + checked_u64_to_size(container.predictor_size, "SPC3 predictor stream")));
}

void patch_spc3_table_entry(std::vector<uint8_t>& bytes, size_t pos, const Spc3TableEntry& entry) {
    patch_u32(bytes, pos + 0, entry.lane);
    patch_u32(bytes, pos + 4, entry.level);
    patch_u32(bytes, pos + 8, entry.stream_kind);
    patch_u32(bytes, pos + 12, entry.flags);
    patch_u64(bytes, pos + 16, entry.source_zip_size);
    patch_u64(bytes, pos + 24, entry.source_zip_crc32);
    patch_u64(bytes, pos + 32, entry.source_zip_fnv64);
    patch_u64(bytes, pos + 40, entry.original_payload_crc32);
    patch_u64(bytes, pos + 48, entry.rebuilt_payload_crc32);
    patch_u64(bytes, pos + 56, entry.stream_offset);
    patch_u64(bytes, pos + 64, entry.stream_size);
    patch_u64(bytes, pos + 72, entry.uncompressed_model_size);
    patch_u64(bytes, pos + 80, entry.predictor_matches);
    patch_u64(bytes, pos + 88, entry.predictor_exceptions);
}

Spc3ConsolidateResult build_consolidated_spc3(const Options& options) {
    Stopwatch watch;
    Spc3ConsolidateResult result;
    const std::vector<fs::path> input_paths = find_spc3_shards(options.consolidate_root, options.output);
    std::vector<ConsolidatedLaneStream> lanes;
    std::map<uint32_t, fs::path> seen_lanes;
    std::vector<uint8_t> predictor_stream;
    std::vector<uint8_t> predictor_raw;
    bool initialized = false;

    for (const fs::path& path : input_paths) {
        const std::vector<uint8_t> bytes = read_file_bytes(path);
        Spc3Container container = parse_spc3_file(bytes);
        if (!initialized) {
            result.version = container.version;
            result.level = container.level;
            result.flags = container.flags;
            predictor_stream = spc3_predictor_stream_bytes(bytes, container);
            if (spc3_predictor_embedded(container)) {
                predictor_raw = serialize_predictor_raw(container.predictor);
            }
            initialized = true;
        } else {
            if (container.version != result.version || container.level != result.level || container.flags != result.flags) {
                throw std::runtime_error("SPC3 shard layout mismatch: " + path.string());
            }
            if (spc3_predictor_embedded(container)) {
                const std::vector<uint8_t> raw = serialize_predictor_raw(container.predictor);
                if (raw != predictor_raw) {
                    throw std::runtime_error("SPC3 shard predictor mismatch: " + path.string());
                }
            }
        }

        result.inputs.push_back({
            path,
            static_cast<uint64_t>(bytes.size()),
            container.version,
            container.level,
            static_cast<uint32_t>(container.entries.size()),
        });

        for (const Spc3TableEntry& entry : container.entries) {
            const auto [it, inserted] = seen_lanes.emplace(entry.lane, path);
            if (!inserted) {
                throw std::runtime_error(
                    "duplicate lane " + hex4(entry.lane) + " in " + path.string() +
                    " and " + it->second.string());
            }
            ConsolidatedLaneStream lane;
            lane.entry = entry;
            lane.source_path = path;
            result.source_zip_bytes = checked_add_u64(result.source_zip_bytes, entry.source_zip_size, "consolidated source ZIP bytes");
            result.raw_payload_bytes = checked_add_u64(result.raw_payload_bytes, kPayloadSize, "consolidated raw payload bytes");
            lanes.push_back(std::move(lane));
        }
    }

    std::sort(lanes.begin(), lanes.end(), [](const ConsolidatedLaneStream& a, const ConsolidatedLaneStream& b) {
        return a.entry.lane < b.entry.lane;
    });
    if (lanes.empty()) {
        throw std::runtime_error("SPC3 shards contain no lanes");
    }
    if (lanes.size() > std::numeric_limits<uint32_t>::max()) {
        throw std::runtime_error("consolidated SPC3 lane count exceeds u32 header limit");
    }

    const uint64_t predictor_offset = kSpc3HeaderSize;
    const uint64_t predictor_size = predictor_stream.size();
    const uint64_t table_offset = checked_add_u64(predictor_offset, predictor_size, "consolidated predictor/table offset");
    const uint64_t table_size = checked_mul_u64(lanes.size(), kSpc3TableEntrySize, "consolidated table size");
    const uint64_t data_offset = checked_add_u64(table_offset, table_size, "consolidated table/data offset");
    uint64_t data_size = 0;
    for (const ConsolidatedLaneStream& lane : lanes) {
        data_size = checked_add_u64(data_size, lane.entry.stream_size, "consolidated data size");
    }
    const uint64_t total_size = checked_add_u64(data_offset, data_size, "consolidated SPC3 file size");
    result.total_size = total_size;

    std::ofstream out(options.output, std::ios::binary | std::ios::trunc);
    if (!out) {
        throw std::runtime_error("failed to open output: " + options.output.string());
    }

    std::vector<uint8_t> header;
    header.reserve(kSpc3HeaderSize);
    header.insert(header.end(), {'S', 'P', 'C', '3'});
    append_u32(header, result.version);
    append_u32(header, result.level);
    append_u32(header, static_cast<uint32_t>(lanes.size()));
    append_u32(header, kExpectedRecords);
    append_u32(header, kRecordSize);
    append_u32(header, result.flags);
    append_u32(header, kSpc3HeaderSize);
    append_u64(header, predictor_offset);
    append_u64(header, predictor_size);
    append_u64(header, table_offset);
    append_u64(header, kSpc3TableEntrySize);
    append_u64(header, data_offset);
    append_u64(header, data_size);
    if (header.size() != kSpc3HeaderSize) {
        throw std::runtime_error("internal consolidated SPC3 header size mismatch");
    }
    out.write(reinterpret_cast<const char*>(header.data()),
              static_cast<std::streamsize>(header.size()));
    if (predictor_stream.empty()) {
        // no predictor payload
    } else {
        out.write(reinterpret_cast<const char*>(predictor_stream.data()),
                  static_cast<std::streamsize>(predictor_stream.size()));
    }

    std::vector<uint8_t> table_bytes(checked_u64_to_size(table_size, "consolidated SPC3 table size"), 0);
    std::vector<char> io_buf(static_cast<size_t>(1) << 20);  // 1 MB stream copy buffer
    uint64_t stream_offset = data_offset;
    result.entries.reserve(lanes.size());
    for (size_t i = 0; i < lanes.size(); ++i) {
        const size_t table_pos = i * static_cast<size_t>(kSpc3TableEntrySize);
        std::ifstream input(lanes[i].source_path, std::ios::binary);
        if (!input) {
            throw std::runtime_error("failed to open SPC3 shard for consolidation: " + lanes[i].source_path.string());
        }
        Spc3TableEntry entry = lanes[i].entry;
        const uint64_t source_stream_offset = entry.stream_offset;
        const uint64_t source_stream_size = entry.stream_size;
        entry.stream_offset = stream_offset;
        patch_spc3_table_entry(table_bytes, table_pos, entry);
        result.entries.push_back(entry);

        if (source_stream_offset > static_cast<uint64_t>(std::numeric_limits<std::streamoff>::max())) {
            throw std::runtime_error("consolidated stream source offset exceeds host stream address space");
        }
        input.seekg(static_cast<std::streamoff>(source_stream_offset), std::ios::beg);
        if (!input) {
            throw std::runtime_error("failed to seek consolidated stream source offset: " + lanes[i].source_path.string());
        }
        uint64_t remaining = source_stream_size;
        while (remaining > 0) {
            const size_t want = static_cast<size_t>(std::min<uint64_t>(remaining, io_buf.size()));
            input.read(io_buf.data(), static_cast<std::streamsize>(want));
            const std::streamsize got = input.gcount();
            if (got <= 0) {
                throw std::runtime_error("short read while copying consolidated stream: " + lanes[i].source_path.string());
            }
            out.write(io_buf.data(), got);
            if (!out) {
                throw std::runtime_error("failed writing consolidated data section");
            }
            remaining -= static_cast<uint64_t>(got);
        }

        stream_offset = checked_add_u64(stream_offset, entry.stream_size, "consolidated stream layout");
    }
    out.seekp(static_cast<std::streamoff>(table_offset), std::ios::beg);
    out.write(reinterpret_cast<const char*>(table_bytes.data()),
              static_cast<std::streamsize>(table_bytes.size()));
    if (!out) {
        throw std::runtime_error("failed writing consolidated table");
    }
    out.flush();
    if (!out) {
        throw std::runtime_error("failed to finalize output: " + options.output.string());
    }
    out.seekp(0, std::ios::end);
    if (out.tellp() != static_cast<std::streamoff>(total_size)) {
        throw std::runtime_error("consolidated output size mismatch");
    }
    out.close();
    result.build_ms = watch.elapsed_ms();
    return result;
}

std::string build_consolidate_report_json(
    const Options& options,
    const Spc3ConsolidateResult& result)
{
    std::ostringstream out;
    out << std::fixed << std::setprecision(3);
    out << "{\n";
    out << "  \"schema\": \"spc3_consolidate_report.v1\",\n";
    out << "  \"mode\": \"consolidate\",\n";
    out << "  \"ok\": true,\n";
    out << "  \"consolidate_root\": \"" << json_escape(options.consolidate_root.string()) << "\",\n";
    out << "  \"output\": \"" << json_escape(options.output.string()) << "\",\n";
    out << "  \"copy_mode\": \"compressed_stream_copy_no_payload_decode\",\n";
    out << "  \"version\": " << result.version << ",\n";
    out << "  \"level\": " << result.level << ",\n";
    out << "  \"input_spc3_count\": " << result.inputs.size() << ",\n";
    out << "  \"lane_count\": " << result.entries.size() << ",\n";
    out << "  \"spc3_size_bytes\": " << result.total_size << ",\n";
    out << "  \"source_zip_bytes\": " << result.source_zip_bytes << ",\n";
    out << "  \"raw_payload_bytes\": " << result.raw_payload_bytes << ",\n";
    out << "  \"build_ms\": " << result.build_ms << ",\n";
    out << "  \"inputs\": [\n";
    for (size_t i = 0; i < result.inputs.size(); ++i) {
        const auto& input = result.inputs[i];
        out << "    {\"path\": \"" << json_escape(input.path.string()) << "\", "
            << "\"size_bytes\": " << input.size_bytes << ", "
            << "\"version\": " << input.version << ", "
            << "\"level\": " << input.level << ", "
            << "\"lane_count\": " << input.lane_count << "}"
            << (i + 1 == result.inputs.size() ? "\n" : ",\n");
    }
    out << "  ],\n";
    out << "  \"lanes\": [\n";
    for (size_t i = 0; i < result.entries.size(); ++i) {
        const auto& entry = result.entries[i];
        out << "    {\"lane\": \"" << hex4(entry.lane)
            << "\", \"level\": " << entry.level
            << ", \"stream_kind\": \"" << stream_kind_name(entry)
            << "\", \"stream_size\": " << entry.stream_size
            << ", \"source_zip_size\": " << entry.source_zip_size
            << ", \"payload_crc32\": " << entry.original_payload_crc32
            << ", \"rebuilt_payload_crc32\": " << entry.rebuilt_payload_crc32
            << ", \"predictor_exceptions\": " << entry.predictor_exceptions << "}"
            << (i + 1 == result.entries.size() ? "\n" : ",\n");
    }
    out << "  ]\n";
    out << "}\n";
    return out.str();
}

// Per-worker lane result. Holds only metadata + a byte offset into the worker's
// own temp file; never holds the stream bytes themselves.
struct PackedLaneResult {
    size_t lane_index = 0;          // position in find_lane_zips order, for sorting
    uint64_t temp_offset = 0;       // byte offset within worker's temp file
    Spc3TableEntry entry;           // table metadata (stream_offset filled later)
    uint64_t source_zip_size = 0;
    uint64_t encrypted_size = 0;
    bool roundtrip_ok = true;
};

// Streaming + multithreaded pack. N workers each process lanes from a shared atomic
// queue, writing compressed streams into per-worker temp files. After join, results
// are sorted by lane_index to preserve find_lane_zips ordering, then the final SPC3
// is assembled (single-threaded) by reading streams in sorted order from the worker
// temp files and concatenating into the output. Per-thread working set ~5 MB.
void run_pack_mode(const Options& options) {
    PredictorTable predictor;
    if (!options.no_predictor || options.level == 3) {
        predictor = load_predictor_table(options.predictor);
    }

    const std::vector<LanePath> lanes_to_run = find_lane_zips(options.root, options.limit_zips);
    if (lanes_to_run.empty()) {
        throw std::runtime_error("no Phase 3 lane ZIPs found");
    }

    const uint32_t level = options.level;
    const bool typed_level3 = options.typed_level3;
    const bool embed_predictor = level == 3 && !options.external_predictor;
    if (level > 3) {
        throw std::runtime_error("SPC3 level must be 0..3");
    }
    if (level == 3 && !predictor.loaded) {
        throw std::runtime_error("SPC3 level 3 requires predictor table");
    }
    if (typed_level3 && level != 3) {
        throw std::runtime_error("typed level 3 pack requires level 3");
    }
    if (lanes_to_run.size() > std::numeric_limits<uint32_t>::max()) {
        throw std::runtime_error("SPC3 lane count exceeds u32 header limit");
    }

    // Resolve worker count. 0 -> hardware_concurrency; clamp to lane count.
    unsigned int worker_count;
    if (options.threads > 0) {
        worker_count = static_cast<unsigned int>(options.threads);
    } else {
        worker_count = std::thread::hardware_concurrency();
        if (worker_count == 0) worker_count = 1;
    }
    if (static_cast<size_t>(worker_count) > lanes_to_run.size()) {
        worker_count = static_cast<unsigned int>(lanes_to_run.size());
    }

    const TypedLevel3Policy typed_policy = typed_level3
        ? typed_level3_policy_for_pack(options)
        : TypedLevel3Policy{};
    const CodecSpec requested_codec = requested_pack_codec(options);
    const bool codec_level_set = requested_pack_codec_level_set(options);

    Stopwatch watch;
    StreamingPackResult result;
    result.entries.reserve(lanes_to_run.size());

    std::vector<uint8_t> predictor_stream;
    if (embed_predictor) {
        predictor_stream = zlib_compress_data(serialize_predictor_raw(predictor), 9);
    }

    const uint64_t predictor_offset = kSpc3HeaderSize;
    const uint64_t predictor_size = predictor_stream.size();
    const uint64_t table_offset = checked_add_u64(predictor_offset, predictor_size, "SPC3 predictor/table offset");
    const uint64_t table_size = checked_mul_u64(lanes_to_run.size(), kSpc3TableEntrySize, "SPC3 table size");
    const uint64_t data_offset = checked_add_u64(table_offset, table_size, "SPC3 table/data offset");

    // Per-worker state. Each worker owns its temp file and result list independently.
    struct WorkerSlot {
        fs::path temp_path;
        std::vector<PackedLaneResult> results;
        std::string error;  // first error encountered by this worker, if any
    };
    std::vector<WorkerSlot> slots(worker_count);
    for (unsigned int w = 0; w < worker_count; ++w) {
        slots[w].temp_path = options.output.string() + ".streamdata." + std::to_string(w) + ".tmp";
        std::error_code ec;
        fs::remove(slots[w].temp_path, ec);
    }

    auto cleanup_temps = [&]() {
        for (auto& s : slots) {
            std::error_code ec;
            fs::remove(s.temp_path, ec);
        }
    };

    std::atomic<size_t> next_lane{0};
    std::atomic<bool> abort_requested{false};
    std::mutex print_mutex;

    std::cout << "pack: " << worker_count << " worker thread"
              << (worker_count == 1 ? "" : "s") << ", " << lanes_to_run.size() << " lanes\n";

    auto worker_fn = [&](unsigned int worker_id) {
        WorkerSlot& slot = slots[worker_id];
        std::ofstream temp_out(slot.temp_path, std::ios::binary);
        if (!temp_out) {
            slot.error = "failed to create temp stream file: " + slot.temp_path.string();
            abort_requested.store(true);
            return;
        }
        uint64_t worker_offset = 0;

        try {
            while (true) {
                if (abort_requested.load(std::memory_order_relaxed)) break;
                const size_t i = next_lane.fetch_add(1, std::memory_order_relaxed);
                if (i >= lanes_to_run.size()) break;

                const LanePath& lane_path = lanes_to_run[i];
                {
                    std::lock_guard<std::mutex> lk(print_mutex);
                    std::cout << "pack lane " << hex4(lane_path.lane) << " stream (w" << worker_id << ")\n";
                }

                LaneModel model = build_lane_model(
                    lane_path,
                    predictor.loaded ? &predictor : nullptr,
                    !options.no_entropy_probe);
                if (lane_has_failure(model.metrics)) {
                    slot.error = "lane failed validation before pack: " + hex4(lane_path.lane);
                    abort_requested.store(true);
                    return;
                }

                uint64_t uncompressed_model_size = 0;
                std::array<Spc3TypedSubstreamEntry, kSpc3TypedLevel3SubstreamCount> typed_substreams{};
                const CodecSpec lane_codec = typed_level3
                    ? CodecSpec{CodecId::None, 0, 0}
                    : resolve_pack_codec(level, requested_codec, codec_level_set);
                std::vector<uint8_t> stream;
                if (level == 0) {
                    stream = model.encrypted;
                    uncompressed_model_size = model.encrypted.size();
                } else if (level == 1) {
                    const std::vector<uint8_t> decrypted = decrypted_stream_from_model(model);
                    uncompressed_model_size = decrypted.size();
                    stream = codec_compress_data(decrypted, lane_codec);
                } else if (level == 2) {
                    const std::vector<uint8_t> raw = make_template_iv32_model(model);
                    uncompressed_model_size = raw.size();
                    stream = codec_compress_data(raw, lane_codec);
                } else if (typed_level3) {
                    const TypedLevel3StreamBuild typed = build_typed_level3_stream(model, typed_policy);
                    uncompressed_model_size = typed.raw_size;
                    stream = typed.stream;
                    typed_substreams = typed.substreams;
                } else {
                    const std::vector<uint8_t> raw = make_template_exception_model(model);
                    uncompressed_model_size = raw.size();
                    stream = codec_compress_data(raw, lane_codec);
                }

                Spc3TableEntry entry;
                entry.lane = model.metrics.lane;
                entry.level = level;
                entry.stream_kind = typed_level3 ? kSpc3StreamKindTypedLevel3 : level;
                entry.flags = typed_level3 ? 0 : pack_entry_codec_flags(lane_codec);
                entry.source_zip_size = model.metrics.zip_size_bytes;
                entry.source_zip_crc32 = model.zip_crc32;
                entry.source_zip_fnv64 = model.zip_fnv64;
                entry.original_payload_crc32 = model.encrypted_crc32;
                entry.uncompressed_model_size = uncompressed_model_size;
                entry.predictor_matches = model.metrics.predictor_matches;
                entry.predictor_exceptions = model.metrics.predictor_exceptions;
                // stream_offset is assigned later in single-threaded merge phase.
                entry.stream_size = stream.size();
                if (typed_level3) {
                    entry.typed_level3 = true;
                    entry.typed_substreams = typed_substreams;
                }

                // Roundtrip verify (uses entry.typed_substreams for typed_level3; never
                // touches entry.stream_offset, so it works before final layout is known).
                const std::vector<uint8_t> rebuilt = rebuild_payload_from_stream_data(
                    stream.data(),
                    stream.size(),
                    entry,
                    predictor);
                entry.rebuilt_payload_crc32 = crc32_vector(rebuilt);
                const bool roundtrip_ok = (rebuilt.size() == model.encrypted.size() &&
                    std::memcmp(rebuilt.data(), model.encrypted.data(), kPayloadSize) == 0);

                if (stream.size() > static_cast<size_t>(std::numeric_limits<std::streamsize>::max())) {
                    slot.error = "lane stream exceeds streamsize limit";
                    abort_requested.store(true);
                    return;
                }
                temp_out.write(reinterpret_cast<const char*>(stream.data()),
                               static_cast<std::streamsize>(stream.size()));
                if (!temp_out) {
                    slot.error = "failed to write temp stream file (w" + std::to_string(worker_id) + ")";
                    abort_requested.store(true);
                    return;
                }

                PackedLaneResult lane_result;
                lane_result.lane_index = i;
                lane_result.temp_offset = worker_offset;
                lane_result.entry = entry;
                lane_result.source_zip_size = model.metrics.zip_size_bytes;
                lane_result.encrypted_size = model.encrypted.size();
                lane_result.roundtrip_ok = roundtrip_ok;
                slot.results.push_back(std::move(lane_result));
                worker_offset += stream.size();
                // model, stream, rebuilt all drop here.
            }
            temp_out.flush();
            if (!temp_out) {
                slot.error = "failed to flush temp stream file (w" + std::to_string(worker_id) + ")";
                abort_requested.store(true);
            }
        } catch (const std::exception& e) {
            slot.error = std::string("worker ") + std::to_string(worker_id) + " exception: " + e.what();
            abort_requested.store(true);
        }
    };

    // Dispatch.
    if (worker_count == 1) {
        worker_fn(0);
    } else {
        std::vector<std::thread> threads;
        threads.reserve(worker_count);
        for (unsigned int w = 0; w < worker_count; ++w) {
            threads.emplace_back(worker_fn, w);
        }
        for (auto& t : threads) t.join();
    }

    // Surface first worker error.
    for (const auto& slot : slots) {
        if (!slot.error.empty()) {
            cleanup_temps();
            throw std::runtime_error(slot.error);
        }
    }

    // Merge all worker results, sort by lane_index to restore find_lane_zips order.
    struct GlobalLaneResult {
        size_t lane_index;
        unsigned int worker_id;
        uint64_t temp_offset;
        Spc3TableEntry entry;
        uint64_t source_zip_size;
        uint64_t encrypted_size;
        bool roundtrip_ok;
    };
    size_t total_count = 0;
    for (const auto& s : slots) total_count += s.results.size();
    if (total_count != lanes_to_run.size()) {
        cleanup_temps();
        throw std::runtime_error("worker results do not cover every lane (got " +
            std::to_string(total_count) + " of " + std::to_string(lanes_to_run.size()) + ")");
    }
    std::vector<GlobalLaneResult> sorted;
    sorted.reserve(total_count);
    for (unsigned int w = 0; w < worker_count; ++w) {
        for (const auto& r : slots[w].results) {
            GlobalLaneResult g;
            g.lane_index = r.lane_index;
            g.worker_id = w;
            g.temp_offset = r.temp_offset;
            g.entry = r.entry;
            g.source_zip_size = r.source_zip_size;
            g.encrypted_size = r.encrypted_size;
            g.roundtrip_ok = r.roundtrip_ok;
            sorted.push_back(std::move(g));
        }
    }
    std::sort(sorted.begin(), sorted.end(),
        [](const GlobalLaneResult& a, const GlobalLaneResult& b) {
            return a.lane_index < b.lane_index;
        });

    // Assign final stream offsets in deterministic order.
    uint64_t stream_cursor = data_offset;
    for (auto& g : sorted) {
        g.entry.stream_offset = stream_cursor;
        if (!g.roundtrip_ok) ++result.roundtrip_mismatches;
        result.source_zip_bytes = checked_add_u64(result.source_zip_bytes, g.source_zip_size,
                                                  "SPC3 source zip bytes");
        result.raw_payload_bytes = checked_add_u64(result.raw_payload_bytes, g.encrypted_size,
                                                   "SPC3 raw payload bytes");
        result.entries.push_back(g.entry);
        stream_cursor = checked_add_u64(stream_cursor, g.entry.stream_size, "SPC3 stream layout");
    }

    if (result.roundtrip_mismatches != 0) {
        cleanup_temps();
        throw std::runtime_error("SPC3 pack roundtrip mismatch");
    }

    const uint64_t data_size = stream_cursor - data_offset;
    result.total_size = stream_cursor;

    // Assemble the final SPC3: header + predictor + table + (sorted concat of streams).
    std::ofstream out(options.output, std::ios::binary | std::ios::trunc);
    if (!out) {
        cleanup_temps();
        throw std::runtime_error("failed to open output: " + options.output.string());
    }

    std::vector<uint8_t> header;
    header.reserve(kSpc3HeaderSize);
    header.insert(header.end(), {'S', 'P', 'C', '3'});
    append_u32(header, typed_level3 ? kSpc3VersionV2 : kSpc3VersionV1);
    append_u32(header, level);
    append_u32(header, static_cast<uint32_t>(result.entries.size()));
    append_u32(header, kExpectedRecords);
    append_u32(header, kRecordSize);
    append_u32(header, embed_predictor ? kSpc3FlagPredictorEmbedded : 0);
    append_u32(header, kSpc3HeaderSize);
    append_u64(header, predictor_offset);
    append_u64(header, predictor_size);
    append_u64(header, table_offset);
    append_u64(header, kSpc3TableEntrySize);
    append_u64(header, data_offset);
    append_u64(header, data_size);
    if (header.size() != kSpc3HeaderSize) {
        cleanup_temps();
        throw std::runtime_error("internal SPC3 header size mismatch (threaded pack)");
    }
    out.write(reinterpret_cast<const char*>(header.data()),
              static_cast<std::streamsize>(header.size()));
    if (!predictor_stream.empty()) {
        out.write(reinterpret_cast<const char*>(predictor_stream.data()),
                  static_cast<std::streamsize>(predictor_stream.size()));
    }

    std::vector<uint8_t> table_bytes(checked_u64_to_size(table_size, "SPC3 table size"), 0);
    for (size_t i = 0; i < result.entries.size(); ++i) {
        const size_t pos = i * static_cast<size_t>(kSpc3TableEntrySize);
        const Spc3TableEntry& entry = result.entries[i];
        patch_u32(table_bytes, pos + 0, entry.lane);
        patch_u32(table_bytes, pos + 4, entry.level);
        patch_u32(table_bytes, pos + 8, entry.stream_kind);
        patch_u32(table_bytes, pos + 12, entry.flags);
        patch_u64(table_bytes, pos + 16, entry.source_zip_size);
        patch_u64(table_bytes, pos + 24, entry.source_zip_crc32);
        patch_u64(table_bytes, pos + 32, entry.source_zip_fnv64);
        patch_u64(table_bytes, pos + 40, entry.original_payload_crc32);
        patch_u64(table_bytes, pos + 48, entry.rebuilt_payload_crc32);
        patch_u64(table_bytes, pos + 56, entry.stream_offset);
        patch_u64(table_bytes, pos + 64, entry.stream_size);
        patch_u64(table_bytes, pos + 72, entry.uncompressed_model_size);
        patch_u64(table_bytes, pos + 80, entry.predictor_matches);
        patch_u64(table_bytes, pos + 88, entry.predictor_exceptions);
    }
    out.write(reinterpret_cast<const char*>(table_bytes.data()),
              static_cast<std::streamsize>(table_bytes.size()));

    // Open each worker's temp file for read, concat streams in sorted (lane_index) order.
    std::vector<std::ifstream> worker_in(worker_count);
    for (unsigned int w = 0; w < worker_count; ++w) {
        worker_in[w].open(slots[w].temp_path, std::ios::binary);
        if (!worker_in[w]) {
            cleanup_temps();
            throw std::runtime_error("failed to reopen temp stream file: " + slots[w].temp_path.string());
        }
    }
    std::vector<char> io_buf(static_cast<size_t>(1) << 20);  // 1 MB copy buffer
    for (const auto& g : sorted) {
        std::ifstream& in = worker_in[g.worker_id];
        in.seekg(static_cast<std::streamoff>(g.temp_offset));
        uint64_t remaining = g.entry.stream_size;
        while (remaining > 0) {
            const size_t want = static_cast<size_t>(std::min<uint64_t>(remaining, io_buf.size()));
            in.read(io_buf.data(), static_cast<std::streamsize>(want));
            const std::streamsize got = in.gcount();
            if (got <= 0) {
                cleanup_temps();
                throw std::runtime_error("short read from worker temp file");
            }
            out.write(io_buf.data(), got);
            if (!out) {
                cleanup_temps();
                throw std::runtime_error("failed writing data section");
            }
            remaining -= static_cast<uint64_t>(got);
        }
    }
    for (auto& f : worker_in) f.close();
    cleanup_temps();

    out.flush();
    out.close();
    if (!out) {
        throw std::runtime_error("failed to finalize output: " + options.output.string());
    }

    result.build_ms = watch.elapsed_ms();

    write_text_file(options.report, build_pack_report_json_streaming(options, result));
    std::cout << "spc3 " << options.output.string() << " bytes=" << result.total_size << "\n";
}

void run_consolidate_mode(const Options& options) {
    Stopwatch watch;
    Spc3ConsolidateResult consolidated = build_consolidated_spc3(options);
    consolidated.build_ms = watch.elapsed_ms();
    write_text_file(options.report, build_consolidate_report_json(options, consolidated));
    std::cout << "consolidated_spc3 " << options.output.string()
              << " lanes=" << consolidated.entries.size()
              << " inputs=" << consolidated.inputs.size()
              << " bytes=" << consolidated.total_size << "\n";
}

void run_unpack_mode(const Options& options) {
    if (options.input.empty()) {
        throw std::runtime_error("--input is required for unpack");
    }
    Stopwatch watch;
    const std::vector<uint8_t> bytes = read_file_bytes(options.input);
    Spc3Container container = parse_spc3_file(bytes);
    ensure_spc3_predictor_for_decode(container, options);
    Spc3Container selected_container = select_unpack_lanes(options, container);
    GpuOffloadBenchResult gpu_result;
    CpuDecodeProfile cpu_profile;
    fs::create_directories(options.unpack_dir);
    std::unique_ptr<TsvTrainerIndex> trainer_index;
    if (options.pk3_state != Pk3CorpusState::Egg) {
        trainer_index = std::make_unique<TsvTrainerIndex>(load_tsv_trainer_index(options.trainer_index));
        if (trainer_index->count != trainer_index->entries.size()) {
            throw std::runtime_error("hatched unpack output requires a complete 8192-entry trainer index");
        }
    }

    uint64_t crc_mismatches = 0;
    std::vector<UnpackOutputReport> outputs;
    outputs.reserve(selected_container.entries.size());
    gpu_result.lane_count = static_cast<uint32_t>(selected_container.entries.size());
    gpu_result.output_bytes = static_cast<uint64_t>(selected_container.entries.size()) * kPayloadSize;

    struct UnpackSinkState {
        const Spc3Container* container;
        std::vector<UnpackOutputReport>* outputs;
        const Options* options;
        const TsvTrainerIndex* trainer_index;
        uint64_t* crc_mismatches;
    };
    auto emit_unpacked_lane = [](void* user,
                                 size_t lane_index,
                                 uint16_t lane,
                                 const uint8_t* payload,
                                 size_t payload_size,
                                 uint32_t payload_crc32) {
        const UnpackSinkState* state = static_cast<const UnpackSinkState*>(user);
        if (lane_index >= state->container->entries.size()) {
            throw std::runtime_error("unpack callback lane index out of range");
        }
        const Spc3TableEntry& entry = state->container->entries[lane_index];
        if (lane != entry.lane) {
            throw std::runtime_error("unpack callback lane ordering mismatch");
        }
        if (payload_size != kPayloadSize) {
            throw std::runtime_error("unpack callback lane payload size mismatch");
        }
        const bool needs_transform =
            state->options->pk3_state != Pk3CorpusState::Egg || state->options->pk3_edits.any();
        const uint8_t* output_payload_data = payload;
        size_t output_payload_size = payload_size;
        std::vector<uint8_t> transformed_payload;
        if (needs_transform) {
            const std::vector<uint8_t> payload_bytes(payload, payload + payload_size);
            transformed_payload = transform_lane_payload_for_corpus_state(
                static_cast<uint16_t>(lane),
                payload_bytes,
                state->options->pk3_state,
                state->trainer_index,
                state->options->pk3_edits);
            output_payload_data = transformed_payload.data();
            output_payload_size = transformed_payload.size();
        }
        UnpackOutputReport output;
        output.lane = lane;
        output.payload_crc32 = payload_crc32;
        if (payload_crc32 != entry.rebuilt_payload_crc32 ||
            payload_crc32 != entry.original_payload_crc32) {
            ++(*state->crc_mismatches);
        }
        output.output_payload_crc32 = crc32_bytes(output_payload_data, output_payload_size);
        if (state->options->unpack_format == UnpackFormat::Zip) {
            const std::vector<uint8_t> output_payload(
                output_payload_data,
                output_payload_data + output_payload_size);
            std::vector<uint8_t> zip = build_stored_lane_zip(static_cast<uint16_t>(lane), output_payload);
            output.path = state->options->unpack_dir / (hex4(lane) + ".spinda80.zip");
            output.bytes = zip.size();
            output.output_crc32 = crc32_vector(zip);
            write_binary_file(output.path, zip);
        } else {
            output.path = state->options->unpack_dir / (hex4(lane) + ".pk3raw");
            output.bytes = output_payload_size;
            output.output_crc32 = output.output_payload_crc32;
            write_binary_file(output.path, output_payload_data, output_payload_size);
        }
        state->outputs->push_back(std::move(output));
    };

    UnpackSinkState sink_state{
        &selected_container,
        &outputs,
        &options,
        trainer_index.get(),
        &crc_mismatches};
    Spc3DecodedLaneSink sink{&sink_state, emit_unpacked_lane};

    if (options.gpu_rebuild) {
        decode_spc3_lanes_with_optional_gpu(bytes, selected_container, options, &gpu_result, &cpu_profile, &sink);
    } else {
        decode_spc3_lanes_streaming(
            bytes,
            selected_container,
            &cpu_profile,
            nullptr,
            &sink);
        cpu_profile.backend = "cpu";
    }
    const double ms = watch.elapsed_ms();
    write_text_file(options.report, build_unpack_report_json(
        options,
        selected_container,
        outputs,
        crc_mismatches,
        gpu_result,
        cpu_profile,
        ms));
    if (crc_mismatches != 0) {
        throw std::runtime_error("SPC3 unpack CRC mismatch");
    }
    std::cout << "unpacked_lanes=" << selected_container.entries.size()
              << " format=" << unpack_format_name(options.unpack_format)
              << " dir=" << options.unpack_dir.string() << "\n";
}

// Streaming verify: GPU rebuild can run in streaming mode when requested; otherwise
// fall back to multithreaded CPU decoding with lane-local reporting.
void run_verify_mode(const Options& options) {
    if (options.input.empty()) {
        throw std::runtime_error("--input is required for verify");
    }
    Stopwatch watch;
    const std::vector<uint8_t> bytes = read_file_bytes(options.input);
    Spc3Container container = parse_spc3_file(bytes);
    ensure_spc3_predictor_for_decode(container, options);

    GpuOffloadBenchResult gpu_result;
    gpu_result.lane_count = static_cast<uint32_t>(container.entries.size());
    gpu_result.output_bytes = static_cast<uint64_t>(container.entries.size()) * kPayloadSize;
    CpuDecodeProfile cpu_profile;
    uint64_t internal_crc_mismatches = 0;
    uint64_t source_compare_mismatches = 0;

    if (options.gpu_rebuild) {
        struct VerifySinkState {
            const Spc3Container* container;
            const Options* options;
            uint64_t* internal_crc_mismatches;
            uint64_t* source_compare_mismatches;
        };
        auto emit_verify_lane = [](void* user,
                                  size_t lane_index,
                                  uint16_t lane,
                                  const uint8_t* payload,
                                  size_t payload_size,
                                  uint32_t payload_crc32) {
            const VerifySinkState* state = static_cast<const VerifySinkState*>(user);
            if (lane_index >= state->container->entries.size()) {
                throw std::runtime_error("verify callback lane index out of range");
            }
            const Spc3TableEntry& entry = state->container->entries[lane_index];
            if (lane != entry.lane) {
                throw std::runtime_error("verify callback lane ordering mismatch");
            }
            if (payload_size != kPayloadSize) {
                throw std::runtime_error("verify callback payload size mismatch");
            }
            if (payload_crc32 != entry.original_payload_crc32 ||
                payload_crc32 != entry.rebuilt_payload_crc32) {
                ++(*state->internal_crc_mismatches);
            }
            if (state->options->no_source_compare) {
                return;
            }
            const fs::path zip_path = state->options->root / (hex4(lane) + ".spinda80.zip");
            const std::vector<uint8_t> payload_bytes(payload, payload + payload_size);
            const LaneModel source = build_lane_model({lane, zip_path}, nullptr, false);
            if (lane_has_failure(source.metrics) ||
                source.encrypted.size() != payload_bytes.size() ||
                std::memcmp(source.encrypted.data(), payload_bytes.data(), kPayloadSize) != 0) {
                ++(*state->source_compare_mismatches);
            }
        };

        VerifySinkState verify_state{
            &container,
            &options,
            &internal_crc_mismatches,
            &source_compare_mismatches};
        Spc3DecodedLaneSink sink{&verify_state, emit_verify_lane};
        decode_spc3_lanes_with_optional_gpu(bytes, container, options, &gpu_result, &cpu_profile, &sink);
    } else {
        // Resolve worker count (same logic as pack).
        unsigned int worker_count;
        if (options.threads > 0) {
            worker_count = static_cast<unsigned int>(options.threads);
        } else {
            worker_count = std::thread::hardware_concurrency();
            if (worker_count == 0) worker_count = 1;
        }
        if (container.entries.empty()) {
            worker_count = 1;
        } else if (static_cast<size_t>(worker_count) > container.entries.size()) {
            worker_count = static_cast<unsigned int>(container.entries.size());
        }

        // Per-worker accumulator. No shared mutable state outside this slot.
        struct VerifySlot {
            uint64_t internal_crc_mismatches = 0;
            uint64_t source_compare_mismatches = 0;
            CpuDecodeProfile profile;
            std::string error;
        };
        std::vector<VerifySlot> slots(worker_count);

        std::atomic<size_t> next_entry{0};
        std::atomic<bool> abort_requested{false};
        Stopwatch decode_watch;

        std::cout << "verify: " << worker_count << " worker thread"
                  << (worker_count == 1 ? "" : "s") << ", " << container.entries.size() << " lanes\n";

        auto worker_fn = [&](unsigned int worker_id) {
            VerifySlot& slot = slots[worker_id];
            slot.profile.used = true;
            try {
                while (true) {
                    if (abort_requested.load(std::memory_order_relaxed)) break;
                    const size_t i = next_entry.fetch_add(1, std::memory_order_relaxed);
                    if (i >= container.entries.size()) break;

                    const Spc3TableEntry& entry = container.entries[i];
                    if (entry_is_typed_level3(entry)) {
                        ++slot.profile.typed_lanes;
                    } else {
                        ++slot.profile.legacy_lanes;
                    }

                    const std::vector<uint8_t> payload = rebuild_payload_from_spc3_stream(
                        bytes, entry, container.predictor, &slot.profile);
                    slot.profile.crc_bytes += payload.size();
                    uint32_t payload_crc;
                    {
                        ScopedTimer timer(slot.profile.crc_ms);
                        payload_crc = crc32_vector(payload);
                    }
                    if (payload_crc != entry.original_payload_crc32 ||
                        payload_crc != entry.rebuilt_payload_crc32) {
                        ++slot.internal_crc_mismatches;
                    }

                    if (!options.no_source_compare) {
                        const fs::path zip_path = options.root /
                            (hex4(static_cast<uint16_t>(entry.lane)) + ".spinda80.zip");
                        const LaneModel source = build_lane_model(
                            {static_cast<uint16_t>(entry.lane), zip_path}, nullptr, false);
                        if (lane_has_failure(source.metrics) ||
                            source.encrypted.size() != payload.size() ||
                            std::memcmp(source.encrypted.data(), payload.data(), kPayloadSize) != 0) {
                            ++slot.source_compare_mismatches;
                        }
                    }
                    // payload and (transient) source LaneModel both drop here.
                }
            } catch (const std::exception& e) {
                slot.error = std::string("worker ") + std::to_string(worker_id) + " exception: " + e.what();
                abort_requested.store(true);
            }
        };

        if (worker_count == 1) {
            worker_fn(0);
        } else {
            std::vector<std::thread> threads;
            threads.reserve(worker_count);
            for (unsigned int w = 0; w < worker_count; ++w) {
                threads.emplace_back(worker_fn, w);
            }
            for (auto& t : threads) t.join();
        }

        // Surface first worker error.
        for (const auto& slot : slots) {
            if (!slot.error.empty()) {
                throw std::runtime_error(slot.error);
            }
        }

        // Merge per-worker results. Profile timing fields are sums-across-lanes, which
        // matches legacy semantics (they were summed across the serial loop too).
        cpu_profile.used = true;
        cpu_profile.lane_count = static_cast<uint32_t>(container.entries.size());
        for (const auto& slot : slots) {
            cpu_profile.typed_lanes += slot.profile.typed_lanes;
            cpu_profile.legacy_lanes += slot.profile.legacy_lanes;
            cpu_profile.crc_bytes += slot.profile.crc_bytes;
            cpu_profile.stream_decode_ms += slot.profile.stream_decode_ms;
            cpu_profile.iv_expand_ms += slot.profile.iv_expand_ms;
            cpu_profile.rebuild_encrypt_ms += slot.profile.rebuild_encrypt_ms;
            cpu_profile.crc_ms += slot.profile.crc_ms;
            internal_crc_mismatches += slot.internal_crc_mismatches;
            source_compare_mismatches += slot.source_compare_mismatches;
        }
        cpu_profile.total_ms = decode_watch.elapsed_ms();
        cpu_profile.backend = "cpu";
    }

    // build_verify_report_json only reads lanes.size(). Pass a vector of
    // metadata-only placeholders so the report shape matches the legacy output
    // bit-for-bit without ever materializing decoded payloads.
    std::vector<Spc3DecodedLane> lane_placeholders(container.entries.size());
    for (size_t i = 0; i < container.entries.size(); ++i) {
        lane_placeholders[i].lane = container.entries[i].lane;
    }

    const double ms = watch.elapsed_ms();
    write_text_file(options.report, build_verify_report_json(
        options,
        container,
        lane_placeholders,
        internal_crc_mismatches,
        source_compare_mismatches,
        gpu_result,
        cpu_profile,
        ms));
    if (internal_crc_mismatches != 0 || source_compare_mismatches != 0) {
        throw std::runtime_error("SPC3 verify failed");
    }
    std::cout << "verify_ok lanes=" << container.entries.size() << "\n";
}

void run_inspect_mode(const Options& options) {
    if (options.input.empty()) {
        throw std::runtime_error("--input is required for inspect");
    }
    Stopwatch watch;
    const std::vector<uint8_t> bytes = read_file_bytes(options.input);
    const Spc3Container container = parse_spc3_file(bytes);
    const double ms = watch.elapsed_ms();
    write_text_file(options.report, build_inspect_report_json(
        options,
        container,
        bytes.size(),
        ms));
    std::cout << "inspect_ok level=" << container.level
              << " lanes=" << container.entries.size()
              << " bytes=" << bytes.size()
              << " predictor_embedded=" << (spc3_predictor_embedded(container) ? "true" : "false")
              << "\n";
}

struct ExternalBenchResult {
    std::string status = "not_run";
    uint64_t size_bytes = 0;
    double ms = 0;
};

struct NativeCodecBenchResult {
    std::string status = "not_run";
    CodecSpec codec;
    uint32_t spc3_level = 0;
    uint64_t size_bytes = 0;
    double build_ms = 0;
    double unpack_ms = 0;
    double verify_ms = 0;
    double decode_mib_s = 0;
    uint64_t decode_crc_mismatches = 0;
};

std::vector<CodecSpec> selected_native_codecs(const Options& options) {
    if (options.bench_codecs_set) {
        return options.bench_codecs;
    }
    return {
        {CodecId::Zlib, kZlibDefaultLevel, 0},
        {CodecId::Zstd, 3, 0},
        {CodecId::Zstd, 9, 0},
        {CodecId::Zstd, 19, 0},
        {CodecId::Lzma2, kLzma2DefaultPreset, 0},
    };
}

std::vector<std::pair<uint32_t, CodecSpec>> selected_native_jobs(const Options& options) {
    std::vector<std::pair<uint32_t, CodecSpec>> jobs;
    if (!options.bench_native_codecs) {
        return jobs;
    }
    const std::vector<CodecSpec> codecs = selected_native_codecs(options);
    for (const uint32_t level : options.bench_levels) {
        for (const CodecSpec& codec : codecs) {
            jobs.push_back({level, codec});
        }
    }
    return jobs;
}

struct TypedLevel3BenchResult {
    std::string status = "not_run";
    TypedLevel3Policy policy;
    uint64_t template_stream_bytes = 0;
    uint64_t bitmap_stream_bytes = 0;
    uint64_t values_stream_bytes = 0;
    uint64_t substream_bytes = 0;
    uint64_t uncompressed_model_size = 0;
    uint64_t size_bytes = 0;
    double build_ms = 0;
    double unpack_ms = 0;
    double verify_ms = 0;
    double decode_mib_s = 0;
    uint64_t decode_crc_mismatches = 0;
};

std::vector<TypedLevel3Policy> selected_typed_level3_policies(const Options& options) {
    std::vector<TypedLevel3Policy> policies;
    if (!options.bench_typed_level3) {
        return policies;
    }

    const CodecSpec none{CodecId::None, 0, 0};
    policies.push_back({"raw", none, none, none});
    for (const CodecSpec& codec : selected_native_codecs(options)) {
        const std::string display = codec_display_name(codec);
        policies.push_back({"all-" + display, codec, codec, codec});
        policies.push_back({"exceptions-" + display, none, codec, codec});
    }
    if (options.bench_rans_fse) {
        const CodecSpec rans{CodecId::Rans, 0, 0};
        policies.push_back({"exceptions-rans", none, rans, rans});
    }
    return policies;
}

struct StreamingBenchLevelResult {
    uint64_t stream_bytes = 0;
    uint64_t size_bytes = 0;
    double build_ms = 0;
    double unpack_ms = 0;
    double verify_ms = 0;
    double decode_mib_s = 0;
    uint64_t decode_crc_mismatches = 0;
};

struct StreamingBenchSampleResult {
    uint32_t lane_count = 0;
    uint64_t source_zip_bytes = 0;
    uint64_t raw_payload_bytes = 0;
    uint64_t predictor_matches = 0;
    uint64_t predictor_exceptions = 0;
    uint64_t lanes_with_exceptions = 0;
    uint64_t min_predictor_exceptions = std::numeric_limits<uint64_t>::max();
    uint64_t max_predictor_exceptions = 0;
    uint64_t exception_bitmap_bytes = 0;
    uint64_t exception_value_bytes = 0;
    uint64_t xor_zero_values = 0;
    std::array<uint64_t, 256> xor_low_byte_histogram{};
    std::array<StreamingBenchLevelResult, 4> spc3_levels{};
    std::vector<NativeCodecBenchResult> native_codec_matrix;
    std::vector<TypedLevel3BenchResult> typed_level3_matrix;
    GpuOffloadBenchResult gpu_offload;
};

struct LaneStreamBenchResult {
    uint64_t stream_size = 0;
    uint64_t uncompressed_model_size = 0;
    double build_ms = 0;
    double unpack_ms = 0;
    double verify_ms = 0;
    uint64_t decode_crc_mismatches = 0;
};

uint64_t spc3_streaming_container_overhead(uint32_t lane_count, uint32_t level, uint64_t predictor_stream_size) {
    const uint64_t table_bytes = static_cast<uint64_t>(lane_count) * kSpc3TableEntrySize;
    return kSpc3HeaderSize + table_bytes + (level == 3 ? predictor_stream_size : 0);
}

uint64_t typed_level3_streaming_container_overhead(uint32_t lane_count, uint64_t predictor_stream_size) {
    const uint64_t table_bytes = static_cast<uint64_t>(lane_count) * kSpc3TableEntrySize;
    const uint64_t substream_table_bytes =
        static_cast<uint64_t>(lane_count) * kSpc3TypedLevel3SubstreamCount * kSpc3TypedLevel3SubstreamEntrySize;
    return kSpc3HeaderSize + predictor_stream_size + table_bytes + substream_table_bytes;
}

uint32_t byte_popcount(uint8_t byte) {
    uint32_t count = 0;
    while (byte) {
        count += byte & 1U;
        byte >>= 1;
    }
    return count;
}

#if defined(_WIN32)
using CUdevice = int;
using CUresult = int;
using CUdeviceptr = unsigned long long;
using CUcontext = void*;
using CUmodule = void*;
using CUfunction = void*;
using nvrtcProgram = void*;
using nvrtcResult = int;

struct CudaDriverApi {
    HMODULE cuda = nullptr;
    HMODULE nvrtc = nullptr;
    CUresult (__stdcall *cuInit)(unsigned int) = nullptr;
    CUresult (__stdcall *cuDeviceGetCount)(int*) = nullptr;
    CUresult (__stdcall *cuDeviceGet)(CUdevice*, int) = nullptr;
    CUresult (__stdcall *cuDeviceGetName)(char*, int, CUdevice) = nullptr;
    CUresult (__stdcall *cuCtxCreate)(CUcontext*, unsigned int, CUdevice) = nullptr;
    CUresult (__stdcall *cuCtxDestroy)(CUcontext) = nullptr;
    CUresult (__stdcall *cuCtxSetCurrent)(CUcontext) = nullptr;
    CUresult (__stdcall *cuModuleLoadData)(CUmodule*, const void*) = nullptr;
    CUresult (__stdcall *cuModuleUnload)(CUmodule) = nullptr;
    CUresult (__stdcall *cuModuleGetFunction)(CUfunction*, CUmodule, const char*) = nullptr;
    CUresult (__stdcall *cuMemAlloc)(CUdeviceptr*, size_t) = nullptr;
    CUresult (__stdcall *cuMemFree)(CUdeviceptr) = nullptr;
    CUresult (__stdcall *cuMemcpyHtoD)(CUdeviceptr, const void*, size_t) = nullptr;
    CUresult (__stdcall *cuMemcpyDtoH)(void*, CUdeviceptr, size_t) = nullptr;
    CUresult (__stdcall *cuLaunchKernel)(CUfunction, unsigned int, unsigned int, unsigned int,
        unsigned int, unsigned int, unsigned int, unsigned int, void*, void**, void**) = nullptr;
    CUresult (__stdcall *cuCtxSynchronize)() = nullptr;
    CUresult (__stdcall *cuGetErrorName)(CUresult, const char**) = nullptr;
    CUresult (__stdcall *cuGetErrorString)(CUresult, const char**) = nullptr;

    nvrtcResult (__stdcall *nvrtcCreateProgram)(nvrtcProgram*, const char*, const char*, int,
        const char* const*, const char* const*) = nullptr;
    nvrtcResult (__stdcall *nvrtcCompileProgram)(nvrtcProgram, int, const char* const*) = nullptr;
    nvrtcResult (__stdcall *nvrtcGetPTXSize)(nvrtcProgram, size_t*) = nullptr;
    nvrtcResult (__stdcall *nvrtcGetPTX)(nvrtcProgram, char*) = nullptr;
    nvrtcResult (__stdcall *nvrtcGetProgramLogSize)(nvrtcProgram, size_t*) = nullptr;
    nvrtcResult (__stdcall *nvrtcGetProgramLog)(nvrtcProgram, char*) = nullptr;
    nvrtcResult (__stdcall *nvrtcDestroyProgram)(nvrtcProgram*) = nullptr;
    const char* (__stdcall *nvrtcGetErrorString)(nvrtcResult) = nullptr;
};

HMODULE load_cuda_library(const char* name) {
    HMODULE module = LoadLibraryA(name);
    if (module != nullptr) {
        return module;
    }
    const char* cuda_path = std::getenv("CUDA_PATH");
    if (cuda_path == nullptr || *cuda_path == '\0') {
        return nullptr;
    }
    const std::string full_path = std::string(cuda_path) + "\\bin\\" + name;
    return LoadLibraryA(full_path.c_str());
}

template <typename T>
void load_cuda_symbol(HMODULE module, const char* name, T& target) {
    FARPROC proc = GetProcAddress(module, name);
    if (proc == nullptr) {
        throw std::runtime_error(std::string("missing CUDA symbol ") + name);
    }
    static_assert(sizeof(target) == sizeof(proc));
    std::memcpy(&target, &proc, sizeof(target));
}

CudaDriverApi load_cuda_driver_api() {
    CudaDriverApi api;
    api.cuda = load_cuda_library("nvcuda.dll");
    api.nvrtc = load_cuda_library("nvrtc64_120_0.dll");
    if (api.cuda == nullptr) {
        throw std::runtime_error("nvcuda.dll not found");
    }
    if (api.nvrtc == nullptr) {
        throw std::runtime_error("nvrtc64_120_0.dll not found");
    }

    load_cuda_symbol(api.cuda, "cuInit", api.cuInit);
    load_cuda_symbol(api.cuda, "cuDeviceGetCount", api.cuDeviceGetCount);
    load_cuda_symbol(api.cuda, "cuDeviceGet", api.cuDeviceGet);
    load_cuda_symbol(api.cuda, "cuDeviceGetName", api.cuDeviceGetName);
    load_cuda_symbol(api.cuda, "cuCtxCreate_v2", api.cuCtxCreate);
    load_cuda_symbol(api.cuda, "cuCtxDestroy_v2", api.cuCtxDestroy);
    load_cuda_symbol(api.cuda, "cuCtxSetCurrent", api.cuCtxSetCurrent);
    load_cuda_symbol(api.cuda, "cuModuleLoadData", api.cuModuleLoadData);
    load_cuda_symbol(api.cuda, "cuModuleUnload", api.cuModuleUnload);
    load_cuda_symbol(api.cuda, "cuModuleGetFunction", api.cuModuleGetFunction);
    load_cuda_symbol(api.cuda, "cuMemAlloc_v2", api.cuMemAlloc);
    load_cuda_symbol(api.cuda, "cuMemFree_v2", api.cuMemFree);
    load_cuda_symbol(api.cuda, "cuMemcpyHtoD_v2", api.cuMemcpyHtoD);
    load_cuda_symbol(api.cuda, "cuMemcpyDtoH_v2", api.cuMemcpyDtoH);
    load_cuda_symbol(api.cuda, "cuLaunchKernel", api.cuLaunchKernel);
    load_cuda_symbol(api.cuda, "cuCtxSynchronize", api.cuCtxSynchronize);
    load_cuda_symbol(api.cuda, "cuGetErrorName", api.cuGetErrorName);
    load_cuda_symbol(api.cuda, "cuGetErrorString", api.cuGetErrorString);

    load_cuda_symbol(api.nvrtc, "nvrtcCreateProgram", api.nvrtcCreateProgram);
    load_cuda_symbol(api.nvrtc, "nvrtcCompileProgram", api.nvrtcCompileProgram);
    load_cuda_symbol(api.nvrtc, "nvrtcGetPTXSize", api.nvrtcGetPTXSize);
    load_cuda_symbol(api.nvrtc, "nvrtcGetPTX", api.nvrtcGetPTX);
    load_cuda_symbol(api.nvrtc, "nvrtcGetProgramLogSize", api.nvrtcGetProgramLogSize);
    load_cuda_symbol(api.nvrtc, "nvrtcGetProgramLog", api.nvrtcGetProgramLog);
    load_cuda_symbol(api.nvrtc, "nvrtcDestroyProgram", api.nvrtcDestroyProgram);
    load_cuda_symbol(api.nvrtc, "nvrtcGetErrorString", api.nvrtcGetErrorString);
    return api;
}

std::string cuda_error_string(const CudaDriverApi& api, CUresult result) {
    const char* name = nullptr;
    const char* detail = nullptr;
    if (api.cuGetErrorName != nullptr) {
        (void)api.cuGetErrorName(result, &name);
    }
    if (api.cuGetErrorString != nullptr) {
        (void)api.cuGetErrorString(result, &detail);
    }
    std::ostringstream out;
    out << (name ? name : "CUDA_ERROR") << "(" << result << ")";
    if (detail != nullptr) {
        out << ": " << detail;
    }
    return out.str();
}

void check_cuda(const CudaDriverApi& api, CUresult result, const char* op) {
    if (result != 0) {
        throw std::runtime_error(std::string(op) + " failed: " + cuda_error_string(api, result));
    }
}

void check_nvrtc(const CudaDriverApi& api, nvrtcResult result, const char* op) {
    if (result != 0) {
        const char* detail = api.nvrtcGetErrorString ? api.nvrtcGetErrorString(result) : "NVRTC error";
        throw std::runtime_error(std::string(op) + " failed: " + detail);
    }
}

const char* spc3_cuda_rebuild_kernel_source() {
    return R"CUDA(
__device__ __constant__ unsigned char kBlockPosition[24][4] = {
    {0,1,2,3},{0,1,3,2},{0,2,1,3},{0,3,1,2},{0,2,3,1},{0,3,2,1},
    {1,0,2,3},{1,0,3,2},{2,0,1,3},{3,0,1,2},{2,0,3,1},{3,0,2,1},
    {1,2,0,3},{1,3,0,2},{2,1,0,3},{3,1,0,2},{2,3,0,1},{3,2,0,1},
    {1,2,3,0},{1,3,2,0},{2,1,3,0},{3,1,2,0},{2,3,1,0},{3,2,1,0}
};
__device__ __constant__ unsigned char kInvertSelector[24] = {
    0,1,2,4,3,5,6,7,12,18,13,19,8,10,14,20,16,22,9,11,15,21,17,23
};

__device__ unsigned int load_le32_device(const unsigned char* p) {
    return (unsigned int)p[0] | ((unsigned int)p[1] << 8) |
           ((unsigned int)p[2] << 16) | ((unsigned int)p[3] << 24);
}

__device__ void store_le16_device(unsigned char* p, unsigned int value) {
    p[0] = (unsigned char)(value & 0xffu);
    p[1] = (unsigned char)((value >> 8) & 0xffu);
}

__device__ void store_le32_device(unsigned char* p, unsigned int value) {
    p[0] = (unsigned char)(value & 0xffu);
    p[1] = (unsigned char)((value >> 8) & 0xffu);
    p[2] = (unsigned char)((value >> 16) & 0xffu);
    p[3] = (unsigned char)((value >> 24) & 0xffu);
}

__device__ unsigned int checksum_device(const unsigned char* record) {
    unsigned int sum = 0;
    for (int offset = 0x20; offset < 0x50; offset += 2) {
        sum += (unsigned int)record[offset] | ((unsigned int)record[offset + 1] << 8);
    }
    return sum & 0xffffu;
}

__device__ void shuffle_device(unsigned char* record, const unsigned char* order) {
    unsigned char copy[48];
    for (int i = 0; i < 48; ++i) {
        copy[i] = record[0x20 + i];
    }
    for (int block = 0; block < 4; ++block) {
        const int src = order[block] * 12;
        const int dst = 0x20 + block * 12;
        for (int j = 0; j < 12; ++j) {
            record[dst + j] = copy[src + j];
        }
    }
}

extern "C" __global__ void spc3_rebuild_level3_kernel(
    const unsigned char* templates,
    const unsigned int* predictor,
    const unsigned char* bitmaps,
    const unsigned int* values,
    const unsigned int* value_offsets,
    const unsigned int* prefixes,
    const unsigned short* lanes,
    unsigned char* output,
    unsigned int lane_count)
{
    const unsigned int index = blockIdx.x * blockDim.x + threadIdx.x;
    const unsigned int total = lane_count * 65536u;
    if (index >= total) {
        return;
    }
    const unsigned int lane_index = index >> 16;
    const unsigned int upper = index & 0xffffu;
    const unsigned int byte_index = upper >> 3;
    const unsigned int bit = upper & 7u;
    const unsigned char bitmap_byte = bitmaps[lane_index * 8192u + byte_index];

    unsigned int iv32 = predictor[upper];
    if ((bitmap_byte & (1u << bit)) != 0) {
        const unsigned int prior_in_byte = __popc((unsigned int)(bitmap_byte & ((1u << bit) - 1u)));
        const unsigned int value_index =
            value_offsets[lane_index] + prefixes[lane_index * 8193u + byte_index] + prior_in_byte;
        iv32 ^= values[value_index];
    }

    unsigned char record[80];
    const unsigned char* tmpl = templates + lane_index * 80u;
    for (int i = 0; i < 80; ++i) {
        record[i] = tmpl[i];
    }

    const unsigned int pid = (upper << 16) | (unsigned int)lanes[lane_index];
    store_le32_device(record, pid);
    store_le32_device(record + 0x48, iv32);
    store_le16_device(record + 0x1c, checksum_device(record));

    const unsigned int oid = load_le32_device(record + 4);
    const unsigned char inverse = kInvertSelector[pid % 24u];
    shuffle_device(record, kBlockPosition[inverse]);
    const unsigned int key = pid ^ oid;
    for (int offset = 0x20; offset < 0x50; offset += 4) {
        store_le32_device(record + offset, load_le32_device(record + offset) ^ key);
    }

    unsigned char* dst = output + (unsigned long long)index * 80ull;
    for (int i = 0; i < 80; ++i) {
        dst[i] = record[i];
    }
}
)CUDA";
}

std::string compile_spc3_cuda_kernel(CudaDriverApi& api, double& compile_ms) {
    Stopwatch watch;
    nvrtcProgram program = nullptr;
    auto destroy_program = [&]() {
        if (program != nullptr && api.nvrtcDestroyProgram != nullptr) {
            (void)api.nvrtcDestroyProgram(&program);
            program = nullptr;
        }
    };
    check_nvrtc(api, api.nvrtcCreateProgram(
        &program,
        spc3_cuda_rebuild_kernel_source(),
        "spc3_rebuild_level3.cu",
        0,
        nullptr,
        nullptr), "nvrtcCreateProgram");
    try {
        const char* options[] = {"--std=c++11", "--gpu-architecture=compute_52"};
        const nvrtcResult compile_result = api.nvrtcCompileProgram(program, 2, options);
        if (compile_result != 0) {
            size_t log_size = 0;
            (void)api.nvrtcGetProgramLogSize(program, &log_size);
            std::string log(log_size == 0 ? 1 : log_size, '\0');
            if (log_size != 0) {
                (void)api.nvrtcGetProgramLog(program, log.data());
            }
            destroy_program();
            throw std::runtime_error(std::string("nvrtcCompileProgram failed: ") +
                (api.nvrtcGetErrorString ? api.nvrtcGetErrorString(compile_result) : "NVRTC error") +
                "\n" + log);
        }
        size_t ptx_size = 0;
        check_nvrtc(api, api.nvrtcGetPTXSize(program, &ptx_size), "nvrtcGetPTXSize");
        std::string ptx(ptx_size, '\0');
        check_nvrtc(api, api.nvrtcGetPTX(program, ptx.data()), "nvrtcGetPTX");
        const nvrtcResult destroy_result = api.nvrtcDestroyProgram(&program);
        program = nullptr;
        check_nvrtc(api, destroy_result, "nvrtcDestroyProgram");
        compile_ms = watch.elapsed_ms();
        return ptx;
    } catch (...) {
        destroy_program();
        throw;
    }
}

struct CudaRebuildRuntime {
    std::mutex mutex;
    CudaDriverApi api;
    CUcontext context = nullptr;
    CUmodule module = nullptr;
    CUfunction kernel = nullptr;
    std::string device_name;
    std::string cached_failure_reason;
    uint64_t initialization_count = 0;
    bool initialized = false;
    bool cached_failure = false;

    ~CudaRebuildRuntime() {
        cleanup();
    }

    void cleanup() {
        if (api.cuCtxSetCurrent != nullptr && context != nullptr) {
            (void)api.cuCtxSetCurrent(context);
        }
        if (api.cuModuleUnload != nullptr && module != nullptr) {
            (void)api.cuModuleUnload(module);
        }
        module = nullptr;
        kernel = nullptr;
        if (api.cuCtxDestroy != nullptr && context != nullptr) {
            (void)api.cuCtxDestroy(context);
        }
        context = nullptr;
        initialized = false;
    }

    void initialize(
        double& compile_ms,
        bool& cache_hit,
        bool& failure_cached,
        uint64_t& runtime_initializations)
    {
        compile_ms = 0;
        cache_hit = false;
        failure_cached = false;
        runtime_initializations = initialization_count;
        if (initialized) {
            check_cuda(api, api.cuCtxSetCurrent(context), "cuCtxSetCurrent cached context");
            cache_hit = true;
            runtime_initializations = initialization_count;
            return;
        }
        if (cached_failure) {
            failure_cached = true;
            throw std::runtime_error(cached_failure_reason);
        }
        try {
            api = load_cuda_driver_api();
            check_cuda(api, api.cuInit(0), "cuInit");
            int device_count = 0;
            check_cuda(api, api.cuDeviceGetCount(&device_count), "cuDeviceGetCount");
            if (device_count <= 0) {
                throw std::runtime_error("no CUDA device");
            }
            CUdevice device = 0;
            check_cuda(api, api.cuDeviceGet(&device, 0), "cuDeviceGet");
            char name[256] = {};
            check_cuda(api, api.cuDeviceGetName(name, sizeof(name), device), "cuDeviceGetName");
            device_name = name;
            check_cuda(api, api.cuCtxCreate(&context, 0, device), "cuCtxCreate");
            check_cuda(api, api.cuCtxSetCurrent(context), "cuCtxSetCurrent");
            const std::string ptx = compile_spc3_cuda_kernel(api, compile_ms);
            check_cuda(api, api.cuModuleLoadData(&module, ptx.data()), "cuModuleLoadData");
            check_cuda(api, api.cuModuleGetFunction(&kernel, module, "spc3_rebuild_level3_kernel"),
                "cuModuleGetFunction");
            initialized = true;
            ++initialization_count;
            runtime_initializations = initialization_count;
            return;
        } catch (const std::exception& error) {
            cached_failure = true;
            cached_failure_reason = error.what();
            cleanup();
            throw;
        } catch (...) {
            cached_failure = true;
            cached_failure_reason = "unknown CUDA initialization failure";
            cleanup();
            throw;
        }
    }
};

CudaRebuildRuntime& cuda_rebuild_runtime() {
    static CudaRebuildRuntime runtime;
    return runtime;
}

GpuOffloadBenchResult run_gpu_level3_rebuild(
    const std::vector<GpuLevel3LaneInput>& inputs,
    const PredictorTable& predictor,
    bool compare_cpu,
    std::vector<Spc3DecodedLane>* decoded_lanes,
    const Spc3DecodedLaneSink* lane_sink)
{
    GpuOffloadBenchResult result;
    if (inputs.size() > std::numeric_limits<uint32_t>::max()) {
        result.status = "skipped_bad_lane_count";
        result.fallback_reason = "too many lanes for GPU rebuild";
        return result;
    }
    const uint32_t lane_count = static_cast<uint32_t>(inputs.size());
    result.lane_count = lane_count;
    result.compare_mode = compare_cpu ? "cpu_rebuild_per_lane" : "none";
    if (!predictor.loaded) {
        result.status = "skipped_no_predictor";
        result.fallback_reason = "predictor table is not loaded";
        return result;
    }
    if (lane_count == 0) {
        result.status = "skipped_bad_lane_count";
        result.fallback_reason = "no lanes were provided for GPU rebuild";
        return result;
    }
    if (const char* disabled = std::getenv("SPC3_DISABLE_CUDA"); disabled != nullptr && *disabled != '\0') {
        result.status = "cuda_disabled_by_environment";
        result.fallback_reason = "SPC3_DISABLE_CUDA is set";
        return result;
    }
    if (const char* forced = std::getenv("SPC3_FORCE_GPU_REBUILD_FAILURE"); forced != nullptr && *forced != '\0') {
        result.status = "forced_gpu_rebuild_failure";
        result.fallback_reason = "SPC3_FORCE_GPU_REBUILD_FAILURE is set";
        return result;
    }
    const bool emit_to_memory = decoded_lanes != nullptr;
    const bool emit_to_sink = lane_sink != nullptr && lane_sink->emit != nullptr;
    if (emit_to_memory) {
        decoded_lanes->clear();
        decoded_lanes->reserve(inputs.size());
    }

    Stopwatch total_watch;
    CUdeviceptr d_templates = 0;
    CUdeviceptr d_predictor = 0;
    CUdeviceptr d_bitmaps = 0;
    CUdeviceptr d_values = 0;
    CUdeviceptr d_value_offsets = 0;
    CUdeviceptr d_prefixes = 0;
    CUdeviceptr d_lanes = 0;
    CUdeviceptr d_output = 0;
    CudaDriverApi* cleanup_api = nullptr;
    auto free_device_buffers = [&]() {
        if (cleanup_api == nullptr || cleanup_api->cuMemFree == nullptr) {
            return;
        }
        if (d_output) { (void)cleanup_api->cuMemFree(d_output); d_output = 0; }
        if (d_lanes) { (void)cleanup_api->cuMemFree(d_lanes); d_lanes = 0; }
        if (d_prefixes) { (void)cleanup_api->cuMemFree(d_prefixes); d_prefixes = 0; }
        if (d_value_offsets) { (void)cleanup_api->cuMemFree(d_value_offsets); d_value_offsets = 0; }
        if (d_values) { (void)cleanup_api->cuMemFree(d_values); d_values = 0; }
        if (d_bitmaps) { (void)cleanup_api->cuMemFree(d_bitmaps); d_bitmaps = 0; }
        if (d_predictor) { (void)cleanup_api->cuMemFree(d_predictor); d_predictor = 0; }
        if (d_templates) { (void)cleanup_api->cuMemFree(d_templates); d_templates = 0; }
    };

    try {
        CudaRebuildRuntime& runtime = cuda_rebuild_runtime();
        std::lock_guard<std::mutex> runtime_lock(runtime.mutex);
        runtime.initialize(
            result.compile_ms,
            result.runtime_cache_hit,
            result.runtime_failure_cached,
            result.runtime_initializations);
        CudaDriverApi& api = runtime.api;
        cleanup_api = &api;
        CUfunction kernel = runtime.kernel;
        result.device_name = runtime.device_name;

        const size_t template_bytes = checked_u64_to_size(
            checked_mul_u64(lane_count, kRecordSize, "GPU template staging size"),
            "GPU template staging size");
        const size_t bitmap_bytes = checked_u64_to_size(
            checked_mul_u64(lane_count, kExpectedRecords / 8ULL, "GPU bitmap staging size"),
            "GPU bitmap staging size");
        const size_t prefix_count = checked_u64_to_size(
            checked_mul_u64(lane_count, kExpectedRecords / 8ULL + 1ULL, "GPU prefix staging count"),
            "GPU prefix staging count");
        std::vector<uint8_t> templates(template_bytes);
        std::vector<uint8_t> bitmaps(bitmap_bytes);
        std::vector<uint32_t> prefixes(prefix_count);
        std::vector<uint32_t> values;
        uint64_t total_value_count = 0;
        for (const GpuLevel3LaneInput& input : inputs) {
            if (input.exception_bitmap.size() != kExpectedRecords / 8ULL ||
                input.exception_values.size() % 4ULL != 0) {
                throw std::runtime_error("GPU rebuild typed input sizes are invalid");
            }
            total_value_count = checked_add_u64(
                total_value_count,
                input.exception_values.size() / 4,
                "GPU rebuild exception value count");
            if (total_value_count > std::numeric_limits<uint32_t>::max()) {
                throw std::runtime_error("GPU rebuild exception values exceed u32 offset range");
            }
        }
        values.reserve(checked_u64_to_size(total_value_count, "GPU rebuild exception value count"));
        std::vector<uint32_t> value_offsets(lane_count);
        std::vector<uint16_t> lanes(lane_count);
        for (uint32_t lane_index = 0; lane_index < lane_count; ++lane_index) {
            const GpuLevel3LaneInput& input = inputs[lane_index];
            lanes[lane_index] = input.lane;
            std::memcpy(
                templates.data() + static_cast<size_t>(lane_index) * kRecordSize,
                input.template_record.data(),
                kRecordSize);
            std::memcpy(
                bitmaps.data() + static_cast<size_t>(lane_index) * (kExpectedRecords / 8),
                input.exception_bitmap.data(),
                kExpectedRecords / 8);
            value_offsets[lane_index] = static_cast<uint32_t>(values.size());

            uint32_t running = 0;
            const size_t prefix_base = static_cast<size_t>(lane_index) * (kExpectedRecords / 8 + 1);
            const size_t bitmap_base = static_cast<size_t>(lane_index) * (kExpectedRecords / 8);
            for (uint32_t byte_index = 0; byte_index < kExpectedRecords / 8; ++byte_index) {
                prefixes[prefix_base + byte_index] = running;
                running += byte_popcount(bitmaps[bitmap_base + byte_index]);
            }
            prefixes[prefix_base + kExpectedRecords / 8] = running;
            if (input.exception_values.size() != static_cast<size_t>(running) * 4) {
                throw std::runtime_error("GPU probe exception value count mismatch");
            }
            for (size_t offset = 0; offset < input.exception_values.size(); offset += 4) {
                values.push_back(load_le32(input.exception_values.data() + offset));
            }
        }
        result.value_count = values.size();
        result.output_bytes = static_cast<uint64_t>(lane_count) * kPayloadSize;

        auto gpu_alloc = [&](CUdeviceptr& ptr, size_t bytes, const char* label) {
            if (bytes == 0) {
                ptr = 0;
                return;
            }
            check_cuda(api, api.cuMemAlloc(&ptr, bytes), label);
        };
        auto gpu_copy_h2d = [&](CUdeviceptr ptr, const void* src, size_t bytes, const char* label) {
            if (bytes != 0) {
                check_cuda(api, api.cuMemcpyHtoD(ptr, src, bytes), label);
            }
        };

        {
            Stopwatch upload_watch;
            gpu_alloc(d_templates, templates.size(), "cuMemAlloc templates");
            gpu_alloc(d_predictor, predictor.iv32.size() * sizeof(uint32_t), "cuMemAlloc predictor");
            gpu_alloc(d_bitmaps, bitmaps.size(), "cuMemAlloc bitmaps");
            gpu_alloc(d_values, values.size() * sizeof(uint32_t), "cuMemAlloc values");
            gpu_alloc(d_value_offsets, value_offsets.size() * sizeof(uint32_t), "cuMemAlloc value offsets");
            gpu_alloc(d_prefixes, prefixes.size() * sizeof(uint32_t), "cuMemAlloc prefixes");
            gpu_alloc(d_lanes, lanes.size() * sizeof(uint16_t), "cuMemAlloc lanes");
            gpu_alloc(d_output, checked_u64_to_size(result.output_bytes, "GPU output bytes"), "cuMemAlloc output");
            gpu_copy_h2d(d_templates, templates.data(), templates.size(), "cuMemcpyHtoD templates");
            gpu_copy_h2d(d_predictor, predictor.iv32.data(), predictor.iv32.size() * sizeof(uint32_t),
                "cuMemcpyHtoD predictor");
            gpu_copy_h2d(d_bitmaps, bitmaps.data(), bitmaps.size(), "cuMemcpyHtoD bitmaps");
            gpu_copy_h2d(d_values, values.data(), values.size() * sizeof(uint32_t), "cuMemcpyHtoD values");
            gpu_copy_h2d(d_value_offsets, value_offsets.data(), value_offsets.size() * sizeof(uint32_t),
                "cuMemcpyHtoD value offsets");
            gpu_copy_h2d(d_prefixes, prefixes.data(), prefixes.size() * sizeof(uint32_t),
                "cuMemcpyHtoD prefixes");
            gpu_copy_h2d(d_lanes, lanes.data(), lanes.size() * sizeof(uint16_t), "cuMemcpyHtoD lanes");
            result.upload_ms = upload_watch.elapsed_ms();
        }

        {
            Stopwatch kernel_watch;
            if (lane_count > std::numeric_limits<uint32_t>::max() / kExpectedRecords) {
                throw std::runtime_error("GPU rebuild lane count exceeds u32 record range");
            }
            const uint64_t total_records = static_cast<uint64_t>(lane_count) * kExpectedRecords;
            const uint32_t block_size = 256;
            const uint64_t grid_size_u64 = (total_records + block_size - 1) / block_size;
            if (grid_size_u64 > std::numeric_limits<uint32_t>::max()) {
                throw std::runtime_error("GPU rebuild grid size exceeds CUDA launch limit");
            }
            const uint32_t grid_size = static_cast<uint32_t>(grid_size_u64);
            uint32_t kernel_lane_count = lane_count;
            void* args[] = {
                &d_templates,
                &d_predictor,
                &d_bitmaps,
                &d_values,
                &d_value_offsets,
                &d_prefixes,
                &d_lanes,
                &d_output,
                &kernel_lane_count,
            };
            check_cuda(api, api.cuLaunchKernel(
                kernel,
                grid_size, 1, 1,
                block_size, 1, 1,
                0,
                nullptr,
                args,
                nullptr), "cuLaunchKernel");
            check_cuda(api, api.cuCtxSynchronize(), "cuCtxSynchronize");
            result.kernel_ms = kernel_watch.elapsed_ms();
        }

        const bool bulk_download = result.output_bytes <= kGpuBulkDownloadLimitBytes;
        result.download_mode = bulk_download ? "bulk" : "per_lane";
        if (bulk_download) {
            std::vector<uint8_t> output(checked_u64_to_size(result.output_bytes, "GPU host output bytes"));
            {
                Stopwatch download_watch;
                check_cuda(api, api.cuMemcpyDtoH(output.data(), d_output, output.size()),
                    "cuMemcpyDtoH output bulk");
                result.download_ms += download_watch.elapsed_ms();
            }
            for (uint32_t lane_index = 0; lane_index < lane_count; ++lane_index) {
                const uint8_t* lane_data = output.data() + static_cast<size_t>(lane_index) * kPayloadSize;
                uint32_t payload_crc32 = 0;
                if (emit_to_memory || emit_to_sink) {
                    ScopedTimer crc_timer(result.host_crc_ms);
                    payload_crc32 = crc32_bytes(lane_data, kPayloadSize);
                }
                if (emit_to_sink) {
                    lane_sink->emit(
                        lane_sink->user,
                        lane_index,
                        inputs[lane_index].lane,
                        lane_data,
                        kPayloadSize,
                        payload_crc32);
                }
                if (emit_to_memory) {
                    Spc3DecodedLane decoded;
                    decoded.lane = inputs[lane_index].lane;
                    decoded.payload_crc32 = payload_crc32;
                    decoded.payload.assign(lane_data, lane_data + kPayloadSize);
                    decoded_lanes->push_back(std::move(decoded));
                }
                if (compare_cpu) {
                    Stopwatch compare_watch;
                    const GpuLevel3LaneInput& input = inputs[lane_index];
                    const std::vector<uint8_t> cpu_lane = rebuild_payload_from_template_exceptions(
                        input.lane,
                        input.template_record.data(),
                        input.exception_bitmap.data(),
                        input.exception_bitmap.size(),
                        input.exception_values.data(),
                        input.exception_values.size(),
                        predictor);
                    if (std::memcmp(lane_data, cpu_lane.data(), kPayloadSize) != 0) {
                        ++result.mismatched_lanes;
                        for (size_t i = 0; i < kPayloadSize; ++i) {
                            if (lane_data[i] != cpu_lane[i]) {
                                ++result.mismatched_bytes;
                            }
                        }
                    }
                    result.compare_ms += compare_watch.elapsed_ms();
                }
            }
        } else {
            std::vector<uint8_t> lane_output(kPayloadSize);
            for (uint32_t lane_index = 0; lane_index < lane_count; ++lane_index) {
                const CUdeviceptr lane_ptr = d_output + static_cast<uint64_t>(lane_index) * kPayloadSize;
                {
                    Stopwatch download_watch;
                    check_cuda(api, api.cuMemcpyDtoH(lane_output.data(), lane_ptr, lane_output.size()),
                        "cuMemcpyDtoH output lane");
                    result.download_ms += download_watch.elapsed_ms();
                }
                uint32_t payload_crc32 = 0;
                if (emit_to_memory || emit_to_sink) {
                    ScopedTimer crc_timer(result.host_crc_ms);
                    payload_crc32 = crc32_vector(lane_output);
                }
                if (emit_to_sink) {
                    lane_sink->emit(
                        lane_sink->user,
                        lane_index,
                        inputs[lane_index].lane,
                        lane_output.data(),
                        kPayloadSize,
                        payload_crc32);
                }
                if (emit_to_memory) {
                    Spc3DecodedLane decoded;
                    decoded.lane = inputs[lane_index].lane;
                    decoded.payload_crc32 = payload_crc32;
                    decoded.payload = lane_output;
                    decoded_lanes->push_back(std::move(decoded));
                }
                if (compare_cpu) {
                    Stopwatch compare_watch;
                    const GpuLevel3LaneInput& input = inputs[lane_index];
                    const std::vector<uint8_t> cpu_lane = rebuild_payload_from_template_exceptions(
                        input.lane,
                        input.template_record.data(),
                        input.exception_bitmap.data(),
                        input.exception_bitmap.size(),
                        input.exception_values.data(),
                        input.exception_values.size(),
                        predictor);
                    if (std::memcmp(lane_output.data(), cpu_lane.data(), kPayloadSize) != 0) {
                        ++result.mismatched_lanes;
                        for (size_t i = 0; i < kPayloadSize; ++i) {
                            if (lane_output[i] != cpu_lane[i]) {
                                ++result.mismatched_bytes;
                            }
                        }
                    }
                    result.compare_ms += compare_watch.elapsed_ms();
                }
            }
        }

        result.status = result.mismatched_lanes == 0 ? "ok" : "mismatch";
        if (result.status == "mismatch") {
            result.fallback_reason = "GPU output mismatched CPU reference";
        }
        result.used = true;

        free_device_buffers();
    } catch (const std::exception& error) {
        free_device_buffers();
        result.status = std::string("failed: ") + error.what();
        result.fallback_reason = result.status;
    }
    result.total_ms = total_watch.elapsed_ms();
    return result;
}
#else
GpuOffloadBenchResult run_gpu_level3_rebuild(
    const std::vector<GpuLevel3LaneInput>& inputs,
    const PredictorTable&,
    bool compare_cpu,
    std::vector<Spc3DecodedLane>*,
    const Spc3DecodedLaneSink*)
{
    GpuOffloadBenchResult result;
    result.lane_count = inputs.size() > std::numeric_limits<uint32_t>::max()
        ? std::numeric_limits<uint32_t>::max()
        : static_cast<uint32_t>(inputs.size());
    result.compare_mode = compare_cpu ? "cpu_rebuild_per_lane" : "none";
    result.status = "failed: CUDA driver/NVRTC probe is Windows-only in this build";
    result.fallback_reason = "CUDA driver/NVRTC probe is Windows-only in this build";
    return result;
}
#endif

GpuLevel3LaneInput make_gpu_level3_input_from_typed_entry(
    const std::vector<uint8_t>& bytes,
    const Spc3TableEntry& entry)
{
    if (!entry_is_typed_level3(entry)) {
        throw std::runtime_error("GPU rebuild supports only v0.2 typed level 3 streams");
    }
    const size_t stream_offset = checked_offset(bytes, entry.stream_offset, entry.stream_size, "SPC3 lane stream");
    const uint8_t* stream = bytes.data() + stream_offset;
    const size_t stream_size = static_cast<size_t>(entry.stream_size);
    const auto decode_substream = [&](uint32_t kind, const std::string& label) {
        const Spc3TypedSubstreamEntry& sub = typed_substream_by_kind(entry, kind);
        const uint64_t sub_end = checked_add_u64(sub.offset, sub.stream_size, label + " end");
        if (sub_end > stream_size) {
            throw std::runtime_error(label + " outside typed stream");
        }
        const CodecSpec codec = codec_from_entry_flags(sub.flags, 3, true);
        return codec_decompress_exact(
            stream + static_cast<size_t>(sub.offset),
            static_cast<size_t>(sub.stream_size),
            sub.raw_size,
            codec,
            label);
    };

    GpuLevel3LaneInput input;
    input.lane = static_cast<uint16_t>(entry.lane);
    const std::vector<uint8_t> template_raw =
        decode_substream(kSpc3TypedSubstreamTemplate, "typed level 3 template stream");
    input.exception_bitmap =
        decode_substream(kSpc3TypedSubstreamBitmap, "typed level 3 bitmap stream");
    input.exception_values =
        decode_substream(kSpc3TypedSubstreamValues, "typed level 3 XOR value stream");
    if (template_raw.size() != input.template_record.size()) {
        throw std::runtime_error("typed level 3 template raw size is invalid");
    }
    std::copy(template_raw.begin(), template_raw.end(), input.template_record.begin());
    return input;
}

[[maybe_unused]] std::vector<GpuLevel3LaneInput> build_gpu_inputs_from_typed_spc3(
    const std::vector<uint8_t>& bytes,
    const Spc3Container& container)
{
    std::vector<GpuLevel3LaneInput> inputs;
    inputs.reserve(container.entries.size());
    for (const Spc3TableEntry& entry : container.entries) {
        inputs.push_back(make_gpu_level3_input_from_typed_entry(bytes, entry));
    }
    return inputs;
}

std::vector<Spc3DecodedLane> decode_spc3_lanes_with_optional_gpu(
    const std::vector<uint8_t>& bytes,
    const Spc3Container& container,
    const Options& options,
    GpuOffloadBenchResult* gpu_result,
    CpuDecodeProfile* cpu_profile,
    const Spc3DecodedLaneSink* lane_sink)
{
    GpuOffloadBenchResult local_gpu;
    local_gpu.lane_count = static_cast<uint32_t>(container.entries.size());
    local_gpu.output_bytes = static_cast<uint64_t>(container.entries.size()) * kPayloadSize;
    if (cpu_profile != nullptr) {
        *cpu_profile = CpuDecodeProfile{};
    }
    if (!options.gpu_rebuild) {
        local_gpu.status = "not_requested";
        if (gpu_result != nullptr) {
            *gpu_result = local_gpu;
        }
        if (lane_sink != nullptr) {
            decode_spc3_lanes_streaming(bytes, container, cpu_profile, nullptr, lane_sink);
            if (cpu_profile != nullptr) {
                cpu_profile->backend = "cpu";
            }
            return std::vector<Spc3DecodedLane>{};
        }
        const auto decoded = decode_spc3_lanes(bytes, container, cpu_profile);
        if (cpu_profile != nullptr) {
            cpu_profile->backend = "cpu";
        }
        return decoded;
    }

    auto fallback_cpu = [&](const std::string& reason) {
        local_gpu.status = "fallback_cpu";
        local_gpu.fallback_reason = reason;
        if (gpu_result != nullptr) {
            *gpu_result = local_gpu;
        }
        if (lane_sink != nullptr) {
            decode_spc3_lanes_streaming(bytes, container, cpu_profile, nullptr, lane_sink);
            if (cpu_profile != nullptr) {
                cpu_profile->backend = "cpu";
            }
            return std::vector<Spc3DecodedLane>{};
        }
        const auto decoded = decode_spc3_lanes(bytes, container, cpu_profile);
        if (cpu_profile != nullptr) {
            cpu_profile->backend = "cpu";
        }
        return decoded;
    };

    if (container.level != 3 || container.version < kSpc3VersionV2) {
        return fallback_cpu("SPC3 file is not v0.2 level 3");
    }
    if (!container.predictor.loaded) {
        return fallback_cpu("predictor is not loaded");
    }
    for (const Spc3TableEntry& entry : container.entries) {
        if (!entry_is_typed_level3(entry)) {
            return fallback_cpu("not every lane is typed level 3");
        }
    }

    bool emitted_any = false;
    try {
        std::vector<Spc3DecodedLane> decoded;
        decoded.reserve(container.entries.size());

        local_gpu = GpuOffloadBenchResult{};
        local_gpu.lane_count = 0;
        local_gpu.value_count = 0;
        local_gpu.output_bytes = 0;
        local_gpu.compare_mode = "none";
        local_gpu.status = "ok";
        const size_t total_lanes = container.entries.size();

        LaneSinkOffsetState chunk_sink_state;
        chunk_sink_state.sink = lane_sink;
        const Spc3DecodedLaneSink chunk_sink{&chunk_sink_state, emit_decoded_lane_with_offset};

        for (size_t lane_index = 0; lane_index < total_lanes; ) {
            const size_t chunk_start = lane_index;
            uint64_t chunk_bytes = 0;
            std::vector<GpuLevel3LaneInput> chunk_inputs;
            const size_t remaining_lanes = total_lanes - lane_index;
            chunk_inputs.reserve(std::min<size_t>(remaining_lanes, 32));

            while (lane_index < total_lanes) {
                const Spc3TableEntry& entry = container.entries[lane_index];
                const GpuLevel3LaneInput input = make_gpu_level3_input_from_typed_entry(bytes, entry);
                const uint64_t predicted_bytes = estimate_gpu_level3_lane_bytes(input);
                if (!chunk_inputs.empty() && checked_add_u64(chunk_bytes, predicted_bytes, "GPU chunk byte budget") > kGpuRebuildChunkBytesBudget) {
                    break;
                }
                chunk_inputs.push_back(std::move(input));
                chunk_bytes = checked_add_u64(chunk_bytes, predicted_bytes, "GPU chunk byte budget");
                ++lane_index;
            }
            if (chunk_inputs.empty()) {
                throw std::runtime_error("GPU chunk budget cannot fit a typed lane");
            }

            std::vector<Spc3DecodedLane> chunk_decoded;
            chunk_sink_state.lane_index_offset = chunk_start;
            const GpuOffloadBenchResult chunk_gpu = run_gpu_level3_rebuild(
                chunk_inputs,
                container.predictor,
                false,
                lane_sink == nullptr ? &chunk_decoded : nullptr,
                lane_sink == nullptr ? nullptr : &chunk_sink);

            if (chunk_gpu.status != "ok") {
                const std::string reason = !chunk_gpu.fallback_reason.empty()
                    ? chunk_gpu.fallback_reason
                    : chunk_gpu.status;
                if (lane_sink != nullptr && emitted_any) {
                    throw std::runtime_error("GPU chunked decode failed after partial emit: " + reason);
                }
                return fallback_cpu(reason);
            }

            local_gpu.used = true;
            local_gpu.runtime_initializations += chunk_gpu.runtime_initializations;
            local_gpu.runtime_cache_hit = local_gpu.runtime_cache_hit || chunk_gpu.runtime_cache_hit;
            local_gpu.runtime_failure_cached = local_gpu.runtime_failure_cached || chunk_gpu.runtime_failure_cached;
            local_gpu.lane_count = static_cast<uint32_t>(checked_add_u64(local_gpu.lane_count, chunk_gpu.lane_count, "GPU total lane count"));
            local_gpu.value_count = checked_add_u64(local_gpu.value_count, chunk_gpu.value_count, "GPU total value count");
            local_gpu.output_bytes = checked_add_u64(local_gpu.output_bytes, chunk_gpu.output_bytes, "GPU total output bytes");
            local_gpu.mismatched_lanes += chunk_gpu.mismatched_lanes;
            local_gpu.mismatched_bytes += chunk_gpu.mismatched_bytes;
            local_gpu.compile_ms += chunk_gpu.compile_ms;
            local_gpu.upload_ms += chunk_gpu.upload_ms;
            local_gpu.kernel_ms += chunk_gpu.kernel_ms;
            local_gpu.download_ms += chunk_gpu.download_ms;
            local_gpu.host_crc_ms += chunk_gpu.host_crc_ms;
            local_gpu.compare_ms += chunk_gpu.compare_ms;
            local_gpu.total_ms += chunk_gpu.total_ms;
            emitted_any = true;
            local_gpu.fallback_reason.clear();
            local_gpu.download_mode = local_gpu.download_mode == "none"
                ? chunk_gpu.download_mode
                : (local_gpu.download_mode == chunk_gpu.download_mode ? local_gpu.download_mode : "mixed");
            if (local_gpu.device_name.empty() && !chunk_gpu.device_name.empty()) {
                local_gpu.device_name = chunk_gpu.device_name;
            }
            if (lane_sink == nullptr) {
                if (chunk_decoded.size() != chunk_inputs.size()) {
                    const std::string reason = "GPU chunk decode returned wrong lane count";
                    return fallback_cpu(reason);
                }
                decoded.insert(
                    decoded.end(),
                    std::make_move_iterator(chunk_decoded.begin()),
                    std::make_move_iterator(chunk_decoded.end()));
                emitted_any = true;
            }
        }

        if (local_gpu.lane_count != container.entries.size()) {
            const std::string reason = "GPU decode returned wrong lane count";
            return fallback_cpu(reason);
        }

        if (gpu_result != nullptr) {
            *gpu_result = local_gpu;
        }
        if (lane_sink != nullptr) {
            return std::vector<Spc3DecodedLane>{};
        }
        return decoded;
    } catch (const std::exception& error) {
        if (lane_sink != nullptr && emitted_any) {
            throw;
        }
        return fallback_cpu(error.what());
    }
}

LaneStreamBenchResult bench_lane_stream_roundtrip(
    const LaneModel& model,
    uint32_t level,
    const CodecSpec& codec,
    const PredictorTable& predictor)
{
    LaneStreamBenchResult result;
    std::vector<uint8_t> raw;
    std::vector<uint8_t> stream;
    const uint8_t* stream_data = nullptr;
    size_t stream_size = 0;

    Stopwatch build_watch;
    if (level == 0) {
        if (codec.id != CodecId::None) {
            throw std::runtime_error("streaming level 0 requires none codec");
        }
        stream_data = model.encrypted.data();
        stream_size = model.encrypted.size();
        result.uncompressed_model_size = model.encrypted.size();
    } else if (level == 1) {
        raw = decrypted_stream_from_model(model);
        result.uncompressed_model_size = raw.size();
        stream = codec_compress_data(raw, codec);
        stream_data = stream.data();
        stream_size = stream.size();
    } else if (level == 2) {
        raw = make_template_iv32_model(model);
        result.uncompressed_model_size = raw.size();
        stream = codec_compress_data(raw, codec);
        stream_data = stream.data();
        stream_size = stream.size();
    } else if (level == 3) {
        raw = make_template_exception_model(model);
        result.uncompressed_model_size = raw.size();
        stream = codec_compress_data(raw, codec);
        stream_data = stream.data();
        stream_size = stream.size();
    } else {
        throw std::runtime_error("streaming bench level must be 0..3");
    }
    result.build_ms = build_watch.elapsed_ms();
    result.stream_size = stream_size;

    Spc3TableEntry entry;
    entry.lane = model.metrics.lane;
    entry.level = level;
    entry.stream_kind = level;
    entry.flags = pack_entry_codec_flags(codec);
    entry.original_payload_crc32 = model.encrypted_crc32;
    entry.rebuilt_payload_crc32 = model.encrypted_crc32;
    entry.uncompressed_model_size = result.uncompressed_model_size;
    entry.stream_size = result.stream_size;
    entry.predictor_matches = model.metrics.predictor_matches;
    entry.predictor_exceptions = model.metrics.predictor_exceptions;

    Stopwatch verify_watch;
    Stopwatch unpack_watch;
    const std::vector<uint8_t> rebuilt = rebuild_payload_from_stream_data(
        stream_data,
        stream_size,
        entry,
        predictor);
    result.unpack_ms = unpack_watch.elapsed_ms();
    const uint32_t rebuilt_crc32 = crc32_vector(rebuilt);
    if (rebuilt_crc32 != model.encrypted_crc32 ||
        rebuilt.size() != model.encrypted.size() ||
        std::memcmp(rebuilt.data(), model.encrypted.data(), kPayloadSize) != 0) {
        ++result.decode_crc_mismatches;
    }
    result.verify_ms = verify_watch.elapsed_ms();
    return result;
}

TypedLevel3BenchResult bench_typed_level3_roundtrip(
    const LaneModel& model,
    const TypedLevel3Policy& policy,
    const PredictorTable& predictor)
{
    if (!predictor.loaded) {
        throw std::runtime_error("typed level 3 bench requires predictor");
    }

    TypedLevel3BenchResult result;
    result.status = "ok";
    result.policy = policy;

    const std::vector<uint8_t> template_raw(model.base_template.begin(), model.base_template.end());
    const std::vector<uint8_t>& bitmap_raw = model.exception_bitmap;
    const std::vector<uint8_t>& values_raw = model.exception_values;
    result.uncompressed_model_size = template_raw.size() + bitmap_raw.size() + values_raw.size();

    std::vector<uint8_t> template_stream;
    std::vector<uint8_t> bitmap_stream;
    std::vector<uint8_t> values_stream;
    {
        Stopwatch build_watch;
        template_stream = codec_compress_data(template_raw, policy.template_codec);
        bitmap_stream = codec_compress_data(bitmap_raw, policy.bitmap_codec);
        values_stream = codec_compress_data(values_raw, policy.values_codec);
        result.build_ms = build_watch.elapsed_ms();
    }

    result.template_stream_bytes = template_stream.size();
    result.bitmap_stream_bytes = bitmap_stream.size();
    result.values_stream_bytes = values_stream.size();
    result.substream_bytes =
        result.template_stream_bytes + result.bitmap_stream_bytes + result.values_stream_bytes;

    std::vector<uint8_t> decoded_template;
    std::vector<uint8_t> decoded_bitmap;
    std::vector<uint8_t> decoded_values;
    std::vector<uint8_t> rebuilt;
    {
        Stopwatch unpack_watch;
        decoded_template = codec_decompress_exact(
            template_stream.data(),
            template_stream.size(),
            template_raw.size(),
            policy.template_codec,
            "typed level 3 template stream");
        decoded_bitmap = codec_decompress_exact(
            bitmap_stream.data(),
            bitmap_stream.size(),
            bitmap_raw.size(),
            policy.bitmap_codec,
            "typed level 3 bitmap stream");
        decoded_values = codec_decompress_exact(
            values_stream.data(),
            values_stream.size(),
            values_raw.size(),
            policy.values_codec,
            "typed level 3 XOR value stream");
        rebuilt = rebuild_payload_from_template_exceptions(
            model.metrics.lane,
            decoded_template.data(),
            decoded_bitmap.data(),
            decoded_bitmap.size(),
            decoded_values.data(),
            decoded_values.size(),
            predictor);
        result.unpack_ms = unpack_watch.elapsed_ms();
    }

    {
        Stopwatch verify_watch;
        const uint32_t rebuilt_crc32 = crc32_vector(rebuilt);
        if (rebuilt_crc32 != model.encrypted_crc32 ||
            rebuilt.size() != model.encrypted.size() ||
            std::memcmp(rebuilt.data(), model.encrypted.data(), kPayloadSize) != 0) {
            ++result.decode_crc_mismatches;
        }
        result.verify_ms = verify_watch.elapsed_ms();
    }
    return result;
}

constexpr std::array<std::string_view, 4> kExternalModelIds = {
    "encrypted_raw",
    "decrypted_solid",
    "template_iv32",
    "template_exceptions",
};

constexpr std::array<std::string_view, 4> kExternalModelDescriptions = {
    "concatenated encrypted .pk3raw payload",
    "concatenated full decrypted PK3 payload",
    "concatenated template plus raw IV32 model",
    "concatenated template plus predictor exception bitmap/XOR model",
};

std::string quote_cmd_path(const fs::path& path) {
    std::string text = path.string();
    std::string quoted = "\"";
    for (const char ch : text) {
        if (ch == '"') {
            quoted += "\\\"";
        } else {
            quoted += ch;
        }
    }
    quoted += "\"";
    return quoted;
}

bool command_exists(const std::string& command) {
    const std::string probe = "where " + command + " >nul 2>nul";
    return std::system(probe.c_str()) == 0;
}

fs::path make_external_temp_root(std::string_view tool, std::string_view model_id) {
    const auto stamp = std::chrono::steady_clock::now().time_since_epoch().count();
    const fs::path base = fs::temp_directory_path();
    for (uint32_t attempt = 0; attempt < 64; ++attempt) {
        fs::path candidate = base /
            ("spc3_" + std::string(tool) + "_bench_" + std::string(model_id) + "_" +
             std::to_string(stamp) + "_" + std::to_string(attempt));
        std::error_code ec;
        if (fs::create_directory(candidate, ec)) {
            return candidate;
        }
        if (ec) {
            throw std::runtime_error("could not create temp directory: " + candidate.string());
        }
    }
    throw std::runtime_error("could not allocate unique external bench temp directory");
}

struct ScopedTempDirectory {
    fs::path path;

    explicit ScopedTempDirectory(fs::path temp_path) : path(std::move(temp_path)) {}

    ~ScopedTempDirectory() {
        if (!path.empty()) {
            std::error_code ignored;
            fs::remove_all(path, ignored);
        }
    }

    ScopedTempDirectory(const ScopedTempDirectory&) = delete;
    ScopedTempDirectory& operator=(const ScopedTempDirectory&) = delete;
};

std::vector<uint8_t> concatenate_model_payloads(
    const std::vector<LaneModel>& models,
    uint32_t count,
    uint32_t model_level)
{
    std::vector<uint8_t> payload;
    if (model_level == 0 || model_level == 1) {
        payload.reserve(static_cast<size_t>(count) * kPayloadSize);
    } else if (model_level == 2) {
        payload.reserve(static_cast<size_t>(count) * (kRecordSize + kExpectedRecords * 4ULL));
    } else {
        size_t reserve_size = 0;
        for (uint32_t i = 0; i < count; ++i) {
            reserve_size += kRecordSize + models[i].exception_bitmap.size() + models[i].exception_values.size();
        }
        payload.reserve(reserve_size);
    }

    for (uint32_t i = 0; i < count; ++i) {
        if (model_level == 0) {
            payload.insert(payload.end(), models[i].encrypted.begin(), models[i].encrypted.end());
        } else if (model_level == 1) {
            const std::vector<uint8_t> decrypted = decrypted_stream_from_model(models[i]);
            payload.insert(payload.end(), decrypted.begin(), decrypted.end());
        } else if (model_level == 2) {
            const std::vector<uint8_t> raw = make_template_iv32_model(models[i]);
            payload.insert(payload.end(), raw.begin(), raw.end());
        } else if (model_level == 3) {
            const std::vector<uint8_t> raw = make_template_exception_model(models[i]);
            payload.insert(payload.end(), raw.begin(), raw.end());
        } else {
            throw std::runtime_error("unknown external bench model level");
        }
    }
    return payload;
}

ExternalBenchResult run_7z_lzma2_payload(const std::vector<uint8_t>& payload, std::string_view model_id) {
    ExternalBenchResult result;
    if (!command_exists("7z")) {
        result.status = "tool_missing";
        return result;
    }

    const ScopedTempDirectory temp_root(make_external_temp_root("7z", model_id));
    const fs::path raw_path = temp_root.path / "payload.bin";
    const fs::path out_path = temp_root.path / "lanes.7z";

    try {
        write_binary_file(raw_path, payload);
        Stopwatch watch;
        const std::string cmd =
            "7z a -t7z -m0=lzma2 -mx=9 -md=64m -ms=on -mmt=on -bso0 -bsp0 " +
            quote_cmd_path(out_path) + " " + quote_cmd_path(raw_path) + " >nul";
        const int rc = std::system(cmd.c_str());
        result.ms = watch.elapsed_ms();
        if (rc != 0 || !fs::is_regular_file(out_path)) {
            result.status = "failed";
        } else {
            result.status = "ok";
            result.size_bytes = fs::file_size(out_path);
        }
    } catch (const std::exception& error) {
        result.status = std::string("failed: ") + error.what();
    }

    return result;
}

ExternalBenchResult run_zstd_payload(const std::vector<uint8_t>& payload, std::string_view model_id) {
    ExternalBenchResult result;
    if (!command_exists("zstd")) {
        result.status = "tool_missing";
        return result;
    }

    const ScopedTempDirectory temp_root(make_external_temp_root("zstd", model_id));
    const fs::path raw_path = temp_root.path / "payload.bin";
    const fs::path out_path = temp_root.path / "payload.bin.zst";

    try {
        write_binary_file(raw_path, payload);
        Stopwatch watch;
        const std::string cmd =
            "zstd -19 -T0 -q -f " + quote_cmd_path(raw_path) + " -o " + quote_cmd_path(out_path);
        const int rc = std::system(cmd.c_str());
        result.ms = watch.elapsed_ms();
        if (rc != 0 || !fs::is_regular_file(out_path)) {
            result.status = "failed";
        } else {
            result.status = "ok";
            result.size_bytes = fs::file_size(out_path);
        }
    } catch (const std::exception& error) {
        result.status = std::string("failed: ") + error.what();
    }

    return result;
}

void write_external_result_json(std::ostream& out, const ExternalBenchResult& result) {
    out << "{\"status\": \"" << json_escape(result.status)
        << "\", \"size_bytes\": " << result.size_bytes
        << ", \"ms\": " << result.ms << "}";
}

void write_codec_filter_json(std::ostream& out, const std::vector<CodecSpec>& codecs) {
    out << "[";
    for (size_t i = 0; i < codecs.size(); ++i) {
        out << "\"" << codec_display_name(codecs[i]) << "\"" << (i + 1 == codecs.size() ? "" : ", ");
    }
    out << "]";
}

void write_level_filter_json(std::ostream& out, const std::vector<uint32_t>& levels) {
    out << "[";
    for (size_t i = 0; i < levels.size(); ++i) {
        out << levels[i] << (i + 1 == levels.size() ? "" : ", ");
    }
    out << "]";
}

void write_typed_policy_filter_json(std::ostream& out, const std::vector<TypedLevel3Policy>& policies) {
    out << "[";
    for (size_t i = 0; i < policies.size(); ++i) {
        out << "\"" << json_escape(policies[i].id) << "\"" << (i + 1 == policies.size() ? "" : ", ");
    }
    out << "]";
}

std::string build_bench_report_json(
    const Options& options,
    const std::vector<LaneModel>& models,
    const std::vector<uint32_t>& sample_limits,
    const std::vector<std::array<uint64_t, 4>>& spc3_sizes,
    const std::vector<std::array<double, 4>>& spc3_ms,
    const std::vector<std::array<double, 4>>& spc3_unpack_ms,
    const std::vector<std::array<double, 4>>& spc3_verify_ms,
    const std::vector<std::array<double, 4>>& spc3_decode_mib_s,
    const std::vector<std::array<uint64_t, 4>>& spc3_decode_crc_mismatches,
    const std::vector<std::array<ExternalBenchResult, 4>>& lzma2_results,
    const std::vector<std::array<ExternalBenchResult, 4>>& zstd_results,
    const std::vector<std::vector<NativeCodecBenchResult>>& native_codec_results)
{
    std::ostringstream out;
    out << std::fixed << std::setprecision(3);
    out << "{\n";
    out << "  \"schema\": \"spc3_compression_oracle.v1\",\n";
    out << "  \"mode\": \"bench\",\n";
    out << "  \"root\": \"" << json_escape(options.root.string()) << "\",\n";
    out << "  \"external_codecs\": {\n";
    out << "    \"enabled\": " << (options.bench_external ? "true" : "false") << ",\n";
    out << "    \"solid_7z_lzma2\": \"7z -t7z -m0=lzma2 -mx=9 -md=64m -ms=on -mmt=on\",\n";
    out << "    \"zstd\": \"zstd -19 -T0\",\n";
    out << "    \"model_inputs\": [\n";
    for (size_t i = 0; i < kExternalModelIds.size(); ++i) {
        out << "      {\"id\": \"" << json_escape(kExternalModelIds[i])
            << "\", \"description\": \"" << json_escape(kExternalModelDescriptions[i]) << "\"}"
            << (i + 1 == kExternalModelIds.size() ? "\n" : ",\n");
    }
    out << "    ],\n";
    out << "    \"nvcomp_gpu\": \"planned_not_run\"\n";
    out << "  },\n";
    out << "  \"native_codecs\": {\n";
    out << "    \"enabled\": " << (options.bench_native_codecs ? "true" : "false") << ",\n";
    out << "    \"default_container_codec\": \"zlib-9\",\n";
    out << "    \"zstd_levels_tested\": [3, 9, 19],\n";
    out << "    \"codec_filter\": ";
    write_codec_filter_json(out, selected_native_codecs(options));
    out << ",\n";
    out << "    \"level_filter\": ";
    write_level_filter_json(out, options.bench_levels);
    out << ",\n";
    out << "    \"lzma2_native\": \"prototype_xz_lzma2_preset_9_not_default\",\n";
    out << "    \"rans_fse\": \"reserved_for_level3_typed_exception_streams\"\n";
    out << "  },\n";
    out << "  \"codec_recommendation\": \"Keep SPC3 structure CPU-stable first. Use zstd only if decode speed beats zlib enough to justify a dependency, use LZMA2 only where smaller level 1/2 streams matter more than speed, and reserve rANS/FSE for level 3 exception streams after more corpus data.\",\n";
    out << "  \"samples\": [\n";
    for (size_t s = 0; s < sample_limits.size(); ++s) {
        const uint32_t count = sample_limits[s];
        uint64_t zip_bytes = 0;
        uint64_t raw_payload = 0;
        for (uint32_t i = 0; i < count; ++i) {
            zip_bytes += models[i].metrics.zip_size_bytes;
            raw_payload += models[i].encrypted.size();
        }
        out << "    {\n";
        out << "      \"lane_count\": " << count << ",\n";
        out << "      \"current_zip_bytes\": " << zip_bytes << ",\n";
        out << "      \"raw_payload_bytes\": " << raw_payload << ",\n";
        out << "      \"solid_7z_lzma2\": ";
        write_external_result_json(out, lzma2_results[s][0]);
        out << ",\n";
        out << "      \"zstd\": ";
        write_external_result_json(out, zstd_results[s][0]);
        out << ",\n";
        out << "      \"external_models\": {\n";
        for (size_t model_index = 0; model_index < kExternalModelIds.size(); ++model_index) {
            out << "        \"" << json_escape(kExternalModelIds[model_index]) << "\": {\"solid_7z_lzma2\": ";
            write_external_result_json(out, lzma2_results[s][model_index]);
            out << ", \"zstd\": ";
            write_external_result_json(out, zstd_results[s][model_index]);
            out << "}" << (model_index + 1 == kExternalModelIds.size() ? "\n" : ",\n");
        }
        out << "      },\n";
        out << "      \"spc3_levels\": [\n";
        for (uint32_t level = 0; level < 4; ++level) {
            out << "        {\"level\": " << level << ", \"size_bytes\": " << spc3_sizes[s][level]
                << ", \"build_ms\": " << spc3_ms[s][level]
                << ", \"unpack_ms\": " << spc3_unpack_ms[s][level]
                << ", \"verify_ms\": " << spc3_verify_ms[s][level]
                << ", \"decode_mib_s\": " << spc3_decode_mib_s[s][level]
                << ", \"decode_crc_mismatches\": " << spc3_decode_crc_mismatches[s][level]
                << "}"
                << (level == 3 ? "\n" : ",\n");
        }
        out << "      ],\n";
        out << "      \"native_codec_matrix\": [\n";
        const auto& native_rows = native_codec_results[s];
        for (size_t i = 0; i < native_rows.size(); ++i) {
            const auto& row = native_rows[i];
            out << "        {\"status\": \"" << json_escape(row.status)
                << "\", \"codec\": \"" << codec_name(row.codec.id)
                << "\", \"codec_level\": " << row.codec.level
                << ", \"spc3_level\": " << row.spc3_level
                << ", \"size_bytes\": " << row.size_bytes
                << ", \"build_ms\": " << row.build_ms
                << ", \"unpack_ms\": " << row.unpack_ms
                << ", \"verify_ms\": " << row.verify_ms
                << ", \"decode_mib_s\": " << row.decode_mib_s
                << ", \"decode_crc_mismatches\": " << row.decode_crc_mismatches << "}"
                << (i + 1 == native_rows.size() ? "\n" : ",\n");
        }
        out << "      ]\n";
        out << "    }" << (s + 1 == sample_limits.size() ? "\n" : ",\n");
    }
    out << "  ]\n";
    out << "}\n";
    return out.str();
}

std::string build_streaming_bench_report_json(
    const Options& options,
    const std::vector<uint32_t>& sample_limits,
    const std::vector<StreamingBenchSampleResult>& samples)
{
    std::ostringstream out;
    out << std::fixed << std::setprecision(3);
    out << "{\n";
    out << "  \"schema\": \"spc3_streaming_compression_oracle.v1\",\n";
    out << "  \"mode\": \"bench\",\n";
    out << "  \"streaming\": true,\n";
    out << "  \"root\": \"" << json_escape(options.root.string()) << "\",\n";
    out << "  \"bench_limits\": [";
    for (size_t i = 0; i < sample_limits.size(); ++i) {
        out << sample_limits[i] << (i + 1 == sample_limits.size() ? "" : ", ");
    }
    out << "],\n";
    out << "  \"memory_model\": \"one LaneModel and one compressed stream at a time unless --bench-gpu is enabled; GPU probe batches requested sample sizes for device upload/output comparison\",\n";
    out << "  \"native_codecs\": {\n";
    out << "    \"enabled\": " << (options.bench_native_codecs ? "true" : "false") << ",\n";
    out << "    \"zstd_levels_tested\": [3, 9, 19],\n";
    out << "    \"codec_filter\": ";
    write_codec_filter_json(out, selected_native_codecs(options));
    out << ",\n";
    out << "    \"level_filter\": ";
    write_level_filter_json(out, options.bench_levels);
    out << ",\n";
    out << "    \"lzma2_native\": \"prototype_xz_lzma2_preset_9_not_default\"\n";
    out << "  },\n";
    out << "  \"typed_level3\": {\n";
    out << "    \"enabled\": " << (options.bench_typed_level3 ? "true" : "false") << ",\n";
    out << "    \"status\": \"v0_2_typed_stream_layout_available_for_pack_and_gpu_probe\",\n";
    out << "    \"layout\": \"template, exception bitmap, and XOR exception values are compressed as separate per-lane substreams\",\n";
    out << "    \"rans_fse_enabled\": " << (options.bench_rans_fse ? "true" : "false") << ",\n";
    out << "    \"estimated_substream_table_entry_size\": " << kSpc3TypedLevel3SubstreamEntrySize << ",\n";
    out << "    \"estimated_substream_count_per_lane\": " << kSpc3TypedLevel3SubstreamCount << ",\n";
    out << "    \"policy_filter\": ";
    write_typed_policy_filter_json(out, selected_typed_level3_policies(options));
    out << "\n";
    out << "  },\n";
    out << "  \"gpu_offload\": {\n";
    out << "    \"enabled\": " << (options.bench_gpu ? "true" : "false") << ",\n";
    out << "    \"backend\": \"cuda_driver_nvrtc\",\n";
    out << "    \"scope\": \"typed level-3 template/bitmap/XOR decode plus encrypted PK3 rebuild; CPU still parses ZIP and verifies bytes\"\n";
    out << "  },\n";
    out << "  \"external_codecs\": {\"enabled\": false, \"reason\": \"streaming bench avoids concatenated disk-backed oracle payloads\"},\n";
    out << "  \"samples\": [\n";
    for (size_t s = 0; s < samples.size(); ++s) {
        const auto& sample = samples[s];
        out << "    {\n";
        out << "      \"lane_count\": " << sample.lane_count << ",\n";
        out << "      \"current_zip_bytes\": " << sample.source_zip_bytes << ",\n";
        out << "      \"raw_payload_bytes\": " << sample.raw_payload_bytes << ",\n";
        const double exception_density =
            sample.raw_payload_bytes == 0 ? 0.0 :
            static_cast<double>(sample.predictor_exceptions) /
                static_cast<double>(static_cast<uint64_t>(sample.lane_count) * kExpectedRecords);
        const double avg_exceptions =
            sample.lane_count == 0 ? 0.0 :
            static_cast<double>(sample.predictor_exceptions) / static_cast<double>(sample.lane_count);
        out << "      \"exception_stats\": {\n";
        out << "        \"predictor_matches\": " << sample.predictor_matches << ",\n";
        out << "        \"predictor_exceptions\": " << sample.predictor_exceptions << ",\n";
        out << "        \"lanes_with_exceptions\": " << sample.lanes_with_exceptions << ",\n";
        out << "        \"min_exceptions_per_lane\": "
            << (sample.min_predictor_exceptions == std::numeric_limits<uint64_t>::max() ? 0 : sample.min_predictor_exceptions) << ",\n";
        out << "        \"max_exceptions_per_lane\": " << sample.max_predictor_exceptions << ",\n";
        out << "        \"avg_exceptions_per_lane\": " << avg_exceptions << ",\n";
        out << "        \"bitmap_density\": " << exception_density << ",\n";
        out << "        \"exception_bitmap_bytes\": " << sample.exception_bitmap_bytes << ",\n";
        out << "        \"exception_value_bytes\": " << sample.exception_value_bytes << ",\n";
        out << "        \"xor_value_count\": " << (sample.exception_value_bytes / 4) << ",\n";
        out << "        \"xor_zero_values\": " << sample.xor_zero_values << ",\n";
        out << "        \"rans_fse_table_init_risk\": \""
            << (avg_exceptions < 1024.0 ? "high" : (avg_exceptions < 4096.0 ? "medium" : "lower")) << "\",\n";
        out << "        \"xor_low_byte_histogram\": [";
        for (size_t i = 0; i < sample.xor_low_byte_histogram.size(); ++i) {
            out << sample.xor_low_byte_histogram[i]
                << (i + 1 == sample.xor_low_byte_histogram.size() ? "" : ", ");
        }
        out << "]\n";
        out << "      },\n";
        out << "      \"spc3_levels\": [\n";
        for (uint32_t level = 0; level < 4; ++level) {
            const auto& row = sample.spc3_levels[level];
            out << "        {\"level\": " << level
                << ", \"size_bytes\": " << row.size_bytes
                << ", \"stream_bytes\": " << row.stream_bytes
                << ", \"build_ms\": " << row.build_ms
                << ", \"unpack_ms\": " << row.unpack_ms
                << ", \"verify_ms\": " << row.verify_ms
                << ", \"decode_mib_s\": " << row.decode_mib_s
                << ", \"decode_crc_mismatches\": " << row.decode_crc_mismatches << "}"
                << (level == 3 ? "\n" : ",\n");
        }
        out << "      ],\n";
        out << "      \"native_codec_matrix\": [\n";
        for (size_t i = 0; i < sample.native_codec_matrix.size(); ++i) {
            const auto& row = sample.native_codec_matrix[i];
            out << "        {\"status\": \"" << json_escape(row.status)
                << "\", \"codec\": \"" << codec_name(row.codec.id)
                << "\", \"codec_level\": " << row.codec.level
                << ", \"spc3_level\": " << row.spc3_level
                << ", \"size_bytes\": " << row.size_bytes
                << ", \"build_ms\": " << row.build_ms
                << ", \"unpack_ms\": " << row.unpack_ms
                << ", \"verify_ms\": " << row.verify_ms
                << ", \"decode_mib_s\": " << row.decode_mib_s
                << ", \"decode_crc_mismatches\": " << row.decode_crc_mismatches << "}"
                << (i + 1 == sample.native_codec_matrix.size() ? "\n" : ",\n");
        }
        out << "      ],\n";
        out << "      \"typed_level3_matrix\": [\n";
        for (size_t i = 0; i < sample.typed_level3_matrix.size(); ++i) {
            const auto& row = sample.typed_level3_matrix[i];
            out << "        {\"status\": \"" << json_escape(row.status)
                << "\", \"policy\": \"" << json_escape(row.policy.id)
                << "\", \"template_codec\": \"" << codec_name(row.policy.template_codec.id)
                << "\", \"template_codec_level\": " << row.policy.template_codec.level
                << ", \"bitmap_codec\": \"" << codec_name(row.policy.bitmap_codec.id)
                << "\", \"bitmap_codec_level\": " << row.policy.bitmap_codec.level
                << ", \"values_codec\": \"" << codec_name(row.policy.values_codec.id)
                << "\", \"values_codec_level\": " << row.policy.values_codec.level
                << ", \"size_bytes\": " << row.size_bytes
                << ", \"substream_bytes\": " << row.substream_bytes
                << ", \"template_stream_bytes\": " << row.template_stream_bytes
                << ", \"bitmap_stream_bytes\": " << row.bitmap_stream_bytes
                << ", \"values_stream_bytes\": " << row.values_stream_bytes
                << ", \"uncompressed_model_size\": " << row.uncompressed_model_size
                << ", \"build_ms\": " << row.build_ms
                << ", \"unpack_ms\": " << row.unpack_ms
                << ", \"verify_ms\": " << row.verify_ms
                << ", \"decode_mib_s\": " << row.decode_mib_s
                << ", \"decode_crc_mismatches\": " << row.decode_crc_mismatches << "}"
                << (i + 1 == sample.typed_level3_matrix.size() ? "\n" : ",\n");
        }
        out << "      ],\n";
        const auto& gpu = sample.gpu_offload;
        out << "      \"gpu_offload\": ";
        write_gpu_result_json(out, gpu);
        out << "\n";
        out << "    }" << (s + 1 == samples.size() ? "\n" : ",\n");
    }
    out << "  ]\n";
    out << "}\n";
    return out.str();
}

void run_streaming_bench_mode(const Options& options) {
    const uint32_t max_limit = *std::max_element(options.bench_limits.begin(), options.bench_limits.end());
    PredictorTable predictor;
    if (!options.no_predictor) {
        predictor = load_predictor_table(options.predictor);
    }
    const std::vector<LanePath> lanes = find_lane_zips(options.root, max_limit);
    if (lanes.empty()) {
        throw std::runtime_error("no Phase 3 lane ZIPs found");
    }

    std::vector<uint32_t> sample_limits;
    for (const uint32_t requested : options.bench_limits) {
        if (requested <= lanes.size()) {
            sample_limits.push_back(requested);
        }
    }
    if (sample_limits.empty()) {
        sample_limits.push_back(static_cast<uint32_t>(lanes.size()));
    }

    std::vector<StreamingBenchSampleResult> samples(sample_limits.size());
    const std::vector<std::pair<uint32_t, CodecSpec>> native_jobs = selected_native_jobs(options);
    const std::vector<TypedLevel3Policy> typed_policies = selected_typed_level3_policies(options);
    std::vector<GpuLevel3LaneInput> gpu_inputs;
    if (options.bench_gpu) {
        gpu_inputs.reserve(lanes.size());
    }

    for (size_t s = 0; s < samples.size(); ++s) {
        samples[s].lane_count = sample_limits[s];
        samples[s].raw_payload_bytes = static_cast<uint64_t>(sample_limits[s]) * kPayloadSize;
        for (const auto& [level, codec] : native_jobs) {
            NativeCodecBenchResult row;
            row.status = "ok";
            row.codec = codec;
            row.spc3_level = level;
            samples[s].native_codec_matrix.push_back(row);
        }
        for (const TypedLevel3Policy& policy : typed_policies) {
            TypedLevel3BenchResult row;
            row.status = "ok";
            row.policy = policy;
            samples[s].typed_level3_matrix.push_back(row);
        }
    }

    uint64_t predictor_stream_size = 0;
    if (predictor.loaded) {
        predictor_stream_size = zlib_compress_data(serialize_predictor_raw(predictor), 9).size();
    }

    for (size_t lane_index = 0; lane_index < lanes.size(); ++lane_index) {
        const uint32_t lane_number = static_cast<uint32_t>(lane_index + 1);
        std::cout << "stream bench lane " << hex4(lanes[lane_index].lane)
                  << " (" << lane_number << "/" << lanes.size() << ") model\n";
        LaneModel model = build_lane_model(lanes[lane_index], predictor.loaded ? &predictor : nullptr, false);
        if (lane_has_failure(model.metrics)) {
            throw std::runtime_error("lane failed validation before streaming bench: " + hex4(model.metrics.lane));
        }

        std::vector<size_t> containing_samples;
        for (size_t s = 0; s < sample_limits.size(); ++s) {
            if (lane_number <= sample_limits[s]) {
                samples[s].source_zip_bytes += model.metrics.zip_size_bytes;
                samples[s].predictor_matches += model.metrics.predictor_matches;
                samples[s].predictor_exceptions += model.metrics.predictor_exceptions;
                samples[s].exception_bitmap_bytes += model.exception_bitmap.size();
                samples[s].exception_value_bytes += model.exception_values.size();
                if (model.metrics.predictor_exceptions != 0) {
                    ++samples[s].lanes_with_exceptions;
                }
                samples[s].min_predictor_exceptions = std::min<uint64_t>(
                    samples[s].min_predictor_exceptions,
                    model.metrics.predictor_exceptions);
                samples[s].max_predictor_exceptions = std::max<uint64_t>(
                    samples[s].max_predictor_exceptions,
                    model.metrics.predictor_exceptions);
                for (size_t offset = 0; offset + 4 <= model.exception_values.size(); offset += 4) {
                    const uint32_t value = load_le32(model.exception_values.data() + offset);
                    if (value == 0) {
                        ++samples[s].xor_zero_values;
                    }
                    ++samples[s].xor_low_byte_histogram[value & 0xFFU];
                }
                containing_samples.push_back(s);
            }
        }

        for (uint32_t level = 0; level < 4; ++level) {
            const CodecSpec codec = resolve_pack_codec(level, CodecSpec{}, false);
            const LaneStreamBenchResult lane_result = bench_lane_stream_roundtrip(model, level, codec, predictor);
            for (const size_t s : containing_samples) {
                auto& row = samples[s].spc3_levels[level];
                row.stream_bytes += lane_result.stream_size;
                row.build_ms += lane_result.build_ms;
                row.unpack_ms += lane_result.unpack_ms;
                row.verify_ms += lane_result.verify_ms;
                row.decode_crc_mismatches += lane_result.decode_crc_mismatches;
            }
        }

        for (size_t job_index = 0; job_index < native_jobs.size(); ++job_index) {
            const auto& [level, codec] = native_jobs[job_index];
            const LaneStreamBenchResult lane_result = bench_lane_stream_roundtrip(model, level, codec, predictor);
            for (const size_t s : containing_samples) {
                auto& row = samples[s].native_codec_matrix[job_index];
                row.size_bytes += lane_result.stream_size;
                row.build_ms += lane_result.build_ms;
                row.unpack_ms += lane_result.unpack_ms;
                row.verify_ms += lane_result.verify_ms;
                row.decode_crc_mismatches += lane_result.decode_crc_mismatches;
            }
        }

        for (size_t policy_index = 0; policy_index < typed_policies.size(); ++policy_index) {
            const TypedLevel3BenchResult lane_result =
                bench_typed_level3_roundtrip(model, typed_policies[policy_index], predictor);
            for (const size_t s : containing_samples) {
                auto& row = samples[s].typed_level3_matrix[policy_index];
                row.template_stream_bytes += lane_result.template_stream_bytes;
                row.bitmap_stream_bytes += lane_result.bitmap_stream_bytes;
                row.values_stream_bytes += lane_result.values_stream_bytes;
                row.substream_bytes += lane_result.substream_bytes;
                row.uncompressed_model_size += lane_result.uncompressed_model_size;
                row.build_ms += lane_result.build_ms;
                row.unpack_ms += lane_result.unpack_ms;
                row.verify_ms += lane_result.verify_ms;
                row.decode_crc_mismatches += lane_result.decode_crc_mismatches;
            }
        }
        if (options.bench_gpu) {
            gpu_inputs.push_back(make_gpu_level3_input_from_model(model));
        }
    }

    for (auto& sample : samples) {
        if (options.bench_gpu) {
            std::vector<GpuLevel3LaneInput> sample_inputs(
                gpu_inputs.begin(),
                gpu_inputs.begin() + sample.lane_count);
            sample.gpu_offload = run_gpu_level3_rebuild(sample_inputs, predictor, true, nullptr, nullptr);
            std::cout << "stream bench lanes=" << sample.lane_count
                      << " gpu_offload=" << sample.gpu_offload.status
                      << " kernel_ms=" << std::fixed << std::setprecision(1) << sample.gpu_offload.kernel_ms
                      << " mismatched_lanes=" << sample.gpu_offload.mismatched_lanes << "\n";
            if (sample.gpu_offload.status == "mismatch") {
                throw std::runtime_error("GPU offload rebuild mismatch");
            }
        }
        const double decoded_mib = (static_cast<double>(sample.lane_count) * kPayloadSize) / (1024.0 * 1024.0);
        for (uint32_t level = 0; level < 4; ++level) {
            auto& row = sample.spc3_levels[level];
            row.size_bytes = row.stream_bytes + spc3_streaming_container_overhead(
                sample.lane_count,
                level,
                predictor_stream_size);
            const double seconds = std::max(row.unpack_ms / 1000.0, 0.000001);
            row.decode_mib_s = decoded_mib / seconds;
            if (row.decode_crc_mismatches != 0) {
                throw std::runtime_error("SPC3 streaming bench decode CRC mismatch");
            }
            std::cout << "stream bench lanes=" << sample.lane_count
                      << " level=" << level
                      << " bytes=" << row.size_bytes
                      << " unpack_ms=" << std::fixed << std::setprecision(1) << row.unpack_ms
                      << " decode_mib_s=" << row.decode_mib_s << "\n";
        }
        for (auto& row : sample.native_codec_matrix) {
            row.size_bytes += spc3_streaming_container_overhead(
                sample.lane_count,
                row.spc3_level,
                predictor_stream_size);
            const double seconds = std::max(row.unpack_ms / 1000.0, 0.000001);
            row.decode_mib_s = decoded_mib / seconds;
            if (row.decode_crc_mismatches != 0) {
                throw std::runtime_error("SPC3 streaming native codec bench decode CRC mismatch");
            }
        }
        for (auto& row : sample.typed_level3_matrix) {
            row.size_bytes = row.substream_bytes + typed_level3_streaming_container_overhead(
                sample.lane_count,
                predictor_stream_size);
            const double seconds = std::max(row.unpack_ms / 1000.0, 0.000001);
            row.decode_mib_s = decoded_mib / seconds;
            if (row.decode_crc_mismatches != 0) {
                throw std::runtime_error("SPC3 streaming typed level 3 decode CRC mismatch");
            }
            std::cout << "stream bench lanes=" << sample.lane_count
                      << " typed_level3=" << row.policy.id
                      << " bytes=" << row.size_bytes
                      << " unpack_ms=" << std::fixed << std::setprecision(1) << row.unpack_ms
                      << " decode_mib_s=" << row.decode_mib_s << "\n";
        }
    }

    write_text_file(options.report, build_streaming_bench_report_json(options, sample_limits, samples));
    std::cout << "streaming_bench_report " << options.report.string() << "\n";
}

void run_bench_mode(const Options& options) {
    const uint32_t max_limit = *std::max_element(options.bench_limits.begin(), options.bench_limits.end());
    PredictorTable predictor;
    if (!options.no_predictor) {
        predictor = load_predictor_table(options.predictor);
    }
    const std::vector<LanePath> lanes = find_lane_zips(options.root, max_limit);
    if (lanes.empty()) {
        throw std::runtime_error("no Phase 3 lane ZIPs found");
    }

    std::vector<LaneModel> models;
    models.reserve(lanes.size());
    for (const auto& lane : lanes) {
        std::cout << "bench lane " << hex4(lane.lane) << " model\n";
        models.push_back(build_lane_model(lane, predictor.loaded ? &predictor : nullptr, false));
        if (lane_has_failure(models.back().metrics)) {
            throw std::runtime_error("lane failed validation before bench: " + hex4(lane.lane));
        }
    }

    std::vector<uint32_t> sample_limits;
    for (const uint32_t requested : options.bench_limits) {
        if (requested <= models.size()) {
            sample_limits.push_back(requested);
        }
    }
    if (sample_limits.empty()) {
        sample_limits.push_back(static_cast<uint32_t>(models.size()));
    }

    std::vector<std::array<uint64_t, 4>> spc3_sizes(sample_limits.size());
    std::vector<std::array<double, 4>> spc3_ms(sample_limits.size());
    std::vector<std::array<double, 4>> spc3_unpack_ms(sample_limits.size());
    std::vector<std::array<double, 4>> spc3_verify_ms(sample_limits.size());
    std::vector<std::array<double, 4>> spc3_decode_mib_s(sample_limits.size());
    std::vector<std::array<uint64_t, 4>> spc3_decode_crc_mismatches(sample_limits.size());
    std::vector<std::array<ExternalBenchResult, 4>> lzma2_results(sample_limits.size());
    std::vector<std::array<ExternalBenchResult, 4>> zstd_results(sample_limits.size());
    std::vector<std::vector<NativeCodecBenchResult>> native_codec_results(sample_limits.size());
    const std::vector<std::pair<uint32_t, CodecSpec>> native_jobs = selected_native_jobs(options);
    const auto count_decode_crc_mismatches = [&](const std::vector<uint8_t>& bytes, const Spc3Container& container) {
        struct DecodeCountState {
            const Spc3Container* container;
            uint64_t mismatches = 0;
        };
        auto emit_decoded_crc_lane = [](void* user,
                                        size_t lane_index,
                                        uint16_t,
                                        const uint8_t*,
                                        size_t,
                                        uint32_t payload_crc32) {
            auto* state = static_cast<DecodeCountState*>(user);
            if (lane_index >= state->container->entries.size()) {
                throw std::runtime_error("bench decode lane index out of range");
            }
            const Spc3TableEntry& entry = state->container->entries[lane_index];
            if (payload_crc32 != entry.original_payload_crc32 ||
                payload_crc32 != entry.rebuilt_payload_crc32) {
                ++state->mismatches;
            }
        };
        DecodeCountState state{&container, 0};
        Spc3DecodedLaneSink sink{&state, emit_decoded_crc_lane};
        decode_spc3_lanes_streaming(bytes, container, nullptr, nullptr, &sink);
        return state.mismatches;
    };

    for (size_t s = 0; s < sample_limits.size(); ++s) {
        for (uint32_t level = 0; level < 4; ++level) {
            const Spc3BuildResult built = build_spc3_file(
                models,
                sample_limits[s],
                level,
                predictor.loaded ? &predictor : nullptr,
                level == 3,
                CodecSpec{},
                false);
            spc3_sizes[s][level] = built.bytes.size();
            spc3_ms[s][level] = built.build_ms;
            Stopwatch verify_watch;
            Spc3Container container = parse_spc3_file(built.bytes);
            ensure_spc3_predictor_for_decode(container, options);
            Stopwatch unpack_watch;
            const uint64_t crc_mismatches = count_decode_crc_mismatches(built.bytes, container);
            spc3_unpack_ms[s][level] = unpack_watch.elapsed_ms();
            spc3_verify_ms[s][level] = verify_watch.elapsed_ms();
            spc3_decode_crc_mismatches[s][level] = crc_mismatches;
            const double seconds = std::max(spc3_unpack_ms[s][level] / 1000.0, 0.000001);
            const double decoded_mib = (static_cast<double>(sample_limits[s]) * kPayloadSize) / (1024.0 * 1024.0);
            spc3_decode_mib_s[s][level] = decoded_mib / seconds;
            if (crc_mismatches != 0) {
                throw std::runtime_error("SPC3 bench decode CRC mismatch");
            }
            std::cout << "bench lanes=" << sample_limits[s] << " level=" << level
                      << " bytes=" << built.bytes.size()
                      << " unpack_ms=" << std::fixed << std::setprecision(1) << spc3_unpack_ms[s][level]
                      << " decode_mib_s=" << spc3_decode_mib_s[s][level] << "\n";
        }
        if (options.bench_native_codecs) {
            for (const auto& [level, codec] : native_jobs) {
                NativeCodecBenchResult row;
                row.codec = codec;
                row.spc3_level = level;
                try {
                    const Spc3BuildResult built = build_spc3_file(
                        models,
                        sample_limits[s],
                        level,
                        predictor.loaded ? &predictor : nullptr,
                        level == 3,
                        codec,
                        true);
                    row.status = "ok";
                    row.size_bytes = built.bytes.size();
                    row.build_ms = built.build_ms;
                    Stopwatch verify_watch;
                    Spc3Container container = parse_spc3_file(built.bytes);
                    ensure_spc3_predictor_for_decode(container, options);
                    Stopwatch unpack_watch;
                    row.unpack_ms = unpack_watch.elapsed_ms();
                    row.decode_crc_mismatches = count_decode_crc_mismatches(built.bytes, container);
                    row.verify_ms = verify_watch.elapsed_ms();
                    const double seconds = std::max(row.unpack_ms / 1000.0, 0.000001);
                    const double decoded_mib =
                        (static_cast<double>(sample_limits[s]) * kPayloadSize) / (1024.0 * 1024.0);
                    row.decode_mib_s = decoded_mib / seconds;
                    if (row.decode_crc_mismatches != 0) {
                        throw std::runtime_error("native codec bench decode CRC mismatch");
                    }
                } catch (const std::exception& error) {
                    row.status = std::string("failed: ") + error.what();
                }
                std::cout << "bench lanes=" << sample_limits[s]
                          << " level=" << level
                          << " codec=" << codec_display_name(codec)
                          << " status=" << row.status
                          << " bytes=" << row.size_bytes
                          << " unpack_ms=" << std::fixed << std::setprecision(1) << row.unpack_ms
                          << "\n";
                native_codec_results[s].push_back(row);
                if (row.decode_crc_mismatches != 0) {
                    throw std::runtime_error("native codec bench decode CRC mismatch");
                }
            }
        }
        if (options.bench_external) {
            for (uint32_t model_level = 0; model_level < 4; ++model_level) {
                const std::vector<uint8_t> payload = concatenate_model_payloads(
                    models,
                    sample_limits[s],
                    model_level);
                lzma2_results[s][model_level] = run_7z_lzma2_payload(payload, kExternalModelIds[model_level]);
                zstd_results[s][model_level] = run_zstd_payload(payload, kExternalModelIds[model_level]);
                std::cout << "bench lanes=" << sample_limits[s]
                          << " model=" << kExternalModelIds[model_level]
                          << " 7z_lzma2=" << lzma2_results[s][model_level].status
                          << " zstd=" << zstd_results[s][model_level].status << "\n";
            }
        }
    }

    write_text_file(options.report, build_bench_report_json(
        options,
        models,
        sample_limits,
        spc3_sizes,
        spc3_ms,
        spc3_unpack_ms,
        spc3_verify_ms,
        spc3_decode_mib_s,
        spc3_decode_crc_mismatches,
        lzma2_results,
        zstd_results,
        native_codec_results));
    std::cout << "bench_report " << options.report.string() << "\n";
}

} // namespace

namespace {

int run_options(const Options& options) {
    if (options.show_help) {
        print_usage(options.exe_name.c_str());
        return 0;
    }

    if (options.self_test) {
        run_self_tests();
        std::cout << "self-test ok\n";
        return 0;
    }

    if (options.mode == Mode::Pack) {
        run_pack_mode(options);
        return 0;
    }
    if (options.mode == Mode::Unpack) {
        run_unpack_mode(options);
        return 0;
    }
    if (options.mode == Mode::Verify) {
        run_verify_mode(options);
        return 0;
    }
    if (options.mode == Mode::Inspect) {
        run_inspect_mode(options);
        return 0;
    }
    if (options.mode == Mode::Consolidate) {
        run_consolidate_mode(options);
        return 0;
    }
    if (options.mode == Mode::Bench) {
        if (options.bench_streaming) {
            run_streaming_bench_mode(options);
            return 0;
        }
        run_bench_mode(options);
        return 0;
    }

    PredictorTable predictor;
    double predictor_load_ms = 0.0;
    if (!options.no_predictor) {
        ScopedTimer timer(predictor_load_ms);
        predictor = load_predictor_table(options.predictor);
    }

    const std::vector<LanePath> lanes_to_run = find_lane_zips(options.root, options.limit_zips);
    if (lanes_to_run.empty()) {
        throw std::runtime_error("no Phase 3 lane ZIPs found");
    }

    std::vector<LaneMetrics> metrics;
    metrics.reserve(lanes_to_run.size());

    for (const auto& lane : lanes_to_run) {
        std::cout << "lane " << hex4(lane.lane) << " start\n";
        metrics.push_back(process_lane(
            lane,
            predictor.loaded ? &predictor : nullptr,
            !options.no_entropy_probe));
        const auto& last = metrics.back();
        std::cout << "lane " << hex4(lane.lane)
                  << " done total_ms=" << std::fixed << std::setprecision(1) << last.total_ms
                  << " exceptions=" << last.predictor_exceptions
                  << " rebuild_mismatches=" << last.rebuild_mismatches
                  << " errors=" << last.errors.size() << "\n";
    }

    const std::string report = build_report_json(
        options,
        metrics,
        lanes_to_run,
        predictor.loaded,
        predictor_load_ms);
    write_text_file(options.report, report);

    const auto hotspots = sorted_hotspots(metrics);
    std::cout << "report " << options.report.string() << "\n";
    std::cout << "hotspot_top " << hotspots.front().first << "="
              << std::fixed << std::setprecision(1) << hotspots.front().second << "ms\n";
    std::cout << "rebuild_mismatches_total="
              << sum_field_u64(metrics, &LaneMetrics::rebuild_mismatches) << "\n";
    std::cout << "predictor_roundtrip_mismatches_total="
              << sum_field_u64(metrics, &LaneMetrics::predictor_roundtrip_mismatches) << "\n";
    // Nonzero exit lets batch scripts and CI treat corpus problems as hard
    // failures while still preserving the JSON report for diagnosis.
    if (any_lane_has_failure(metrics)) {
        std::cerr << "audit_failures_present=1\n";
        return 2;
    }
    return 0;
}

int run_argv(int argc, char** argv) {
    return run_options(parse_args(argc, argv));
}

int hex_value(char ch) {
    if (ch >= '0' && ch <= '9') {
        return ch - '0';
    }
    if (ch >= 'a' && ch <= 'f') {
        return 10 + ch - 'a';
    }
    if (ch >= 'A' && ch <= 'F') {
        return 10 + ch - 'A';
    }
    return -1;
}

std::string hex_decode_arg(std::string_view text) {
    if ((text.size() & 1U) != 0) {
        throw std::runtime_error("server protocol hex token has odd length");
    }
    std::string out;
    out.reserve(text.size() / 2);
    for (size_t i = 0; i < text.size(); i += 2) {
        const int hi = hex_value(text[i]);
        const int lo = hex_value(text[i + 1]);
        if (hi < 0 || lo < 0) {
            throw std::runtime_error("server protocol hex token has non-hex byte");
        }
        out.push_back(static_cast<char>((hi << 4) | lo));
    }
    return out;
}

std::vector<std::string> parse_server_run_line(const std::string& line) {
    constexpr std::string_view prefix = "RUN";
    if (line.size() < prefix.size() || std::string_view(line).substr(0, prefix.size()) != prefix) {
        throw std::runtime_error("server protocol expected RUN line");
    }
    std::vector<std::string> args;
    args.push_back("spc3_prototype_server");
    size_t pos = prefix.size();
    while (pos < line.size()) {
        if (line[pos] != '\t') {
            throw std::runtime_error("server protocol expected tab separator");
        }
        ++pos;
        const size_t next = line.find('\t', pos);
        const size_t end = next == std::string::npos ? line.size() : next;
        args.push_back(hex_decode_arg(std::string_view(line).substr(pos, end - pos)));
        if (next == std::string::npos) {
            break;
        }
        pos = next;
    }
    if (args.size() == 1) {
        throw std::runtime_error("server protocol RUN line has no arguments");
    }
    return args;
}

int run_server_loop() {
    std::string line;
    while (std::getline(std::cin, line)) {
        if (!line.empty() && line.back() == '\r') {
            line.pop_back();
        }
        if (line == "STOP") {
            std::cout << "SPC3_SERVER_DONE exit_code=0\n" << std::flush;
            return 0;
        }
        int exit_code = 1;
        reset_standard_stream_formatting();
        try {
            std::vector<std::string> arg_storage = parse_server_run_line(line);
            std::vector<char*> argv;
            argv.reserve(arg_storage.size());
            for (std::string& arg : arg_storage) {
                argv.push_back(arg.data());
            }
            exit_code = run_argv(static_cast<int>(argv.size()), argv.data());
        } catch (const std::exception& error) {
            std::cerr << "error: " << error.what() << "\n";
            exit_code = 1;
        }
        reset_standard_stream_formatting();
        std::cout << "SPC3_SERVER_DONE exit_code=" << exit_code << "\n" << std::flush;
    }
    return 0;
}

} // namespace

int main(int argc, char** argv) {
    try {
        if (argc == 2 && std::string_view(argv[1]) == "--server") {
            return run_server_loop();
        }
        return run_argv(argc, argv);
    } catch (const std::exception& error) {
        std::cerr << "fatal: " << error.what() << "\n";
        return 1;
    }
}
