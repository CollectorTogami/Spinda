// Native read-only Spinda Workbench.
//
// This is the C++ port of tools/spinda/spinda_workbench/spinda_workbench.py.
// It keeps the hot status scans and pattern scoring out of Python/Flask while
// preserving the same read-only API shape for the browser dashboard.

#include <algorithm>
#include <array>
#include <chrono>
#include <cctype>
#include <cmath>
#include <cstdint>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <map>
#include <optional>
#include <queue>
#include <sstream>
#include <stdexcept>
#include <string>
#include <string_view>
#include <thread>
#include <utility>
#include <vector>

#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <winsock2.h>
#include <ws2tcpip.h>
#include <windows.h>
using SocketHandle = SOCKET;
static constexpr SocketHandle INVALID_SOCKET_HANDLE = INVALID_SOCKET;
#else
#include <arpa/inet.h>
#include <netdb.h>
#include <sys/socket.h>
#include <sys/types.h>
#include <unistd.h>
using SocketHandle = int;
static constexpr SocketHandle INVALID_SOCKET_HANDLE = -1;
#endif

namespace fs = std::filesystem;

namespace {

constexpr int DEFAULT_PORT = 8780;
constexpr int DEFAULT_SAMPLE_LIMIT = 16;
constexpr int DEFAULT_TARGET_PHASE3_LANES = 0xFFFE;
constexpr int SPINDAS_PER_LANE = 0x10000;
constexpr int EXPECTED_TSVS = 8192;
constexpr std::uintmax_t MIN_FINAL_ZIP_BYTES = 1024;
constexpr int DEFAULT_SUGGESTION_SCAN = 8192;
constexpr int DEFAULT_SUGGESTION_COUNT = 12;
constexpr int MAX_SUGGESTION_SCAN = 1'000'000;
constexpr int MAX_SUGGESTION_COUNT = 200;
constexpr const char* DEFAULT_HOST = "0.0.0.0";
constexpr const char* SPINDA_PAINTER_REFERENCE_URL = "https://spindapainter.neocities.org/";

const std::array<const char*, 25> NATURES = {
    "Hardy", "Lonely", "Brave", "Adamant", "Naughty", "Bold", "Docile",
    "Relaxed", "Impish", "Lax", "Timid", "Hasty", "Serious", "Jolly",
    "Naive", "Modest", "Mild", "Quiet", "Bashful", "Rash", "Calm",
    "Gentle", "Sassy", "Careful", "Quirky",
};

const std::array<const char*, 11> TRAIT_KEYS = {
    "balance_score",
    "centered_score",
    "cluster_score",
    "cursed_score",
    "eye_cover_score",
    "funny_score",
    "heartish_score",
    "horizontal_symmetry_score",
    "lower_face_cover_score",
    "spread_score",
    "vertical_symmetry_score",
};

struct Config {
    fs::path root;
    fs::path executable;
    fs::path phase3_dir;
    fs::path tsv_dir;
    fs::path hatch_output_dir;
    fs::path seven_zip_output_dir;
    std::string host = DEFAULT_HOST;
    int port = DEFAULT_PORT;
    int target_phase3_lanes = DEFAULT_TARGET_PHASE3_LANES;
    int sample_limit = DEFAULT_SAMPLE_LIMIT;
    std::string display_url;
};

struct Samples {
    std::map<std::string, std::vector<std::string>> values;

    void append(const std::string& key, const std::string& value, int limit) {
        if (limit <= 0) {
            return;
        }
        auto& bucket = values[key];
        if (static_cast<int>(bucket.size()) < limit) {
            bucket.push_back(value);
        }
    }
};

struct Phase3Summary {
    std::string folder;
    int target_lanes = 0;
    int complete_lanes = 0;
    int zip_files = 0;
    int missing_lanes = 0;
    std::uint64_t completed_spindas = 0;
    std::uint64_t target_spindas = 0;
    double progress_percent = 0.0;
    int bad_names = 0;
    int zero_size_zips = 0;
    int tiny_zips = 0;
    int tmp_files = 0;
    int duplicate_lanes = 0;
    int out_of_scope_zips = 0;
    int bad_artifacts = 0;
    std::optional<int> last_good_lane;
    std::vector<std::string> complete_lane_ranges;
    Samples samples;
};

struct RecentSave {
    double mtime_unix = 0.0;
    fs::path path;
    std::string name;
    int tsv = 0;
    int sid = 0;
};

struct TsvSummary {
    std::string folder;
    int expected_saves = EXPECTED_TSVS;
    int complete_saves = 0;
    int missing_saves = 0;
    double progress_percent = 0.0;
    int save_files = 0;
    int invalid_files = 0;
    int mismatched_files = 0;
    int duplicate_tsvs = 0;
    int duplicate_files = 0;
    std::string ledger_path;
    bool ledger_exists = false;
    std::optional<int> ledger_done;
    std::optional<int> ledger_errors;
    std::optional<std::string> ledger_load_error;
    std::vector<RecentSave> recent_saves;
    Samples samples;
};

struct PidLocation {
    std::uint32_t pid = 0;
    std::uint16_t upper = 0;
    std::uint16_t lower = 0;
    fs::path lane_zip;
    int expected_psv = 0;
    int matching_tsv = 0;
    int matching_sid_min = 0;
    int matching_sid_max = 0;
    bool zip_exists = false;
    std::string note;
};

struct SpindaSpot {
    std::string name;
    int offset_x = 0;
    int offset_y = 0;
    int x = 0;
    int y = 0;
    int width = 0;
    int height = 0;
    double center_x = 0.0;
    double center_y = 0.0;
};

struct SpindaStats {
    std::uint32_t pid_decimal = 0;
    std::string nature;
    std::string ability_slot;
    std::string gender;
    int tid = 0;
    int sid = 0;
    int rarity = 0;
    bool is_shiny = false;
    int tid0_sid0_rarity = 0;
    bool tid0_sid0_is_shiny = false;
};

struct SuggestionRow {
    std::uint32_t pid = 0;
    int offset = 0;
    double score = 0.0;
};

enum class Mode {
    Server,
    StatusJson,
    CommandsJson,
    PidJson,
    SuggestJson,
    SelfTest,
    Help,
};

struct Cli {
    Mode mode = Mode::Server;
    std::optional<fs::path> root;
    std::optional<fs::path> phase3_dir;
    std::optional<fs::path> tsv_dir;
    std::optional<fs::path> hatch_output_dir;
    std::optional<fs::path> seven_zip_output_dir;
    std::string host = DEFAULT_HOST;
    int port = DEFAULT_PORT;
    int target_phase3_lanes = DEFAULT_TARGET_PHASE3_LANES;
    int sample_limit = DEFAULT_SAMPLE_LIMIT;
    std::string pid_text;
    std::string suggest_mode;
    std::uint32_t start_pid = 0;
    int scan_limit = DEFAULT_SUGGESTION_SCAN;
    int count = DEFAULT_SUGGESTION_COUNT;
    int tid = 0;
    int sid = 0;
};

std::string lower_ascii(std::string text) {
    std::transform(text.begin(), text.end(), text.begin(), [](unsigned char c) {
        return static_cast<char>(std::tolower(c));
    });
    return text;
}

std::string trim(const std::string& text) {
    const auto first = text.find_first_not_of(" \t\r\n");
    if (first == std::string::npos) {
        return "";
    }
    const auto last = text.find_last_not_of(" \t\r\n");
    return text.substr(first, last - first + 1);
}

bool starts_with(const std::string& text, const std::string& prefix) {
    return text.rfind(prefix, 0) == 0;
}

char lower_ascii_char(char c) {
    return static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
}

bool ascii_iequals_at(const std::string& text, std::size_t offset, const char* literal) {
    const std::size_t literal_length = std::strlen(literal);
    if (offset > text.size() || text.size() - offset < literal_length) {
        return false;
    }
    for (std::size_t index = 0; index < literal_length; ++index) {
        if (lower_ascii_char(text[offset + index]) != lower_ascii_char(literal[index])) {
            return false;
        }
    }
    return true;
}

bool ascii_ends_with(const std::string& text, const char* suffix) {
    const std::size_t suffix_length = std::strlen(suffix);
    return text.size() >= suffix_length && ascii_iequals_at(text, text.size() - suffix_length, suffix);
}

bool ascii_contains(const std::string& text, const char* needle) {
    const std::size_t needle_length = std::strlen(needle);
    if (needle_length == 0) {
        return true;
    }
    if (needle_length > text.size()) {
        return false;
    }
    for (std::size_t offset = 0; offset <= text.size() - needle_length; ++offset) {
        if (ascii_iequals_at(text, offset, needle)) {
            return true;
        }
    }
    return false;
}

int hex_digit_value(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return 10 + (c - 'a');
    if (c >= 'A' && c <= 'F') return 10 + (c - 'A');
    return -1;
}

std::optional<int> parse_hex4_at(const std::string& text, std::size_t offset) {
    if (offset > text.size() || text.size() - offset < 4) {
        return std::nullopt;
    }
    int value = 0;
    for (std::size_t index = 0; index < 4; ++index) {
        const int digit = hex_digit_value(text[offset + index]);
        if (digit < 0) {
            return std::nullopt;
        }
        value = (value << 4) | digit;
    }
    return value;
}

std::optional<int> parse_phase3_zip_lane(const std::string& name) {
    if (name.size() != std::strlen("0x0000.spinda80.zip")) {
        return std::nullopt;
    }
    if (!ascii_iequals_at(name, 0, "0x") || !ascii_iequals_at(name, 6, ".spinda80.zip")) {
        return std::nullopt;
    }
    return parse_hex4_at(name, 2);
}

bool is_phase3_tmp_name(const std::string& name) {
    // Temp names are judged by shape only; the dashboard stays read-only and
    // does not inspect partial ZIP contents.
    return name.size() >= std::strlen("0x0000.spinda80.zip..tmp")
        && ascii_iequals_at(name, 0, "0x")
        && parse_hex4_at(name, 2).has_value()
        && ascii_iequals_at(name, 6, ".spinda80.zip.")
        && ascii_ends_with(name, ".tmp");
}

std::optional<std::pair<int, int>> parse_tsv_save_name(const std::string& name) {
    if (name.size() != std::strlen("TSV-0000-sid-00000.sav")) {
        return std::nullopt;
    }
    if (!ascii_iequals_at(name, 0, "TSV-") || !ascii_iequals_at(name, 8, "-sid-") || !ascii_iequals_at(name, 18, ".sav")) {
        return std::nullopt;
    }
    int tsv = 0;
    for (std::size_t index = 4; index < 8; ++index) {
        if (!std::isdigit(static_cast<unsigned char>(name[index]))) {
            return std::nullopt;
        }
        tsv = (tsv * 10) + (name[index] - '0');
    }
    int sid = 0;
    for (std::size_t index = 13; index < 18; ++index) {
        if (!std::isdigit(static_cast<unsigned char>(name[index]))) {
            return std::nullopt;
        }
        sid = (sid * 10) + (name[index] - '0');
    }
    return std::make_pair(tsv, sid);
}

double round_to(double value, double scale) {
    return std::nearbyint(value * scale) / scale;
}

double round3(double value) {
    return round_to(value, 1000.0);
}

double percent(int done, int total) {
    if (total <= 0) {
        return 0.0;
    }
    return round_to((static_cast<double>(done) / static_cast<double>(total)) * 100.0, 10000.0);
}

std::string json_escape(const std::string& text) {
    std::ostringstream out;
    for (unsigned char c : text) {
        switch (c) {
        case '\\':
            out << "\\\\";
            break;
        case '"':
            out << "\\\"";
            break;
        case '\b':
            out << "\\b";
            break;
        case '\f':
            out << "\\f";
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
            if (c < 0x20) {
                out << "\\u" << std::hex << std::setw(4) << std::setfill('0') << static_cast<int>(c)
                    << std::dec << std::setfill(' ');
            } else {
                out << static_cast<char>(c);
            }
        }
    }
    return out.str();
}

std::string json_string(const std::string& text) {
    return "\"" + json_escape(text) + "\"";
}

std::string json_bool(bool value) {
    return value ? "true" : "false";
}

std::string json_number(double value, int precision = 6) {
    if (!std::isfinite(value)) {
        return "0";
    }
    std::ostringstream out;
    out << std::fixed << std::setprecision(precision) << value;
    std::string text = out.str();
    while (text.find('.') != std::string::npos && text.back() == '0') {
        text.pop_back();
    }
    if (!text.empty() && text.back() == '.') {
        text.pop_back();
    }
    return text.empty() ? "0" : text;
}

std::string json_optional_int(const std::optional<int>& value) {
    return value ? std::to_string(*value) : "null";
}

std::string json_optional_double(const std::optional<double>& value, int precision = 6) {
    return value ? json_number(*value, precision) : "null";
}

std::string json_optional_string(const std::optional<std::string>& value) {
    return value ? json_string(*value) : "null";
}

std::string hex4(std::uint32_t value) {
    std::ostringstream out;
    out << "0x" << std::uppercase << std::hex << std::setw(4) << std::setfill('0') << (value & 0xFFFFU);
    return out.str();
}

std::string hex8(std::uint32_t value) {
    std::ostringstream out;
    out << "0x" << std::uppercase << std::hex << std::setw(8) << std::setfill('0') << value;
    return out.str();
}

std::string path_string(const fs::path& path) {
    return path.lexically_normal().string();
}

std::string ps_quote(const fs::path& path) {
    std::string text = path_string(path);
    std::string escaped;
    escaped.reserve(text.size() + 2);
    for (char c : text) {
        escaped.push_back(c);
        if (c == '\'') {
            escaped.push_back('\'');
        }
    }
    return "'" + escaped + "'";
}

fs::path lexical_absolute(const fs::path& path) {
    std::error_code ec;
    if (path.is_absolute()) {
        return path.lexically_normal();
    }
    return (fs::current_path(ec) / path).lexically_normal();
}

bool looks_like_repo_root(const fs::path& path) {
    std::error_code ec;
    return fs::exists(path / "tools" / "spinda", ec) && fs::exists(path / "markdown-files", ec);
}

fs::path executable_path_from_argv(const char* argv0) {
#ifdef _WIN32
    std::array<char, 32768> buffer{};
    DWORD size = GetModuleFileNameA(nullptr, buffer.data(), static_cast<DWORD>(buffer.size()));
    if (size > 0 && size < buffer.size()) {
        return fs::path(std::string(buffer.data(), size));
    }
#endif
    if (argv0 != nullptr && argv0[0] != '\0') {
        return lexical_absolute(fs::path(argv0));
    }
    return {};
}

fs::path find_default_root(const fs::path& executable) {
    std::error_code ec;
    fs::path current = fs::current_path(ec);
    for (fs::path probe = current; !probe.empty(); probe = probe.parent_path()) {
        if (looks_like_repo_root(probe)) {
            return probe.lexically_normal();
        }
        if (probe == probe.parent_path()) {
            break;
        }
    }
    for (fs::path probe = executable.parent_path(); !probe.empty(); probe = probe.parent_path()) {
        if (looks_like_repo_root(probe)) {
            return probe.lexically_normal();
        }
        if (probe == probe.parent_path()) {
            break;
        }
    }
    return current.lexically_normal();
}

std::uint64_t parse_uint_text(const std::string& raw, std::uint64_t min, std::uint64_t max, const std::string& name) {
    const std::string text = trim(raw);
    if (text.empty()) {
        throw std::runtime_error(name + " must not be empty");
    }

    auto range_error = [&]() {
        std::ostringstream out;
        out << name << " must be between " << min << " and " << max;
        return std::runtime_error(out.str());
    };

    if (text.front() == '-') {
        throw range_error();
    }

    std::size_t index = text.front() == '+' ? 1U : 0U;
    int base = 10;
    if (index + 2 <= text.size() && text[index] == '0' && (text[index + 1] == 'x' || text[index + 1] == 'X')) {
        base = 16;
        index += 2;
    }
    if (index >= text.size()) {
        throw std::runtime_error(name + " must be decimal or 0x-prefixed integer text");
    }

    std::uint64_t value = 0;
    bool saw_digit = false;
    bool overflowed = false;
    for (; index < text.size(); ++index) {
        const char ch = text[index];
        int digit = -1;
        if (ch >= '0' && ch <= '9') {
            digit = ch - '0';
        } else if (base == 16 && ch >= 'a' && ch <= 'f') {
            digit = 10 + (ch - 'a');
        } else if (base == 16 && ch >= 'A' && ch <= 'F') {
            digit = 10 + (ch - 'A');
        }
        if (digit < 0 || digit >= base) {
            if (saw_digit) {
                throw std::runtime_error(name + " has trailing characters");
            }
            throw std::runtime_error(name + " must be decimal or 0x-prefixed integer text");
        }
        saw_digit = true;
        // Parse directly against the caller's maximum so decimal strings such
        // as "010" stay decimal instead of inheriting base-0 octal behavior.
        const auto digit_value = static_cast<std::uint64_t>(digit);
        if (digit_value > max || value > (max - digit_value) / static_cast<std::uint64_t>(base)) {
            overflowed = true;
            continue;
        }
        if (!overflowed) {
            value = (value * static_cast<std::uint64_t>(base)) + digit_value;
        }
    }
    if (!saw_digit) {
        throw std::runtime_error(name + " must be decimal or 0x-prefixed integer text");
    }
    if (overflowed || value < min || value > max) {
        throw range_error();
    }
    return value;
}

std::uint32_t parse_pid_text(const std::string& pid_text) {
    std::string text = trim(pid_text);
    if (text.size() >= 4 && ascii_ends_with(text, ".pk3")) {
        text.resize(text.size() - 4);
    }
    if (text.size() >= 2 && ascii_iequals_at(text, 0, "0x")) {
        text = text.substr(2);
    }
    if (text.size() != 8) {
        throw std::runtime_error("PID must be 8 hex digits, with optional 0x prefix or .pk3 suffix");
    }
    std::uint32_t value = 0;
    for (char c : text) {
        const int digit = hex_digit_value(c);
        if (digit < 0) {
            throw std::runtime_error("PID must be 8 hex digits, with optional 0x prefix or .pk3 suffix");
        }
        value = (value << 4U) | static_cast<std::uint32_t>(digit);
    }
    return value;
}

std::string normalize_mode(std::string mode) {
    mode = lower_ascii(trim(mode));
    std::replace(mode.begin(), mode.end(), '-', '_');
    return mode;
}

std::string score_key_for_mode(const std::string& raw_mode) {
    const std::string mode = normalize_mode(raw_mode);
    static const std::map<std::string, std::string> mapping = {
        {"balanced", "balance_score"},
        {"centered", "centered_score"},
        {"clustered", "cluster_score"},
        {"cursed", "cursed_score"},
        {"eye", "eye_cover_score"},
        {"eye_cover", "eye_cover_score"},
        {"funny", "funny_score"},
        {"heart", "heartish_score"},
        {"horizontal_symmetry", "horizontal_symmetry_score"},
        {"spread", "spread_score"},
        {"symmetry", "vertical_symmetry_score"},
        {"vertical_symmetry", "vertical_symmetry_score"},
    };
    const auto found = mapping.find(mode);
    if (found == mapping.end()) {
        throw std::runtime_error(
            "mode must be one of: balanced, centered, clustered, cursed, eye, eye_cover, funny, heart, "
            "horizontal_symmetry, spread, symmetry, vertical_symmetry");
    }
    return found->second;
}

std::vector<std::string> lane_ids_to_ranges(std::vector<int> lanes) {
    std::sort(lanes.begin(), lanes.end());
    lanes.erase(std::unique(lanes.begin(), lanes.end()), lanes.end());
    std::vector<std::string> ranges;
    if (lanes.empty()) {
        return ranges;
    }
    int start = lanes.front();
    int previous = lanes.front();
    for (std::size_t index = 1; index < lanes.size(); ++index) {
        const int lane = lanes[index];
        if (lane == previous + 1) {
            previous = lane;
            continue;
        }
        ranges.push_back(start == previous ? hex4(start) : (hex4(start) + "-" + hex4(previous)));
        start = previous = lane;
    }
    ranges.push_back(start == previous ? hex4(start) : (hex4(start) + "-" + hex4(previous)));
    return ranges;
}

int normalize_target_phase3_lanes(int target_lanes) {
    return std::max(0, std::min(target_lanes, 0x10000));
}

std::pair<int, int> target_phase3_lane_bounds(int target_lanes) {
    if (target_lanes >= 0x10000) {
        return {0x0000, 0xFFFF};
    }
    if (target_lanes <= 0) {
        return {0x0001, 0x0000};
    }
    return {0x0001, std::min(target_lanes, 0xFFFF)};
}

Phase3Summary scan_phase3(const Config& config) {
    Phase3Summary summary;
    summary.folder = path_string(config.phase3_dir);
    summary.target_lanes = normalize_target_phase3_lanes(config.target_phase3_lanes);
    const auto [lane_min, lane_max] = target_phase3_lane_bounds(summary.target_lanes);

    std::array<unsigned char, 0x10000> seen{};
    std::vector<int> complete_lanes;
    complete_lanes.reserve(static_cast<std::size_t>(std::max(0, std::min(summary.target_lanes, 0x10000))));
    std::error_code ec;
    if (!fs::exists(config.phase3_dir, ec)) {
        summary.samples.append("folder_errors", "missing folder: " + path_string(config.phase3_dir), config.sample_limit);
    } else if (!fs::is_directory(config.phase3_dir, ec)) {
        summary.samples.append("folder_errors", "not a directory: " + path_string(config.phase3_dir), config.sample_limit);
    } else {
        for (const fs::directory_entry& entry : fs::directory_iterator(config.phase3_dir, ec)) {
            if (ec) {
                summary.samples.append("folder_errors", ec.message(), config.sample_limit);
                break;
            }
            std::error_code file_ec;
            if (!entry.is_regular_file(file_ec)) {
                continue;
            }
            const std::string name = entry.path().filename().string();
            const std::optional<int> maybe_lane = parse_phase3_zip_lane(name);
            if (maybe_lane) {
                ++summary.zip_files;
                const int lane = *maybe_lane;
                if (lane < lane_min || lane > lane_max) {
                    ++summary.out_of_scope_zips;
                    summary.samples.append(
                        "out_of_scope_zips",
                        name + " (target range " + hex4(lane_min) + "-" + hex4(lane_max) + ")",
                        config.sample_limit);
                    continue;
                }
                const std::uintmax_t size = entry.file_size(file_ec);
                if (file_ec || size <= 0) {
                    ++summary.zero_size_zips;
                    summary.samples.append("zero_size_zips", name, config.sample_limit);
                } else if (size < MIN_FINAL_ZIP_BYTES) {
                    ++summary.tiny_zips;
                    summary.samples.append("tiny_zips", name + " (" + std::to_string(size) + " bytes)", config.sample_limit);
                } else if (seen[static_cast<std::size_t>(lane)] != 0) {
                    ++summary.duplicate_lanes;
                    summary.samples.append("duplicate_lanes", name, config.sample_limit);
                } else {
                    seen[static_cast<std::size_t>(lane)] = 1;
                    complete_lanes.push_back(lane);
                    if (!summary.last_good_lane || lane > *summary.last_good_lane) {
                        summary.last_good_lane = lane;
                    }
                }
                continue;
            }
            if (ascii_ends_with(name, ".spinda80.zip") || ascii_contains(name, ".spinda80.zip.")) {
                if (is_phase3_tmp_name(name)) {
                    ++summary.tmp_files;
                    summary.samples.append("tmp_files", name, config.sample_limit);
                } else {
                    ++summary.bad_names;
                    summary.samples.append("bad_names", name, config.sample_limit);
                }
            }
        }
    }

    summary.complete_lanes = static_cast<int>(complete_lanes.size());
    summary.missing_lanes = std::max(0, summary.target_lanes - summary.complete_lanes);
    summary.completed_spindas = static_cast<std::uint64_t>(summary.complete_lanes) * SPINDAS_PER_LANE;
    summary.target_spindas = static_cast<std::uint64_t>(summary.target_lanes) * SPINDAS_PER_LANE;
    summary.progress_percent = percent(summary.complete_lanes, summary.target_lanes);
    summary.bad_artifacts = summary.bad_names + summary.zero_size_zips + summary.tiny_zips + summary.tmp_files
        + summary.duplicate_lanes + summary.out_of_scope_zips;
    summary.complete_lane_ranges = lane_ids_to_ranges(std::move(complete_lanes));
    return summary;
}

double file_time_to_unix_seconds(const fs::file_time_type& file_time) {
    const auto system_time = std::chrono::time_point_cast<std::chrono::system_clock::duration>(
        file_time - fs::file_time_type::clock::now() + std::chrono::system_clock::now());
    return std::chrono::duration<double>(system_time.time_since_epoch()).count();
}

std::optional<double> age_seconds(const fs::path& path) {
    std::error_code ec;
    const fs::file_time_type modified = fs::last_write_time(path, ec);
    if (ec) {
        return std::nullopt;
    }
    const double modified_unix = file_time_to_unix_seconds(modified);
    const double now_unix = std::chrono::duration<double>(std::chrono::system_clock::now().time_since_epoch()).count();
    return std::max(0.0, now_unix - modified_unix);
}

std::string read_text_file(const fs::path& path) {
    std::ifstream input(path, std::ios::binary);
    if (!input) {
        throw std::runtime_error("cannot open " + path_string(path));
    }
    std::ostringstream buffer;
    buffer << input.rdbuf();
    return buffer.str();
}

struct JsonValue {
    enum class Type {
        Null,
        Bool,
        Number,
        String,
        Array,
        Object,
    };

    Type type = Type::Null;
    bool bool_value = false;
    double number_value = 0.0;
    std::int64_t integer_value = 0;
    bool integer_number = false;
    std::string string_value;
    std::vector<JsonValue> array_value;
    std::map<std::string, JsonValue> object_value;
};

// The native workbench only needs JSON for the SID ledger, but a tiny parser is
// safer than counting `"error"` text: it preserves Python truthiness for
// strings, numbers, bools, nulls, arrays, and objects.
class JsonParser {
public:
    explicit JsonParser(std::string_view text) : text_(text) {}

    JsonValue parse() {
        skip_ws();
        JsonValue value = parse_value();
        skip_ws();
        if (position_ != text_.size()) {
            fail("trailing JSON content");
        }
        return value;
    }

private:
    std::string_view text_;
    std::size_t position_ = 0;

    [[noreturn]] void fail(const std::string& message) const {
        throw std::runtime_error(message + " at byte " + std::to_string(position_));
    }

    bool consume(char expected) {
        if (position_ < text_.size() && text_[position_] == expected) {
            ++position_;
            return true;
        }
        return false;
    }

    void expect(char expected) {
        if (!consume(expected)) {
            fail(std::string("expected '") + expected + "'");
        }
    }

    void skip_ws() {
        while (position_ < text_.size()) {
            const unsigned char c = static_cast<unsigned char>(text_[position_]);
            if (c != ' ' && c != '\t' && c != '\r' && c != '\n') {
                break;
            }
            ++position_;
        }
    }

    JsonValue parse_value() {
        if (position_ >= text_.size()) {
            fail("unexpected end of JSON");
        }
        switch (text_[position_]) {
        case '{':
            return parse_object();
        case '[':
            return parse_array();
        case '"':
            return parse_string_value();
        case 't':
            return parse_literal("true", true);
        case 'f':
            return parse_literal("false", false);
        case 'n':
            return parse_null();
        default:
            if (text_[position_] == '-' || std::isdigit(static_cast<unsigned char>(text_[position_]))) {
                return parse_number();
            }
            fail("unexpected JSON value");
        }
    }

    JsonValue parse_literal(const char* literal, bool value) {
        const std::size_t length = std::strlen(literal);
        if (position_ + length > text_.size() || text_.substr(position_, length) != literal) {
            fail(std::string("expected ") + literal);
        }
        position_ += length;
        JsonValue result;
        result.type = JsonValue::Type::Bool;
        result.bool_value = value;
        return result;
    }

    JsonValue parse_null() {
        if (position_ + 4 > text_.size() || text_.substr(position_, 4) != "null") {
            fail("expected null");
        }
        position_ += 4;
        return {};
    }

    JsonValue parse_string_value() {
        JsonValue value;
        value.type = JsonValue::Type::String;
        value.string_value = parse_string();
        return value;
    }

    std::string parse_string() {
        expect('"');
        std::string output;
        while (position_ < text_.size()) {
            const char c = text_[position_++];
            if (c == '"') {
                return output;
            }
            if (c == '\\') {
                if (position_ >= text_.size()) {
                    fail("unterminated JSON string escape");
                }
                const char escaped = text_[position_++];
                switch (escaped) {
                case '"':
                case '\\':
                case '/':
                    output.push_back(escaped);
                    break;
                case 'b':
                    output.push_back('\b');
                    break;
                case 'f':
                    output.push_back('\f');
                    break;
                case 'n':
                    output.push_back('\n');
                    break;
                case 'r':
                    output.push_back('\r');
                    break;
                case 't':
                    output.push_back('\t');
                    break;
                case 'u':
                    if (position_ + 4 > text_.size()) {
                        fail("short JSON unicode escape");
                    }
                    for (int index = 0; index < 4; ++index) {
                        if (hex_digit_value(text_[position_ + index]) < 0) {
                            fail("bad JSON unicode escape");
                        }
                    }
                    position_ += 4;
                    output.push_back('?');
                    break;
                default:
                    fail("bad JSON string escape");
                }
            } else {
                if (static_cast<unsigned char>(c) < 0x20) {
                    fail("control character in JSON string");
                }
                output.push_back(c);
            }
        }
        fail("unterminated JSON string");
    }

    JsonValue parse_number() {
        const std::size_t start = position_;
        if (consume('-') && position_ >= text_.size()) {
            fail("short JSON number");
        }
        if (consume('0')) {
            // Leading zero is allowed only for the literal zero.
        } else {
            if (position_ >= text_.size() || !std::isdigit(static_cast<unsigned char>(text_[position_]))) {
                fail("bad JSON number");
            }
            while (position_ < text_.size() && std::isdigit(static_cast<unsigned char>(text_[position_]))) {
                ++position_;
            }
        }
        bool integer = true;
        if (consume('.')) {
            integer = false;
            if (position_ >= text_.size() || !std::isdigit(static_cast<unsigned char>(text_[position_]))) {
                fail("bad JSON fraction");
            }
            while (position_ < text_.size() && std::isdigit(static_cast<unsigned char>(text_[position_]))) {
                ++position_;
            }
        }
        if (position_ < text_.size() && (text_[position_] == 'e' || text_[position_] == 'E')) {
            integer = false;
            ++position_;
            if (position_ < text_.size() && (text_[position_] == '+' || text_[position_] == '-')) {
                ++position_;
            }
            if (position_ >= text_.size() || !std::isdigit(static_cast<unsigned char>(text_[position_]))) {
                fail("bad JSON exponent");
            }
            while (position_ < text_.size() && std::isdigit(static_cast<unsigned char>(text_[position_]))) {
                ++position_;
            }
        }
        const std::string token(text_.substr(start, position_ - start));
        JsonValue value;
        value.type = JsonValue::Type::Number;
        value.integer_number = integer;
        value.number_value = std::stod(token);
        if (integer) {
            value.integer_value = std::stoll(token);
        }
        return value;
    }

    JsonValue parse_array() {
        JsonValue array;
        array.type = JsonValue::Type::Array;
        expect('[');
        skip_ws();
        if (consume(']')) {
            return array;
        }
        for (;;) {
            skip_ws();
            array.array_value.push_back(parse_value());
            skip_ws();
            if (consume(']')) {
                return array;
            }
            expect(',');
        }
    }

    JsonValue parse_object() {
        JsonValue object;
        object.type = JsonValue::Type::Object;
        expect('{');
        skip_ws();
        if (consume('}')) {
            return object;
        }
        for (;;) {
            skip_ws();
            if (position_ >= text_.size() || text_[position_] != '"') {
                fail("expected object key");
            }
            std::string key = parse_string();
            skip_ws();
            expect(':');
            skip_ws();
            object.object_value[std::move(key)] = parse_value();
            skip_ws();
            if (consume('}')) {
                return object;
            }
            expect(',');
        }
    }
};

bool json_truthy(const JsonValue& value) {
    switch (value.type) {
    case JsonValue::Type::Null:
        return false;
    case JsonValue::Type::Bool:
        return value.bool_value;
    case JsonValue::Type::Number:
        return value.number_value != 0.0;
    case JsonValue::Type::String:
        return !value.string_value.empty();
    case JsonValue::Type::Array:
        return !value.array_value.empty();
    case JsonValue::Type::Object:
        return !value.object_value.empty();
    }
    return false;
}

std::tuple<std::optional<int>, std::optional<int>, std::optional<std::string>> ledger_summary(const fs::path& ledger_path) {
    std::error_code ec;
    if (!fs::exists(ledger_path, ec)) {
        return {std::nullopt, std::nullopt, std::nullopt};
    }
    std::string text;
    try {
        text = read_text_file(ledger_path);
    } catch (const std::exception& error) {
        return {std::nullopt, std::nullopt, std::string(error.what())};
    }
    JsonValue root;
    try {
        root = JsonParser(text).parse();
    } catch (const std::exception& error) {
        return {std::nullopt, std::nullopt, std::string(error.what())};
    }
    if (root.type != JsonValue::Type::Object) {
        return {std::nullopt, std::nullopt, "top-level JSON is not an object"};
    }

    const auto entries = root.object_value.find("entries");
    if (entries == root.object_value.end() || entries->second.type != JsonValue::Type::Array) {
        const auto done = root.object_value.find("complete_shiny_values");
        if (done != root.object_value.end()
            && done->second.type == JsonValue::Type::Number
            && done->second.integer_number
            && done->second.integer_value >= std::numeric_limits<int>::min()
            && done->second.integer_value <= std::numeric_limits<int>::max()) {
            return {static_cast<int>(done->second.integer_value), std::nullopt, std::nullopt};
        }
        return {std::nullopt, std::nullopt, std::nullopt};
    }

    int done_count = 0;
    int error_count = 0;
    for (const JsonValue& entry : entries->second.array_value) {
        if (entry.type != JsonValue::Type::Object) {
            continue;
        }
        const auto done = entry.object_value.find("done");
        if (done != entry.object_value.end() && done->second.type == JsonValue::Type::Bool && done->second.bool_value) {
            ++done_count;
        }
        const auto error = entry.object_value.find("error");
        const auto route_error = entry.object_value.find("route_schedule_error");
        if ((error != entry.object_value.end() && json_truthy(error->second))
            || (route_error != entry.object_value.end() && json_truthy(route_error->second))) {
            ++error_count;
        }
    }
    return {done_count, error_count, std::nullopt};
}

TsvSummary scan_tsv(const Config& config) {
    TsvSummary summary;
    summary.folder = path_string(config.tsv_dir);
    std::map<int, std::vector<fs::path>> by_tsv;
    std::vector<RecentSave> recent;

    std::error_code ec;
    if (!fs::exists(config.tsv_dir, ec)) {
        summary.samples.append("folder_errors", "missing folder: " + path_string(config.tsv_dir), config.sample_limit);
    } else if (!fs::is_directory(config.tsv_dir, ec)) {
        summary.samples.append("folder_errors", "not a directory: " + path_string(config.tsv_dir), config.sample_limit);
    } else {
        for (const fs::directory_entry& entry : fs::directory_iterator(config.tsv_dir, ec)) {
            if (ec) {
                summary.samples.append("folder_errors", ec.message(), config.sample_limit);
                break;
            }
            std::error_code file_ec;
            if (!entry.is_regular_file(file_ec)) {
                continue;
            }
            const std::string name = entry.path().filename().string();
            if (!ascii_ends_with(name, ".sav")) {
                continue;
            }
            ++summary.save_files;
            const std::optional<std::pair<int, int>> parsed = parse_tsv_save_name(name);
            if (!parsed) {
                ++summary.invalid_files;
                summary.samples.append("invalid_files", name, config.sample_limit);
                continue;
            }
            const int tsv = parsed->first;
            const int sid = parsed->second;
            if (tsv < 0 || tsv >= EXPECTED_TSVS || sid < 0 || sid > 0xFFFF) {
                ++summary.invalid_files;
                summary.samples.append("out_of_range_files", name, config.sample_limit);
                continue;
            }
            if ((sid >> 3) != tsv) {
                ++summary.mismatched_files;
                summary.samples.append("mismatched_files", name, config.sample_limit);
                continue;
            }
            const fs::path path = config.tsv_dir / name;
            by_tsv[tsv].push_back(path);
            double mtime = 0.0;
            const auto file_time = entry.last_write_time(file_ec);
            if (!file_ec) {
                mtime = file_time_to_unix_seconds(file_time);
            }
            recent.push_back({mtime, path, name, tsv, sid});
        }
    }

    for (const auto& [tsv, paths] : by_tsv) {
        if (paths.size() > 1) {
            ++summary.duplicate_tsvs;
            summary.duplicate_files += static_cast<int>(paths.size() - 1);
            if (static_cast<int>(summary.samples.values["duplicate_tsvs"].size()) < config.sample_limit) {
                summary.samples.append("duplicate_tsvs", "TSV " + std::to_string(10000 + tsv).substr(1) + ": "
                    + std::to_string(paths.size()) + " saves", config.sample_limit);
            }
        }
    }

    std::sort(recent.begin(), recent.end(), [](const RecentSave& a, const RecentSave& b) {
        if (a.mtime_unix != b.mtime_unix) {
            return a.mtime_unix > b.mtime_unix;
        }
        return a.name > b.name;
    });
    if (recent.size() > 12) {
        recent.resize(12);
    }
    summary.recent_saves = std::move(recent);
    summary.ledger_path = path_string(config.tsv_dir / "_sid_shiny_value_ledger_tid_0x0000.json");
    summary.ledger_exists = fs::exists(config.tsv_dir / "_sid_shiny_value_ledger_tid_0x0000.json", ec);
    std::tie(summary.ledger_done, summary.ledger_errors, summary.ledger_load_error) =
        ledger_summary(config.tsv_dir / "_sid_shiny_value_ledger_tid_0x0000.json");
    summary.complete_saves = static_cast<int>(by_tsv.size());
    summary.missing_saves = std::max(0, EXPECTED_TSVS - summary.complete_saves);
    summary.progress_percent = percent(summary.complete_saves, EXPECTED_TSVS);
    return summary;
}

PidLocation locate_pid_int(std::uint32_t pid, const fs::path& phase3_dir) {
    PidLocation location;
    location.pid = pid;
    location.upper = static_cast<std::uint16_t>((pid >> 16) & 0xFFFFU);
    location.lower = static_cast<std::uint16_t>(pid & 0xFFFFU);
    const int psv = (location.upper ^ location.lower) >> 3;
    location.expected_psv = psv;
    location.matching_tsv = psv;
    location.matching_sid_min = psv << 3;
    location.matching_sid_max = (psv << 3) | 7;
    location.lane_zip = phase3_dir / (hex4(location.lower) + ".spinda80.zip");
    std::error_code ec;
    location.zip_exists = fs::is_regular_file(location.lane_zip, ec);
    location.note = location.zip_exists ? "ZIP file exists; deep validator proves entry later." : "ZIP file not present yet.";
    return location;
}

std::vector<SpindaSpot> spinda_spots(std::uint32_t pid) {
    struct Anchor {
        const char* name;
        int base_x;
        int base_y;
        int width;
        int height;
    };
    static const std::array<Anchor, 4> anchors = {{
        {"upper_left", 10, 13, 12, 12},
        {"upper_right", 34, 14, 13, 13},
        {"lower_left", 16, 31, 7, 9},
        {"lower_right", 28, 32, 8, 9},
    }};
    const std::array<int, 8> nibbles = {
        static_cast<int>(pid & 0xFU),
        static_cast<int>((pid >> 4) & 0xFU),
        static_cast<int>((pid >> 8) & 0xFU),
        static_cast<int>((pid >> 12) & 0xFU),
        static_cast<int>((pid >> 16) & 0xFU),
        static_cast<int>((pid >> 20) & 0xFU),
        static_cast<int>((pid >> 24) & 0xFU),
        static_cast<int>((pid >> 28) & 0xFU),
    };
    std::vector<SpindaSpot> spots;
    spots.reserve(4);
    for (std::size_t index = 0; index < anchors.size(); ++index) {
        const Anchor& anchor = anchors[index];
        SpindaSpot spot;
        spot.name = anchor.name;
        spot.offset_x = nibbles[index * 2];
        spot.offset_y = nibbles[index * 2 + 1];
        spot.x = anchor.base_x + spot.offset_x;
        spot.y = anchor.base_y + spot.offset_y;
        spot.width = anchor.width;
        spot.height = anchor.height;
        spot.center_x = round3(spot.x + (spot.width / 2.0));
        spot.center_y = round3(spot.y + (spot.height / 2.0));
        spots.push_back(std::move(spot));
    }
    return spots;
}

double distance(double ax, double ay, double bx, double by) {
    return std::hypot(ax - bx, ay - by);
}

double score_from_distance(double value, double max_distance) {
    if (max_distance <= 0.0) {
        return 0.0;
    }
    return round3(std::max(0.0, 100.0 - ((value / max_distance) * 100.0)));
}

double mirror_score(double left_x, double left_y, double right_x, double right_y, double center_x = 26.0) {
    const double mirror_error_x = std::abs((left_x - center_x) + (right_x - center_x));
    const double y_error = std::abs(left_y - right_y);
    return score_from_distance(std::hypot(mirror_error_x, y_error), 24.0);
}

double horizontal_mirror_score(double top_x, double top_y, double bottom_x, double bottom_y, double center_y = 30.0) {
    const double x_error = std::abs(top_x - bottom_x);
    const double mirror_error_y = std::abs((top_y - center_y) + (bottom_y - center_y));
    return score_from_distance(std::hypot(x_error, mirror_error_y), 28.0);
}

double average_center_distance(double x1, double y1, double x2, double y2, double x3, double y3, double x4, double y4) {
    return (distance(x1, y1, 26.0, 30.0) + distance(x2, y2, 26.0, 30.0)
        + distance(x3, y3, 26.0, 30.0) + distance(x4, y4, 26.0, 30.0)) / 4.0;
}

double average_pair_distance(double x1, double y1, double x2, double y2, double x3, double y3, double x4, double y4) {
    const double total = distance(x1, y1, x2, y2) + distance(x1, y1, x3, y3) + distance(x1, y1, x4, y4)
        + distance(x2, y2, x3, y3) + distance(x2, y2, x4, y4) + distance(x3, y3, x4, y4);
    return total / 6.0;
}

double pid_score(std::uint32_t pid, const std::string& score_key) {
    const double x1 = 16.0 + static_cast<double>(pid & 0xFU);
    const double y1 = 19.0 + static_cast<double>((pid >> 4) & 0xFU);
    const double x2 = 40.5 + static_cast<double>((pid >> 8) & 0xFU);
    const double y2 = 20.5 + static_cast<double>((pid >> 12) & 0xFU);
    const double x3 = 19.5 + static_cast<double>((pid >> 16) & 0xFU);
    const double y3 = 35.5 + static_cast<double>((pid >> 20) & 0xFU);
    const double x4 = 32.0 + static_cast<double>((pid >> 24) & 0xFU);
    const double y4 = 36.5 + static_cast<double>((pid >> 28) & 0xFU);

    if (score_key == "centered_score") {
        return score_from_distance(average_center_distance(x1, y1, x2, y2, x3, y3, x4, y4), 28.0);
    }
    if (score_key == "balance_score") {
        return score_from_distance(distance((x1 + x2 + x3 + x4) / 4.0, (y1 + y2 + y3 + y4) / 4.0, 26.0, 30.0), 18.0);
    }
    if (score_key == "cluster_score") {
        return score_from_distance(average_pair_distance(x1, y1, x2, y2, x3, y3, x4, y4), 32.0);
    }
    if (score_key == "spread_score") {
        return std::min(100.0, (average_pair_distance(x1, y1, x2, y2, x3, y3, x4, y4) / 28.0) * 100.0);
    }
    if (score_key == "eye_cover_score") {
        return (score_from_distance(distance(x1, y1, 19.5, 24.0), 18.0)
            + score_from_distance(distance(x2, y2, 38.0, 24.5), 18.0)) / 2.0;
    }
    if (score_key == "lower_face_cover_score") {
        return (score_from_distance(distance(x3, y3, 23.5, 39.5), 18.0)
            + score_from_distance(distance(x4, y4, 31.5, 40.0), 18.0)) / 2.0;
    }
    if (score_key == "vertical_symmetry_score") {
        return (mirror_score(x1, y1, x2, y2) + mirror_score(x3, y3, x4, y4)) / 2.0;
    }
    if (score_key == "horizontal_symmetry_score") {
        return (horizontal_mirror_score(x1, y1, x3, y3) + horizontal_mirror_score(x2, y2, x4, y4)) / 2.0;
    }
    if (score_key == "heartish_score") {
        const double vertical = (mirror_score(x1, y1, x2, y2) + mirror_score(x3, y3, x4, y4)) / 2.0;
        const double centered = score_from_distance(average_center_distance(x1, y1, x2, y2, x3, y3, x4, y4), 28.0);
        const double lower = (score_from_distance(distance(x3, y3, 23.5, 39.5), 18.0)
            + score_from_distance(distance(x4, y4, 31.5, 40.0), 18.0)) / 2.0;
        return (vertical + centered + lower) / 3.0;
    }
    if (score_key == "funny_score") {
        const double eye = (score_from_distance(distance(x1, y1, 19.5, 24.0), 18.0)
            + score_from_distance(distance(x2, y2, 38.0, 24.5), 18.0)) / 2.0;
        const double vertical = (mirror_score(x1, y1, x2, y2) + mirror_score(x3, y3, x4, y4)) / 2.0;
        const double cluster = score_from_distance(average_pair_distance(x1, y1, x2, y2, x3, y3, x4, y4), 32.0);
        const double centered = score_from_distance(average_center_distance(x1, y1, x2, y2, x3, y3, x4, y4), 28.0);
        return ((eye * 2.0) + vertical + cluster + centered) / 5.0;
    }
    if (score_key == "cursed_score") {
        const double eye = (score_from_distance(distance(x1, y1, 19.5, 24.0), 18.0)
            + score_from_distance(distance(x2, y2, 38.0, 24.5), 18.0)) / 2.0;
        const double lower = (score_from_distance(distance(x3, y3, 23.5, 39.5), 18.0)
            + score_from_distance(distance(x4, y4, 31.5, 40.0), 18.0)) / 2.0;
        const double spread = std::min(100.0, (average_pair_distance(x1, y1, x2, y2, x3, y3, x4, y4) / 28.0) * 100.0);
        const double vertical = (mirror_score(x1, y1, x2, y2) + mirror_score(x3, y3, x4, y4)) / 2.0;
        return (eye + lower + spread + (100.0 - vertical)) / 4.0;
    }
    throw std::runtime_error("unknown score key: " + score_key);
}

std::map<std::string, double> spinda_traits(std::uint32_t pid) {
    std::map<std::string, double> traits;
    for (const char* key : TRAIT_KEYS) {
        traits[key] = round3(pid_score(pid, key));
    }
    return traits;
}

std::vector<std::string> spinda_trait_labels(const std::map<std::string, double>& traits) {
    std::vector<std::string> labels;
    auto score = [&](const std::string& key) -> double {
        const auto found = traits.find(key);
        return found == traits.end() ? 0.0 : found->second;
    };
    if (score("centered_score") >= 70.0) labels.push_back("centered");
    if (score("balance_score") >= 80.0) labels.push_back("balanced");
    if (score("eye_cover_score") >= 65.0) labels.push_back("eye-covering");
    if (score("cluster_score") >= 70.0) labels.push_back("clustered");
    if (score("spread_score") >= 72.0) labels.push_back("wide-spread");
    if (score("vertical_symmetry_score") >= 75.0) labels.push_back("rare vertical symmetry");
    if (score("horizontal_symmetry_score") >= 75.0) labels.push_back("rare horizontal symmetry");
    if (score("heartish_score") >= 70.0) labels.push_back("heart-ish");
    if (score("funny_score") >= 70.0) labels.push_back("funny-face candidate");
    if (score("cursed_score") >= 70.0) labels.push_back("cursed-face candidate");
    if (labels.empty()) {
        labels.push_back("plain");
    }
    return labels;
}

SpindaStats spinda_stats(std::uint32_t pid, int tid, int sid) {
    const int upper = static_cast<int>((pid >> 16) & 0xFFFFU);
    const int lower = static_cast<int>(pid & 0xFFFFU);
    const int shiny_value = (upper ^ lower ^ tid ^ sid) & 0xFFFF;
    SpindaStats stats;
    stats.pid_decimal = pid;
    stats.nature = NATURES[pid % NATURES.size()];
    stats.ability_slot = (pid % 2U == 0U) ? "First" : "Second";
    stats.gender = (pid % 256U >= 127U) ? "Male" : "Female";
    stats.tid = tid;
    stats.sid = sid;
    stats.rarity = shiny_value;
    stats.is_shiny = shiny_value < 8;
    stats.tid0_sid0_rarity = upper ^ lower;
    stats.tid0_sid0_is_shiny = stats.tid0_sid0_rarity < 8;
    return stats;
}

std::string render_spinda_svg(std::uint32_t pid, const std::vector<SpindaSpot>& spots, bool shiny) {
    const char* spot_color = shiny ? "#90a038" : "#de6b39";
    const char* body_color = shiny ? "#efe8ba" : "#f1dfc5";
    std::ostringstream ellipses;
    for (const SpindaSpot& spot : spots) {
        ellipses << "\n  <ellipse cx=\"" << json_number(spot.center_x, 2) << "\" cy=\"" << json_number(spot.center_y, 2)
                 << "\" rx=\"" << json_number(spot.width / 2.0, 2) << "\" ry=\"" << json_number(spot.height / 2.0, 2)
                 << "\" fill=\"" << spot_color << "\" opacity=\"0.92\" />";
    }
    std::ostringstream out;
    out << "<svg class=\"spinda-svg\" xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 52 59\" role=\"img\" aria-label=\"Spinda "
        << std::uppercase << std::hex << std::setw(8) << std::setfill('0') << pid << std::dec << "\">\n"
        << "  <rect width=\"52\" height=\"59\" rx=\"5\" fill=\"#12161a\"/>\n"
        << "  <ellipse cx=\"12\" cy=\"18\" rx=\"7\" ry=\"9\" fill=\"" << body_color << "\" stroke=\"#4b3a31\" stroke-width=\"1.1\"/>\n"
        << "  <ellipse cx=\"40\" cy=\"18\" rx=\"7\" ry=\"9\" fill=\"" << body_color << "\" stroke=\"#4b3a31\" stroke-width=\"1.1\"/>\n"
        << "  <ellipse cx=\"26\" cy=\"31\" rx=\"21\" ry=\"24\" fill=\"" << body_color << "\" stroke=\"#4b3a31\" stroke-width=\"1.25\"/>"
        << ellipses.str() << "\n"
        << "  <circle cx=\"20\" cy=\"25\" r=\"2.2\" fill=\"#342820\"/>\n"
        << "  <circle cx=\"36\" cy=\"25\" r=\"2.2\" fill=\"#342820\"/>\n"
        << "  <path d=\"M22 38 C25 41, 29 41, 32 38\" fill=\"none\" stroke=\"#342820\" stroke-width=\"1.25\" stroke-linecap=\"round\"/>\n"
        << "</svg>";
    return out.str();
}

std::string samples_json(const Samples& samples) {
    std::ostringstream out;
    out << "{";
    bool first_key = true;
    for (const auto& [key, values] : samples.values) {
        if (!first_key) out << ",";
        first_key = false;
        out << json_string(key) << ":[";
        for (std::size_t index = 0; index < values.size(); ++index) {
            if (index != 0) out << ",";
            out << json_string(values[index]);
        }
        out << "]";
    }
    out << "}";
    return out.str();
}

std::string string_array_json(const std::vector<std::string>& values) {
    std::ostringstream out;
    out << "[";
    for (std::size_t index = 0; index < values.size(); ++index) {
        if (index != 0) out << ",";
        out << json_string(values[index]);
    }
    out << "]";
    return out.str();
}

std::string phase3_json(const Phase3Summary& summary) {
    std::ostringstream out;
    out << "{"
        << "\"folder\":" << json_string(summary.folder)
        << ",\"target_lanes\":" << summary.target_lanes
        << ",\"complete_lanes\":" << summary.complete_lanes
        << ",\"zip_files\":" << summary.zip_files
        << ",\"missing_lanes\":" << summary.missing_lanes
        << ",\"completed_spindas\":" << summary.completed_spindas
        << ",\"target_spindas\":" << summary.target_spindas
        << ",\"progress_percent\":" << json_number(summary.progress_percent, 4)
        << ",\"bad_names\":" << summary.bad_names
        << ",\"zero_size_zips\":" << summary.zero_size_zips
        << ",\"tiny_zips\":" << summary.tiny_zips
        << ",\"tmp_files\":" << summary.tmp_files
        << ",\"duplicate_lanes\":" << summary.duplicate_lanes
        << ",\"out_of_scope_zips\":" << summary.out_of_scope_zips
        << ",\"bad_artifacts\":" << summary.bad_artifacts
        << ",\"last_good_lane\":" << (summary.last_good_lane ? json_string(hex4(*summary.last_good_lane)) : "null")
        << ",\"complete_lane_ranges\":" << string_array_json(summary.complete_lane_ranges)
        << ",\"samples\":" << samples_json(summary.samples)
        << "}";
    return out.str();
}

std::string tsv_json(const TsvSummary& summary) {
    std::ostringstream recent;
    recent << "[";
    for (std::size_t index = 0; index < summary.recent_saves.size(); ++index) {
        const RecentSave& save = summary.recent_saves[index];
        if (index != 0) recent << ",";
        recent << "{"
               << "\"name\":" << json_string(save.name)
               << ",\"path\":" << json_string(path_string(save.path))
               << ",\"tsv\":" << save.tsv
               << ",\"sid\":" << save.sid
               << ",\"mtime_unix\":" << json_number(save.mtime_unix, 6)
               << "}";
    }
    recent << "]";

    std::ostringstream out;
    out << "{"
        << "\"folder\":" << json_string(summary.folder)
        << ",\"expected_saves\":" << summary.expected_saves
        << ",\"complete_saves\":" << summary.complete_saves
        << ",\"missing_saves\":" << summary.missing_saves
        << ",\"progress_percent\":" << json_number(summary.progress_percent, 4)
        << ",\"save_files\":" << summary.save_files
        << ",\"invalid_files\":" << summary.invalid_files
        << ",\"mismatched_files\":" << summary.mismatched_files
        << ",\"duplicate_tsvs\":" << summary.duplicate_tsvs
        << ",\"duplicate_files\":" << summary.duplicate_files
        << ",\"ledger_path\":" << json_string(summary.ledger_path)
        << ",\"ledger_exists\":" << json_bool(summary.ledger_exists)
        << ",\"ledger_done\":" << json_optional_int(summary.ledger_done)
        << ",\"ledger_errors\":" << json_optional_int(summary.ledger_errors)
        << ",\"ledger_load_error\":" << json_optional_string(summary.ledger_load_error)
        << ",\"recent_saves\":" << recent.str()
        << ",\"samples\":" << samples_json(summary.samples)
        << "}";
    return out.str();
}

std::map<std::string, std::string> command_previews(const Config& config) {
    const std::string native_command = config.executable.empty() ? "spinda_workbench_native.exe" : ("& " + ps_quote(config.executable));
    const auto python_command = [&]() -> std::string {
        for (const fs::path& candidate : {
                 config.root / ".venv-mgba" / "bin" / "python.exe",
                 config.root / ".venv-mgba" / "Scripts" / "python.exe",
             }) {
            std::error_code ec;
            if (fs::is_regular_file(candidate, ec)) {
                return "& " + ps_quote(candidate);
            }
        }
        return "python";
    }();
    std::map<std::string, std::string> commands;
    commands["workbench_native"] = native_command + " --phase3-dir " + ps_quote(config.phase3_dir) + " --tsv-dir " + ps_quote(config.tsv_dir);
    commands["phase3_manifest"] = python_command + " " + ps_quote(config.root / "tools" / "spinda" / "phase3_zip_validator.py")
        + " --root " + ps_quote(config.phase3_dir) + " --manifest-only";
    commands["phase3_deep_zip"] = python_command + " " + ps_quote(config.root / "tools" / "spinda" / "phase3_zip_validator.py")
        + " --root " + ps_quote(config.phase3_dir);
    commands["phase3_pkhex"] = "dotnet run --project "
        + ps_quote(config.root / "tools" / "spinda" / "phase3_pkhex_validator" / "Phase3PkhexValidator.csproj")
        + " -- --input-dir " + ps_quote(config.phase3_dir);
    commands["tsv_party"] = "dotnet run --project "
        + ps_quote(config.root / "tools" / "verify_tsv_party_slot" / "VerifyTsvPartySlot.csproj")
        + " -- --save-dir " + ps_quote(config.tsv_dir);
    commands["hatch_splitter"] = "dotnet run --project "
        + ps_quote(config.root / "tools" / "spinda" / "hatch_zip_splitter" / "SpindaHatchZipSplitter.csproj")
        + " -c Release -- --input-dir " + ps_quote(config.phase3_dir)
        + " --save-dir " + ps_quote(config.tsv_dir)
        + " --shiny-output " + ps_quote(config.hatch_output_dir / "spinda-hatched-shiny.zip")
        + " --not-shiny-output " + ps_quote(config.hatch_output_dir / "spinda-hatched-not-shiny.zip")
        + " --report " + ps_quote(config.hatch_output_dir / "_spinda_hatch_zip_splitter_report.json")
        + " --overwrite";
    commands["zip_to_7z_gui"] = python_command + " " + ps_quote(config.root / "tools" / "spinda" / "zip_to_7z_gui" / "zip_to_7z_gui.py");
    return commands;
}

std::string commands_json(const Config& config) {
    const auto commands = command_previews(config);
    std::ostringstream out;
    out << "{";
    bool first = true;
    for (const auto& [key, value] : commands) {
        if (!first) out << ",";
        first = false;
        out << json_string(key) << ":" << json_string(value);
    }
    out << "}";
    return out.str();
}

std::string tool_readiness_json(const Config& config) {
    const std::map<std::string, fs::path> paths = {
        {"phase3_zip_validator", config.root / "tools" / "spinda" / "phase3_zip_validator.py"},
        {"phase3_pkhex_validator", config.root / "tools" / "spinda" / "phase3_pkhex_validator" / "Phase3PkhexValidator.csproj"},
        {"tsv_party_verifier", config.root / "tools" / "verify_tsv_party_slot" / "VerifyTsvPartySlot.csproj"},
        {"hatch_splitter", config.root / "tools" / "spinda" / "hatch_zip_splitter" / "SpindaHatchZipSplitter.csproj"},
        {"zip_to_7z_gui", config.root / "tools" / "spinda" / "zip_to_7z_gui" / "zip_to_7z_gui.py"},
        {"hatch_report", config.hatch_output_dir / "_spinda_hatch_zip_splitter_report.json"},
        {"workbench_native", config.executable},
    };
    std::ostringstream out;
    out << "{";
    bool first = true;
    for (const auto& [name, path] : paths) {
        if (!first) out << ",";
        first = false;
        std::error_code ec;
        const bool exists = !path.empty() && fs::exists(path, ec);
        out << json_string(name) << ":{"
            << "\"path\":" << json_string(path_string(path))
            << ",\"exists\":" << json_bool(exists)
            << ",\"age_seconds\":" << json_optional_double(exists ? age_seconds(path) : std::nullopt, 6)
            << "}";
    }
    out << "}";
    return out.str();
}

std::string snapshot_json(const Config& config) {
    const Phase3Summary phase3 = scan_phase3(config);
    const TsvSummary tsv = scan_tsv(config);
    std::vector<std::string> blockers;
    if (phase3.complete_lanes < phase3.target_lanes) blockers.push_back("Phase 3 lane ZIPs incomplete");
    if (phase3.bad_artifacts != 0) blockers.push_back("Phase 3 output folder has bad artifacts");
    if (tsv.complete_saves < EXPECTED_TSVS) blockers.push_back("TSV save bank incomplete");
    if (tsv.invalid_files || tsv.mismatched_files || tsv.duplicate_tsvs) blockers.push_back("TSV save folder has naming/mapping issues");
    if (tsv.ledger_load_error) blockers.push_back("SID ledger cannot be read");

    const double generated = std::chrono::duration<double>(std::chrono::system_clock::now().time_since_epoch()).count();
    std::ostringstream out;
    out << "{"
        << "\"generated_at_unix\":" << json_number(generated, 6)
        << ",\"phase3\":" << phase3_json(phase3)
        << ",\"tsv\":" << tsv_json(tsv)
        << ",\"tools\":" << tool_readiness_json(config)
        << ",\"commands\":" << commands_json(config)
        << ",\"readiness\":{\"ready_for_hatch_splitter\":" << json_bool(blockers.empty())
        << ",\"blocked_by\":" << string_array_json(blockers) << "}"
        << ",\"server\":{\"display_url\":" << json_string(config.display_url)
        << ",\"read_only\":true"
        << ",\"root\":" << json_string(path_string(config.root))
        << ",\"runtime\":\"native-cpp\"}"
        << "}";
    return out.str();
}

std::string spots_json(const std::vector<SpindaSpot>& spots) {
    std::ostringstream out;
    out << "[";
    for (std::size_t index = 0; index < spots.size(); ++index) {
        const SpindaSpot& spot = spots[index];
        if (index != 0) out << ",";
        out << "{"
            << "\"name\":" << json_string(spot.name)
            << ",\"offset_x\":" << spot.offset_x
            << ",\"offset_y\":" << spot.offset_y
            << ",\"x\":" << spot.x
            << ",\"y\":" << spot.y
            << ",\"width\":" << spot.width
            << ",\"height\":" << spot.height
            << ",\"center_x\":" << json_number(spot.center_x, 3)
            << ",\"center_y\":" << json_number(spot.center_y, 3)
            << "}";
    }
    out << "]";
    return out.str();
}

std::string stats_json(const SpindaStats& stats) {
    std::ostringstream out;
    out << "{"
        << "\"pid_decimal\":" << stats.pid_decimal
        << ",\"nature\":" << json_string(stats.nature)
        << ",\"ability_slot\":" << json_string(stats.ability_slot)
        << ",\"gender\":" << json_string(stats.gender)
        << ",\"tid\":" << stats.tid
        << ",\"sid\":" << stats.sid
        << ",\"rarity\":" << stats.rarity
        << ",\"is_shiny\":" << json_bool(stats.is_shiny)
        << ",\"tid0_sid0_rarity\":" << stats.tid0_sid0_rarity
        << ",\"tid0_sid0_is_shiny\":" << json_bool(stats.tid0_sid0_is_shiny)
        << "}";
    return out.str();
}

std::string traits_json(const std::map<std::string, double>& traits) {
    std::ostringstream out;
    out << "{";
    bool first = true;
    for (const auto& [key, value] : traits) {
        if (!first) out << ",";
        first = false;
        out << json_string(key) << ":" << json_number(value, 3);
    }
    out << "}";
    return out.str();
}

std::string pid_report_json(const std::string& pid_text, const Config& config, int tid, int sid) {
    const std::uint32_t pid = parse_pid_text(pid_text);
    const PidLocation location = locate_pid_int(pid, config.phase3_dir);
    const std::vector<SpindaSpot> spots = spinda_spots(pid);
    const SpindaStats stats = spinda_stats(pid, tid, sid);
    const std::map<std::string, double> traits = spinda_traits(pid);
    const std::vector<std::string> labels = spinda_trait_labels(traits);
    std::ostringstream out;
    out << "{"
        << "\"pid\":" << json_string(hex8(pid))
        << ",\"upper\":" << json_string(hex4(location.upper))
        << ",\"lower\":" << json_string(hex4(location.lower))
        << ",\"lane_zip\":" << json_string(path_string(location.lane_zip))
        << ",\"entry_name\":" << json_string(hex8(pid) + ".pk3")
        << ",\"expected_psv\":" << location.expected_psv
        << ",\"matching_tsv\":" << location.matching_tsv
        << ",\"matching_sid_min\":" << location.matching_sid_min
        << ",\"matching_sid_max\":" << location.matching_sid_max
        << ",\"zip_exists\":" << json_bool(location.zip_exists)
        << ",\"note\":" << json_string(location.note)
        << ",\"painter\":{"
        << "\"source_reference\":" << json_string(SPINDA_PAINTER_REFERENCE_URL)
        << ",\"coordinate_model\":\"original-painter-nibble-grid\""
        << ",\"spots\":" << spots_json(spots)
        << ",\"stats\":" << stats_json(stats)
        << ",\"traits\":" << traits_json(traits)
        << ",\"labels\":" << string_array_json(labels)
        << ",\"svg\":" << json_string(render_spinda_svg(pid, spots, stats.is_shiny))
        << "}"
        << "}";
    return out.str();
}

std::string suggestion_json(const std::string& mode, const Config& config, std::uint32_t start_pid, int scan_limit, int count, int tid, int sid) {
    const std::string clean_mode = normalize_mode(mode);
    const std::string score_key = score_key_for_mode(clean_mode);
    scan_limit = std::max(1, std::min(scan_limit, MAX_SUGGESTION_SCAN));
    count = std::max(1, std::min({count, MAX_SUGGESTION_COUNT, scan_limit}));

    auto better = [](const SuggestionRow& a, const SuggestionRow& b) {
        if (a.score != b.score) return a.score > b.score;
        if (a.offset != b.offset) return a.offset < b.offset;
        return a.pid < b.pid;
    };
    struct WorstFirst {
        decltype(better)* better;

        bool operator()(const SuggestionRow& a, const SuggestionRow& b) const {
            return (*better)(a, b);
        }
    };

    const auto started = std::chrono::steady_clock::now();
    std::priority_queue<SuggestionRow, std::vector<SuggestionRow>, WorstFirst> heap((WorstFirst{&better}));
    for (int offset = 0; offset < scan_limit; ++offset) {
        const std::uint32_t pid = start_pid + static_cast<std::uint32_t>(offset);
        SuggestionRow row{pid, offset, pid_score(pid, score_key)};
        if (static_cast<int>(heap.size()) < count) {
            heap.push(row);
        } else if (better(row, heap.top())) {
            heap.pop();
            heap.push(row);
        }
    }
    const double elapsed = std::max(0.000001, std::chrono::duration<double>(std::chrono::steady_clock::now() - started).count());

    std::vector<SuggestionRow> winners;
    winners.reserve(heap.size());
    while (!heap.empty()) {
        winners.push_back(heap.top());
        heap.pop();
    }
    std::sort(winners.begin(), winners.end(), better);

    std::ostringstream rows;
    rows << "[";
    bool first = true;
    for (const SuggestionRow& row : winners) {
        if (!first) rows << ",";
        first = false;
        const PidLocation location = locate_pid_int(row.pid, config.phase3_dir);
        const std::map<std::string, double> traits = spinda_traits(row.pid);
        const SpindaStats stats = spinda_stats(row.pid, tid, sid);
        rows << "{"
             << "\"pid\":" << json_string(hex8(row.pid))
             << ",\"offset\":" << row.offset
             << ",\"score\":" << json_number(round3(row.score), 3)
             << ",\"lane_zip\":" << json_string(path_string(location.lane_zip))
             << ",\"entry_name\":" << json_string(hex8(row.pid) + ".pk3")
             << ",\"matching_tsv\":" << location.matching_tsv
             << ",\"zip_exists\":" << json_bool(location.zip_exists)
             << ",\"rarity\":" << stats.rarity
             << ",\"is_shiny\":" << json_bool(stats.is_shiny)
             << ",\"labels\":" << string_array_json(spinda_trait_labels(traits))
             << ",\"traits\":" << traits_json(traits)
             << "}";
    }
    rows << "]";

    std::ostringstream out;
    out << "{"
        << "\"mode\":" << json_string(clean_mode)
        << ",\"score_key\":" << json_string(score_key)
        << ",\"start_pid\":" << json_string(hex8(start_pid))
        << ",\"scan_limit\":" << scan_limit
        << ",\"count\":" << count
        << ",\"elapsed_seconds\":" << json_number(round_to(elapsed, 1'000'000.0), 6)
        << ",\"pids_per_second\":" << json_number(round_to(scan_limit / elapsed, 100.0), 2)
        << ",\"results\":" << rows.str()
        << "}";
    return out.str();
}

std::map<std::string, std::string> parse_query(const std::string& query);

std::string query_int_or_default(
    const std::map<std::string, std::string>& query,
    const std::string& key,
    const std::string& default_value) {
    const auto found = query.find(key);
    if (found == query.end() || trim(found->second).empty()) {
        return default_value;
    }
    return found->second;
}

const char* HTML = R"HTML(
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Spinda Workbench Native</title>
  <style>
    :root { color-scheme: dark; --bg:#101214; --panel:#171b1f; --line:#303840; --text:#edf1f3; --muted:#a8b0b7; --green:#38c172; --amber:#e0a83a; --red:#e15a5a; --blue:#4ea1d3; }
    * { box-sizing: border-box; }
    body { margin:0; background:var(--bg); color:var(--text); font:14px/1.45 "Segoe UI", system-ui, sans-serif; }
    header { display:flex; justify-content:space-between; gap:16px; padding:18px 22px; border-bottom:1px solid var(--line); background:#15191d; position:sticky; top:0; }
    h1 { font-size:20px; margin:0; }
    main { padding:18px 22px 32px; max-width:1500px; margin:0 auto; }
    .grid { display:grid; grid-template-columns:repeat(4,minmax(180px,1fr)); gap:12px; }
    .panel { background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:14px; min-width:0; }
    .wide { grid-column:span 2; }
    .full { grid-column:1 / -1; }
    h2 { margin:0 0 10px; font-size:14px; color:var(--muted); }
    .metric { font-size:30px; font-weight:700; }
    .sub { color:var(--muted); overflow-wrap:anywhere; }
    .ok { color:var(--green); } .warn { color:var(--amber); } .bad { color:var(--red); }
    table { width:100%; border-collapse:collapse; }
    td, th { border-bottom:1px solid var(--line); padding:7px 6px; text-align:left; vertical-align:top; }
    th { color:var(--muted); font-weight:600; }
    input, button, select { color:var(--text); background:#1f252b; border:1px solid var(--line); border-radius:6px; padding:8px 10px; font:inherit; }
    button { cursor:pointer; } button:hover { border-color:var(--blue); } button:disabled { cursor:wait; opacity:.65; }
    button.link { border:0; padding:0; background:transparent; color:var(--blue); font-family:Consolas, monospace; }
    progress { width:100%; height:12px; accent-color:var(--green); }
    pre { background:#1f252b; border:1px solid var(--line); border-radius:6px; padding:10px; overflow-x:auto; white-space:pre-wrap; }
    code, pre { font-family:"Cascadia Mono", Consolas, monospace; font-size:12px; }
    .row { display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
    .short { width:88px; }
    .pill { display:inline-block; padding:2px 8px; border-radius:999px; border:1px solid var(--line); color:var(--muted); margin:2px 4px 2px 0; }
    .painter { display:grid; grid-template-columns:minmax(140px,190px) 1fr; gap:12px; margin-top:12px; }
    .preview { min-height:210px; display:grid; place-items:center; background:#1f252b; border:1px solid var(--line); border-radius:8px; padding:12px; }
    .spinda-svg { width:100%; max-width:170px; height:auto; image-rendering:pixelated; }
    @media (max-width:900px) { .grid { grid-template-columns:1fr; } .wide { grid-column:span 1; } header { flex-direction:column; } .painter { grid-template-columns:1fr; } }
  </style>
</head>
<body>
  <header><h1>Spinda Workbench</h1><div class="sub">Native C++ read-only panel. Last update: <span id="updated">never</span></div></header>
  <main><section class="grid">
    <div class="panel"><h2>Phase 3 Lanes</h2><div class="metric" id="phase3-count">0 / 0</div><progress id="phase3-progress" value="0" max="100"></progress><div class="sub" id="phase3-sub"></div></div>
    <div class="panel"><h2>Spinda Records</h2><div class="metric" id="spinda-count">0</div><div class="sub" id="spinda-sub"></div></div>
    <div class="panel"><h2>TSV Saves</h2><div class="metric" id="tsv-count">0 / 8192</div><progress id="tsv-progress" value="0" max="100"></progress><div class="sub" id="tsv-sub"></div></div>
    <div class="panel"><h2>Hatch Readiness</h2><div class="metric" id="hatch-ready">check</div><div class="sub" id="hatch-blockers"></div></div>
    <div class="panel wide"><h2>Health</h2><table><tbody id="health-table"></tbody></table></div>
    <div class="panel wide"><h2>PID Locator / Painter</h2><div class="row"><input id="pid-input" placeholder="0x12345678"><input id="tid-input" class="short" placeholder="TID" value="0"><input id="sid-input" class="short" placeholder="SID" value="0"><button id="pid-button">Locate</button></div><div class="painter"><div class="preview" id="spinda-preview">No PID loaded.</div><div><table><tbody id="pid-details"></tbody></table><div id="pid-labels"></div></div></div><pre id="pid-output">Enter a PID.</pre></div>
    <div class="panel wide"><h2>Pattern Automation</h2><div class="row"><select id="suggest-mode"><option value="funny">funny</option><option value="eye_cover">eye cover</option><option value="symmetry">symmetry</option><option value="balanced">balanced</option><option value="centered">centered</option><option value="heart">heart-ish</option><option value="cursed">cursed</option><option value="spread">spread</option><option value="clustered">clustered</option></select><input id="suggest-start" value="0x00000000"><input id="suggest-scan" class="short" value="8192"><input id="suggest-count" class="short" value="12"><button id="suggest-button">Suggest</button></div><div class="sub" id="suggest-status">No scan yet.</div><table><thead><tr><th>PID</th><th>Score</th><th>Offset</th><th>TSV</th><th>Rarity</th><th>Labels</th></tr></thead><tbody id="suggestions-table"><tr><td colspan="6" class="sub">No scan yet.</td></tr></tbody></table></div>
    <div class="panel full"><h2>Command Preview</h2><table><tbody id="commands-table"></tbody></table></div>
    <div class="panel full"><h2>Samples</h2><pre id="samples-output">No samples yet.</pre></div>
  </section></main>
  <script>
    const fmt = new Intl.NumberFormat(); const byId = id => document.getElementById(id);
    const esc = v => String(v ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
    const pills = values => (values || []).map(v => `<span class="pill">${esc(v)}</span>`).join("");
    const setText = (id, value) => byId(id).textContent = value;
    // Keep HTTP error handling centralized so every panel shows API errors cleanly.
    async function fetchJson(url) {
      const response = await fetch(url, {cache:"no-store"});
      const text = await response.text();
      let data = {};
      try {
        data = text ? JSON.parse(text) : {};
      } catch(e) {
        throw new Error(`${response.status} ${response.statusText}: non-JSON response`);
      }
      if (!response.ok && !data.error) {
        data.error = `${response.status} ${response.statusText}`;
      }
      return data;
    }
    function sampleText(samples) { const lines=[]; for (const [k, vals] of Object.entries(samples || {})) { if (!vals.length) continue; lines.push(k + ":"); for (const v of vals) lines.push("  " + v); } return lines.length ? lines.join("\n") : "No warning samples."; }
    function renderStatus(data) {
      const p=data.phase3||{}, t=data.tsv||{}, r=data.readiness||{};
      setText("updated", new Date((data.generated_at_unix||0)*1000).toLocaleTimeString());
      setText("phase3-count", `${fmt.format(p.complete_lanes||0)} / ${fmt.format(p.target_lanes||0)}`); byId("phase3-progress").value=p.progress_percent||0; setText("phase3-sub", `${Number(p.progress_percent||0).toFixed(2)}% complete. Last lane ${p.last_good_lane||"none"}.`);
      setText("spinda-count", fmt.format(p.completed_spindas||0)); setText("spinda-sub", `Target ${fmt.format(p.target_spindas||0)} records.`);
      setText("tsv-count", `${fmt.format(t.complete_saves||0)} / ${fmt.format(t.expected_saves||8192)}`); byId("tsv-progress").value=t.progress_percent||0; setText("tsv-sub", `${Number(t.progress_percent||0).toFixed(2)}% complete. Ledger done ${t.ledger_done ?? "n/a"}.`);
      const blockers=r.blocked_by||[]; setText("hatch-ready", r.ready_for_hatch_splitter ? "ready" : "blocked"); byId("hatch-ready").className = `metric ${r.ready_for_hatch_splitter ? "ok" : "warn"}`; setText("hatch-blockers", blockers.length ? blockers.join("; ") : "No blockers from read-only scan.");
      const health=[["Phase3 bad artifacts",p.bad_artifacts||0],["Bad ZIP names",p.bad_names||0],["Zero-size ZIPs",p.zero_size_zips||0],["Tiny ZIPs",p.tiny_zips||0],["Temp ZIPs",p.tmp_files||0],["Out-of-scope ZIPs",p.out_of_scope_zips||0],["TSV invalid files",t.invalid_files||0],["TSV mismatched files",t.mismatched_files||0],["TSV duplicate rows",t.duplicate_tsvs||0],["Ledger errors",t.ledger_errors ?? "n/a"]];
      byId("health-table").innerHTML = health.map(([k,v]) => `<tr><th>${esc(k)}</th><td>${esc(v)}</td></tr>`).join("");
      byId("commands-table").innerHTML = Object.entries(data.commands||{}).map(([k,v]) => `<tr><th>${esc(k)}</th><td><code>${esc(v)}</code></td></tr>`).join("");
      setText("samples-output", ["Phase 3", sampleText(p.samples||{}), "", "TSV", sampleText(t.samples||{})].join("\n"));
    }
    async function refresh() { try { renderStatus(await fetchJson("/api/status")); } catch(e) { setText("updated", "offline: " + e); } }
    async function locatePid() { const raw=byId("pid-input").value.trim(); if (!raw) return; const q=new URLSearchParams({tid:byId("tid-input").value.trim()||"0", sid:byId("sid-input").value.trim()||"0"}); try { const data=await fetchJson(`/api/pid/${encodeURIComponent(raw)}?${q}`); byId("pid-output").textContent=JSON.stringify(data,null,2); if (data.error) { setText("spinda-preview", data.error); return; } const painter=data.painter||{}, stats=painter.stats||{}; byId("spinda-preview").innerHTML=painter.svg||"No preview."; byId("pid-labels").innerHTML=pills(painter.labels||[]); byId("pid-details").innerHTML=[["PID",data.pid],["Entry",data.entry_name],["Lane ZIP",data.lane_zip],["PSV / TSV",`${data.expected_psv} / ${data.matching_tsv}`],["Nature",stats.nature],["Gender",stats.gender],["Ability slot",stats.ability_slot],["Rarity",stats.rarity],["Shiny",stats.is_shiny?"yes":"no"]].map(([k,v])=>`<tr><th>${esc(k)}</th><td>${esc(v)}</td></tr>`).join(""); } catch(e) { setText("spinda-preview", e); byId("pid-output").textContent=String(e); } }
    async function suggestPatterns() { const b=byId("suggest-button"); const q=new URLSearchParams({start:byId("suggest-start").value.trim()||"0x00000000", scan_limit:byId("suggest-scan").value.trim()||"8192", count:byId("suggest-count").value.trim()||"12", tid:byId("tid-input").value.trim()||"0", sid:byId("sid-input").value.trim()||"0"}); b.disabled=true; setText("suggest-status","Scanning..."); try { const data=await fetchJson(`/api/suggest/${encodeURIComponent(byId("suggest-mode").value)}?${q}`); if (data.error) { byId("suggestions-table").innerHTML=`<tr><td colspan="6" class="bad">${esc(data.error)}</td></tr>`; return; } setText("suggest-status", `${fmt.format(data.scan_limit)} PIDs scanned in ${Number(data.elapsed_seconds||0).toFixed(3)}s; ${fmt.format(data.pids_per_second||0)} PID/s.`); byId("suggestions-table").innerHTML=(data.results||[]).map(row=>`<tr><td><button class="link pid-link" data-pid="${esc(row.pid)}">${esc(row.pid)}</button></td><td>${esc(Number(row.score).toFixed(3))}</td><td>${esc(row.offset)}</td><td>${esc(row.matching_tsv)}</td><td>${esc(row.rarity)}${row.is_shiny ? " shiny" : ""}</td><td>${pills(row.labels)}</td></tr>`).join("") || `<tr><td colspan="6" class="sub">No matches.</td></tr>`; } catch(e) { byId("suggestions-table").innerHTML=`<tr><td colspan="6" class="bad">${esc(e)}</td></tr>`; } finally { b.disabled=false; } }
    byId("pid-button").addEventListener("click", locatePid); byId("pid-input").addEventListener("keydown", e => { if (e.key === "Enter") locatePid(); }); byId("suggest-button").addEventListener("click", suggestPatterns); byId("suggestions-table").addEventListener("click", e => { const t=e.target.closest(".pid-link"); if (!t) return; byId("pid-input").value=t.dataset.pid||""; locatePid(); });
    refresh(); setInterval(refresh, 5000);
  </script>
</body>
</html>
)HTML";

std::string url_decode(const std::string& text, bool plus_as_space) {
    std::string out;
    out.reserve(text.size());
    for (std::size_t index = 0; index < text.size(); ++index) {
        if (text[index] == '%') {
            if (index + 2 >= text.size()) {
                throw std::runtime_error("URL percent escape is incomplete");
            }
            const int high = hex_digit_value(text[index + 1]);
            const int low = hex_digit_value(text[index + 2]);
            if (high < 0 || low < 0) {
                throw std::runtime_error("URL percent escape is invalid");
            }
            out.push_back(static_cast<char>((high << 4) | low));
            index += 2;
            continue;
        }
        out.push_back(plus_as_space && text[index] == '+' ? ' ' : text[index]);
    }
    return out;
}

std::map<std::string, std::string> parse_query(const std::string& query) {
    std::map<std::string, std::string> result;
    std::size_t start = 0;
    while (start <= query.size()) {
        const std::size_t amp = query.find('&', start);
        const std::string part = query.substr(start, amp == std::string::npos ? std::string::npos : amp - start);
        if (!part.empty()) {
            const std::size_t equals = part.find('=');
            const std::string key = url_decode(part.substr(0, equals), true);
            const std::string value = equals == std::string::npos ? "" : url_decode(part.substr(equals + 1), true);
            // Flask/Werkzeug returns the first value for request.args.get().
            // Preserve that contract when duplicate query keys are present.
            result.emplace(key, value);
        }
        if (amp == std::string::npos) break;
        start = amp + 1;
    }
    return result;
}

struct HttpResponse {
    int status = 200;
    std::string status_text = "OK";
    std::string content_type = "application/json; charset=utf-8";
    std::string body;
};

HttpResponse route_request(const Config& config, const std::string& raw_target) {
    try {
        const std::size_t question = raw_target.find('?');
        const std::string raw_path = question == std::string::npos ? raw_target : raw_target.substr(0, question);
        const std::string query_text = question == std::string::npos ? "" : raw_target.substr(question + 1);
        const std::string path = url_decode(raw_path, false);
        const std::map<std::string, std::string> query = parse_query(query_text);

        if (path == "/") {
            return {200, "OK", "text/html; charset=utf-8", HTML};
        }
        if (path == "/favicon.ico") {
            // Avoid noisy browser favicon probes showing as API errors in logs/tests.
            return {204, "No Content", "text/plain; charset=utf-8", ""};
        }
        if (path == "/api/status") {
            return {200, "OK", "application/json; charset=utf-8", snapshot_json(config)};
        }
        if (path == "/api/commands") {
            return {200, "OK", "application/json; charset=utf-8", commands_json(config)};
        }
        if (starts_with(path, "/api/pid/")) {
            const std::string pid_text = path.substr(std::strlen("/api/pid/"));
            const int tid = static_cast<int>(parse_uint_text(query_int_or_default(query, "tid", "0"), 0, 0xFFFF, "tid"));
            const int sid = static_cast<int>(parse_uint_text(query_int_or_default(query, "sid", "0"), 0, 0xFFFF, "sid"));
            return {200, "OK", "application/json; charset=utf-8", pid_report_json(pid_text, config, tid, sid)};
        }
        if (starts_with(path, "/api/suggest/")) {
            const std::string mode = path.substr(std::strlen("/api/suggest/"));
            const std::uint32_t start_pid = static_cast<std::uint32_t>(
                parse_uint_text(query_int_or_default(query, "start", "0"), 0, 0xFFFFFFFFULL, "start"));
            const int scan_limit = static_cast<int>(
                parse_uint_text(query_int_or_default(query, "scan_limit", std::to_string(DEFAULT_SUGGESTION_SCAN)), 1, MAX_SUGGESTION_SCAN, "scan_limit"));
            const int count = static_cast<int>(
                parse_uint_text(query_int_or_default(query, "count", std::to_string(DEFAULT_SUGGESTION_COUNT)), 1, MAX_SUGGESTION_COUNT, "count"));
            const int tid = static_cast<int>(parse_uint_text(query_int_or_default(query, "tid", "0"), 0, 0xFFFF, "tid"));
            const int sid = static_cast<int>(parse_uint_text(query_int_or_default(query, "sid", "0"), 0, 0xFFFF, "sid"));
            return {200, "OK", "application/json; charset=utf-8", suggestion_json(mode, config, start_pid, scan_limit, count, tid, sid)};
        }
        return {404, "Not Found", "application/json; charset=utf-8", "{\"error\":\"not found\"}"};
    } catch (const std::exception& error) {
        return {400, "Bad Request", "application/json; charset=utf-8", std::string("{\"error\":") + json_string(error.what()) + "}"};
    }
}

void close_socket(SocketHandle socket) {
#ifdef _WIN32
    closesocket(socket);
#else
    close(socket);
#endif
}

void handle_client(SocketHandle client, Config config) {
    std::string request;
    std::array<char, 4096> buffer{};
    while (request.find("\r\n\r\n") == std::string::npos && request.size() < 16384) {
        const int received = recv(client, buffer.data(), static_cast<int>(buffer.size()), 0);
        if (received <= 0) {
            close_socket(client);
            return;
        }
        request.append(buffer.data(), static_cast<std::size_t>(received));
    }

    std::istringstream input(request);
    std::string method;
    std::string target;
    std::string version;
    const bool parsed_request_line = static_cast<bool>(input >> method >> target >> version);
    HttpResponse response;
    bool include_body = true;
    if (!parsed_request_line || target.empty()) {
        response = {400, "Bad Request", "application/json; charset=utf-8", "{\"error\":\"bad request line\"}"};
    } else if (method == "HEAD") {
        include_body = false;
        response = route_request(config, target);
    } else if (method != "GET") {
        response = {405, "Method Not Allowed", "application/json; charset=utf-8", "{\"error\":\"GET or HEAD only\"}"};
    } else {
        response = route_request(config, target);
    }

    std::ostringstream output;
    output << "HTTP/1.1 " << response.status << " " << response.status_text << "\r\n"
           << "Content-Type: " << response.content_type << "\r\n"
           << "Content-Length: " << response.body.size() << "\r\n"
           << "Cache-Control: no-store\r\n"
           << "Connection: close\r\n\r\n"
           << (include_body ? response.body : "");
    const std::string bytes = output.str();
    const char* cursor = bytes.data();
    std::size_t remaining = bytes.size();
    while (remaining > 0) {
        const int sent = send(client, cursor, static_cast<int>(std::min<std::size_t>(remaining, 16384)), 0);
        if (sent <= 0) {
            break;
        }
        cursor += sent;
        remaining -= static_cast<std::size_t>(sent);
    }
    close_socket(client);
}

std::vector<std::string> workbench_urls(const std::string& host, int port) {
    if (host == "0.0.0.0" || host.empty()) {
        return {"http://127.0.0.1:" + std::to_string(port) + "/", "http://<this-pc-ip>:" + std::to_string(port) + "/"};
    }
    if (host == "::") {
        return {"http://[::1]:" + std::to_string(port) + "/", "http://<this-pc-ip>:" + std::to_string(port) + "/"};
    }
    if (host.find(':') != std::string::npos && host.front() != '[') {
        return {"http://[" + host + "]:" + std::to_string(port) + "/"};
    }
    return {"http://" + host + ":" + std::to_string(port) + "/"};
}

int run_server(Config config) {
#ifdef _WIN32
    WSADATA data{};
    const int startup = WSAStartup(MAKEWORD(2, 2), &data);
    if (startup != 0) {
        std::cerr << "WSAStartup failed: " << startup << "\n";
        return 1;
    }
#endif
    const std::string port_text = std::to_string(config.port);
    addrinfo hints{};
    hints.ai_family = AF_UNSPEC;
    hints.ai_socktype = SOCK_STREAM;
    hints.ai_protocol = IPPROTO_TCP;
    hints.ai_flags = AI_PASSIVE;

    addrinfo* addresses = nullptr;
    const int lookup = getaddrinfo(config.host.c_str(), port_text.c_str(), &hints, &addresses);
    if (lookup != 0) {
        std::cerr << "getaddrinfo failed for " << config.host << ":" << config.port << "\n";
#ifdef _WIN32
        WSACleanup();
#endif
        return 1;
    }

    SocketHandle listener = INVALID_SOCKET_HANDLE;
    for (addrinfo* address = addresses; address != nullptr; address = address->ai_next) {
        listener = socket(address->ai_family, address->ai_socktype, address->ai_protocol);
        if (listener == INVALID_SOCKET_HANDLE) {
            continue;
        }
        int reuse = 1;
        setsockopt(listener, SOL_SOCKET, SO_REUSEADDR, reinterpret_cast<const char*>(&reuse), sizeof(reuse));
        if (bind(listener, address->ai_addr, static_cast<int>(address->ai_addrlen)) == 0
            && listen(listener, SOMAXCONN) == 0) {
            break;
        }
        close_socket(listener);
        listener = INVALID_SOCKET_HANDLE;
    }
    freeaddrinfo(addresses);

    if (listener == INVALID_SOCKET_HANDLE) {
        std::cerr << "Cannot bind " << config.host << ":" << config.port << "\n";
#ifdef _WIN32
        WSACleanup();
#endif
        return 1;
    }

    const std::vector<std::string> urls = workbench_urls(config.host, config.port);
    config.display_url = urls.empty() ? "" : urls.front();
    std::cout << "Spinda Workbench Native URLs:\n";
    for (const std::string& url : urls) {
        std::cout << "  " << url << "\n";
    }
    std::cout << "Read-only mode. No workers launched.\n";

    for (;;) {
        sockaddr_storage client_addr{};
        socklen_t client_len = sizeof(client_addr);
        SocketHandle client = accept(listener, reinterpret_cast<sockaddr*>(&client_addr), &client_len);
        if (client == INVALID_SOCKET_HANDLE) {
            continue;
        }
        std::thread(handle_client, client, config).detach();
    }
}

void write_file(const fs::path& path, const std::string& bytes) {
    std::ofstream output(path, std::ios::binary);
    if (!output) {
        throw std::runtime_error("cannot write " + path_string(path));
    }
    output << bytes;
}

int run_self_test() {
    const auto now = std::chrono::steady_clock::now().time_since_epoch().count();
    const fs::path root = fs::temp_directory_path() / ("spinda_workbench_native_self_test_" + std::to_string(now));
    const fs::path phase3 = root / "Phase3SpindaBlocks";
    const fs::path tsv = root / "TSVs";
    fs::create_directories(phase3);
    fs::create_directories(tsv);
    write_file(phase3 / "0x0001.spinda80.zip", std::string(2048, 'z'));
    write_file(phase3 / "0x0002.spinda80.zip", std::string(2048, 'z'));
    write_file(phase3 / "0x0003.spinda80.zip", "");
    write_file(phase3 / "0x0004.spinda80.zip.pid123.tmp", "tmp");
    write_file(phase3 / "bad.spinda80.zip", std::string(2048, 'z'));
    write_file(tsv / "TSV-0001-sid-00008.sav", "a");
    write_file(tsv / "TSV-0001-sid-00009.sav", "b");
    write_file(tsv / "TSV-0002-sid-00099.sav", "c");
    write_file(tsv / "old-name.sav", "d");
    write_file(tsv / "_sid_shiny_value_ledger_tid_0x0000.json",
        R"({"entries":[{"done":true,"shiny_value":"0x0001"},{"done":false,"shiny_value":"0x0002","error":"retry"}]})");

    Config config;
    config.root = root;
    config.phase3_dir = phase3;
    config.tsv_dir = tsv;
    config.hatch_output_dir = root / "HatchedSpindaZips";
    config.seven_zip_output_dir = root / "Spinda7zArchives";
    config.target_phase3_lanes = 4;
    config.sample_limit = 16;
    config.display_url = "self-test";

    const Phase3Summary phase = scan_phase3(config);
    const TsvSummary saves = scan_tsv(config);
    const std::uint32_t pid = parse_pid_text("0x12345678.pk3");
    const auto spots = spinda_spots(pid);
    const auto suggest = suggestion_json("centered", config, 0, 64, 4, 0, 0);
    const auto snapshot = snapshot_json(config);
    fs::remove_all(root);

    if (phase.complete_lanes != 2 || phase.missing_lanes != 2 || phase.zero_size_zips != 1
        || phase.tmp_files != 1 || phase.bad_names != 1 || phase.bad_artifacts != 3) {
        std::cerr << "phase3 self-test failed\n";
        return 1;
    }
    if (saves.complete_saves != 1 || saves.duplicate_tsvs != 1 || saves.duplicate_files != 1
        || saves.mismatched_files != 1 || saves.invalid_files != 1 || saves.ledger_done.value_or(-1) != 1
        || saves.ledger_errors.value_or(-1) != 1) {
        std::cerr << "tsv self-test failed\n";
        return 1;
    }
    if (spots.size() != 4 || spots[0].offset_x != 8 || spots[0].offset_y != 7 || spots[3].x != 30 || spots[3].y != 33) {
        std::cerr << "painter self-test failed\n";
        return 1;
    }
    if (suggest.find("\"results\"") == std::string::npos || snapshot.find("\"runtime\":\"native-cpp\"") == std::string::npos) {
        std::cerr << "json self-test failed\n";
        return 1;
    }
    std::cout << "spinda_workbench_native self-test passed\n";
    return 0;
}

void print_help() {
    std::cout <<
        "Spinda Workbench Native\n"
        "Usage:\n"
        "  spinda_workbench_native.exe [options]\n"
        "  spinda_workbench_native.exe --status-json [options]\n"
        "  spinda_workbench_native.exe --pid 0x12345678 [--tid N --sid N] [options]\n"
        "  spinda_workbench_native.exe --suggest funny [--start 0 --scan-limit 8192 --count 12] [options]\n"
        "\n"
        "Options:\n"
        "  --root PATH                 Workspace root. Defaults to detected <repo-root>-style root.\n"
        "  --phase3-dir PATH           Phase3SpindaBlocks directory.\n"
        "  --tsv-dir PATH              TSV save-bank directory.\n"
        "  --hatch-output-dir PATH     Hatch output directory for command previews.\n"
        "  --seven-zip-output-dir PATH 7z output directory for command previews.\n"
        "  --target-phase3-lanes N     0..65536, default 65534.\n"
        "  --sample-limit N            Warning sample cap, default 16.\n"
        "  --host HOST                 Server bind host, default 0.0.0.0.\n"
        "  --port PORT                 Server port, default 8780.\n"
        "  --self-test                 Run built-in native checks.\n";
}

Cli parse_args(int argc, char** argv) {
    Cli cli;
    auto require_value = [&](int& index, const std::string& option) -> std::string {
        if (index + 1 >= argc) {
            throw std::runtime_error(option + " requires a value");
        }
        return argv[++index];
    };

    for (int index = 1; index < argc; ++index) {
        const std::string arg = argv[index];
        if (arg == "--help" || arg == "-h") {
            cli.mode = Mode::Help;
        } else if (arg == "--self-test") {
            cli.mode = Mode::SelfTest;
        } else if (arg == "--status-json") {
            cli.mode = Mode::StatusJson;
        } else if (arg == "--commands-json") {
            cli.mode = Mode::CommandsJson;
        } else if (arg == "--pid") {
            cli.mode = Mode::PidJson;
            cli.pid_text = require_value(index, arg);
        } else if (arg == "--suggest") {
            cli.mode = Mode::SuggestJson;
            cli.suggest_mode = require_value(index, arg);
        } else if (arg == "--root") {
            cli.root = fs::path(require_value(index, arg));
        } else if (arg == "--phase3-dir") {
            cli.phase3_dir = fs::path(require_value(index, arg));
        } else if (arg == "--tsv-dir") {
            cli.tsv_dir = fs::path(require_value(index, arg));
        } else if (arg == "--hatch-output-dir") {
            cli.hatch_output_dir = fs::path(require_value(index, arg));
        } else if (arg == "--seven-zip-output-dir") {
            cli.seven_zip_output_dir = fs::path(require_value(index, arg));
        } else if (arg == "--host") {
            cli.host = require_value(index, arg);
        } else if (arg == "--port") {
            cli.port = static_cast<int>(parse_uint_text(require_value(index, arg), 1, 65535, "port"));
        } else if (arg == "--target-phase3-lanes") {
            cli.target_phase3_lanes = static_cast<int>(parse_uint_text(require_value(index, arg), 0, 0x10000, "target-phase3-lanes"));
        } else if (arg == "--sample-limit") {
            cli.sample_limit = static_cast<int>(parse_uint_text(require_value(index, arg), 0, 10000, "sample-limit"));
        } else if (arg == "--start" || arg == "--start-pid") {
            cli.start_pid = static_cast<std::uint32_t>(parse_uint_text(require_value(index, arg), 0, 0xFFFFFFFFULL, "start"));
        } else if (arg == "--scan-limit") {
            cli.scan_limit = static_cast<int>(parse_uint_text(require_value(index, arg), 1, MAX_SUGGESTION_SCAN, "scan-limit"));
        } else if (arg == "--count") {
            cli.count = static_cast<int>(parse_uint_text(require_value(index, arg), 1, MAX_SUGGESTION_COUNT, "count"));
        } else if (arg == "--tid") {
            cli.tid = static_cast<int>(parse_uint_text(require_value(index, arg), 0, 0xFFFF, "tid"));
        } else if (arg == "--sid") {
            cli.sid = static_cast<int>(parse_uint_text(require_value(index, arg), 0, 0xFFFF, "sid"));
        } else {
            throw std::runtime_error("unknown option: " + arg);
        }
    }
    return cli;
}

Config build_config(const Cli& cli, const fs::path& executable) {
    Config config;
    config.executable = executable;
    config.root = cli.root ? lexical_absolute(*cli.root) : find_default_root(executable);
    config.phase3_dir = cli.phase3_dir ? lexical_absolute(*cli.phase3_dir) : (config.root / "Phase3SpindaBlocks");
    config.tsv_dir = cli.tsv_dir ? lexical_absolute(*cli.tsv_dir) : (config.root / "TSVs");
    config.hatch_output_dir = cli.hatch_output_dir ? lexical_absolute(*cli.hatch_output_dir) : (config.root / "HatchedSpindaZips");
    config.seven_zip_output_dir = cli.seven_zip_output_dir ? lexical_absolute(*cli.seven_zip_output_dir) : (config.root / "Spinda7zArchives");
    config.host = cli.host;
    config.port = cli.port;
    config.target_phase3_lanes = cli.target_phase3_lanes;
    config.sample_limit = cli.sample_limit;
    const auto urls = workbench_urls(config.host, config.port);
    config.display_url = urls.empty() ? "" : urls.front();
    return config;
}

} // namespace

int main(int argc, char** argv) {
    try {
        const Cli cli = parse_args(argc, argv);
        if (cli.mode == Mode::Help) {
            print_help();
            return 0;
        }
        if (cli.mode == Mode::SelfTest) {
            return run_self_test();
        }
        const fs::path executable = executable_path_from_argv(argc > 0 ? argv[0] : nullptr);
        const Config config = build_config(cli, executable);
        switch (cli.mode) {
        case Mode::StatusJson:
            std::cout << snapshot_json(config) << "\n";
            return 0;
        case Mode::CommandsJson:
            std::cout << commands_json(config) << "\n";
            return 0;
        case Mode::PidJson:
            std::cout << pid_report_json(cli.pid_text, config, cli.tid, cli.sid) << "\n";
            return 0;
        case Mode::SuggestJson:
            std::cout << suggestion_json(cli.suggest_mode, config, cli.start_pid, cli.scan_limit, cli.count, cli.tid, cli.sid) << "\n";
            return 0;
        case Mode::Server:
            return run_server(config);
        case Mode::Help:
        case Mode::SelfTest:
            return 0;
        }
    } catch (const std::exception& error) {
        std::cerr << "error: " << error.what() << "\n";
        return 2;
    }
    return 0;
}
