// SPDX-License-Identifier: MPL-2.0
// COLLECTOR_TOGAMI_SOURCE_CREDIT_2026-05-11
// Project credit: Collector Togami is the person behind the Spinda/SPC3 project as a whole and is credited as project originator, coordinator, and driving force.
//
// Native SPC3 compressor GUI.
//
// Credit: Shawrkie helped with SPC3 compressor/decompressor work and
// contributed compute for corpus processing and verification. Keep this credit
// with source and binary packages that include this GUI.
//
// This is a small Win32 launcher for the C++ SPC3 command-line executable. It
// keeps the shipped verifier path disk-light by default and now gives operators
// a native report summary/compare view for pack, verify, inspect, unpack, and
// consolidate reports.

#ifndef NOMINMAX
#define NOMINMAX
#endif

#include <windows.h>
#include <commdlg.h>
#include <shlobj.h>

#include <algorithm>
#include <array>
#include <atomic>
#include <cctype>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <mutex>
#include <optional>
#include <sstream>
#include <stdexcept>
#include <string>
#include <thread>
#include <utility>
#include <vector>

namespace fs = std::filesystem;

namespace {

constexpr UINT kAppendOutput = WM_APP + 1;
constexpr UINT kRunFinished = WM_APP + 2;
constexpr int kSelectorItemHeight = 24;
constexpr int kMinWindowWidth = 760;
constexpr int kMinWindowHeight = 760;
constexpr int kComboNoValue = 0x7FFFFFFE;
const char* const kDarkSelectorClass = "SPC3DarkSelector";
const char* const kDarkSelectorPopupClass = "SPC3DarkSelectorPopup";

LRESULT CALLBACK dark_selector_proc(HWND hwnd, UINT message, WPARAM wparam, LPARAM lparam);
LRESULT CALLBACK dark_selector_popup_proc(HWND hwnd, UINT message, WPARAM wparam, LPARAM lparam);

enum ControlId {
    IdModeVerify = 100,
    IdModePack,
    IdModeConsolidate,
    IdModeInspect,
    IdModeUnpack,
    IdExe = 110,
    IdInput,
    IdOutput,
    IdRoot,
    IdConsolidateRoot,
    IdPredictor,
    IdUnpackDir,
    IdReport,
    IdCompareReport,
    IdLimit,
    IdLevel,
    IdProfile,
    IdTyped,
    IdGpu,
    IdInternalOnly,
    IdExternalPredictor,
    IdNoEntropyProbe,
    IdLaneAll,
    IdLaneOne,
    IdLaneRange,
    IdLaneValue,
    IdLaneFrom,
    IdLaneTo,
    IdPk3StateEgg,
    IdPk3StateHatchedShiny,
    IdPk3StateHatchedNotShiny,
    IdTrainerIndex,
    IdRun,
    IdCancel,
    IdSummary,
    IdConsole,
    IdBrowseExe = 140,
    IdBrowseInput,
    IdBrowseOutput,
    IdBrowseRoot,
    IdBrowseConsolidateRoot,
    IdBrowsePredictor,
    IdBrowseUnpackDir,
    IdBrowseReport,
    IdBrowseCompareReport,
    IdBrowseTrainerIndex,
    IdExtraSettings = 160,
    IdSetNickname,
    IdSetOtName,
    IdSetMoves,
    IdSetPp,
    IdSetPpUps,
    IdSetEvs,
    IdSetIvs,
    IdSetContest,
    IdSetHeldItem,
    IdSetExperience,
    IdSetFriendship,
    IdSetPokerus,
    IdSetMetLocation,
    IdSetMetLevel,
    IdSetOriginGame,
    IdSetBall,
    IdSetOtGender,
    IdSetLanguage,
    IdSetAbilityNumber,
    IdMove1 = 190,
    IdMove2,
    IdMove3,
    IdMove4,
    IdPpUp1,
    IdPpUp2,
    IdPpUp3,
    IdPpUp4,
    IdMovePp1,
    IdMovePp2,
    IdMovePp3,
    IdMovePp4,
    IdEvHp = 210,
    IdEvAtk,
    IdEvDef,
    IdEvSpa,
    IdEvSpd,
    IdEvSpe,
    IdIvHp,
    IdIvAtk,
    IdIvDef,
    IdIvSpa,
    IdIvSpd,
    IdIvSpe,
    IdContestCool,
    IdContestBeauty,
    IdContestCute,
    IdContestSmart,
    IdContestTough,
    IdContestFeel,
    IdPokerusStrain,
    IdPokerusDays,
};

struct AppState {
    HWND window = nullptr;
    HWND mode_label = nullptr;
    HWND mode_verify = nullptr;
    HWND mode_pack = nullptr;
    HWND mode_consolidate = nullptr;
    HWND mode_inspect = nullptr;
    HWND mode_unpack = nullptr;
    HWND exe_label = nullptr;
    HWND exe = nullptr;
    HWND input_label = nullptr;
    HWND input = nullptr;
    HWND output_label = nullptr;
    HWND output = nullptr;
    HWND root_label = nullptr;
    HWND root = nullptr;
    HWND consolidate_root_label = nullptr;
    HWND consolidate_root = nullptr;
    HWND predictor_label = nullptr;
    HWND predictor = nullptr;
    HWND unpack_dir_label = nullptr;
    HWND unpack_dir = nullptr;
    HWND report_label = nullptr;
    HWND report = nullptr;
    HWND compare_report_label = nullptr;
    HWND compare_report = nullptr;
    HWND limit_label = nullptr;
    HWND limit = nullptr;
    HWND level_label = nullptr;
    HWND level = nullptr;
    HWND profile_label = nullptr;
    HWND profile = nullptr;
    HWND typed = nullptr;
    HWND gpu = nullptr;
    HWND internal_only = nullptr;
    HWND external_predictor = nullptr;
    HWND no_entropy_probe = nullptr;
    HWND lane_select_label = nullptr;
    HWND lane_all = nullptr;
    HWND lane_one = nullptr;
    HWND lane_range = nullptr;
    HWND lane_value_label = nullptr;
    HWND lane_value = nullptr;
    HWND lane_from_label = nullptr;
    HWND lane_from = nullptr;
    HWND lane_to_label = nullptr;
    HWND lane_to = nullptr;
    HWND pk3_state_label = nullptr;
    HWND pk3_state_egg = nullptr;
    HWND pk3_state_hatched_shiny = nullptr;
    HWND pk3_state_hatched_not_shiny = nullptr;
    HWND trainer_index_label = nullptr;
    HWND trainer_index = nullptr;
    HWND extra_settings = nullptr;
    HWND set_nickname_label = nullptr;
    HWND set_nickname = nullptr;
    HWND set_ot_name_label = nullptr;
    HWND set_ot_name = nullptr;
    std::array<HWND, 4> move_label{};
    std::array<HWND, 4> move_combo{};
    std::array<HWND, 4> pp_up_label{};
    std::array<HWND, 4> pp_up_combo{};
    std::array<HWND, 4> pp_label{};
    std::array<HWND, 4> pp_value{};
    HWND set_evs_label = nullptr;
    std::array<HWND, 6> ev_combo{};
    HWND set_ivs_label = nullptr;
    std::array<HWND, 6> iv_combo{};
    HWND set_contest_label = nullptr;
    std::array<HWND, 6> contest_combo{};
    HWND set_held_item_label = nullptr;
    HWND set_held_item = nullptr;
    HWND set_experience_label = nullptr;
    HWND set_experience = nullptr;
    HWND set_friendship_label = nullptr;
    HWND set_friendship = nullptr;
    HWND set_pokerus_label = nullptr;
    HWND set_pokerus_strain_label = nullptr;
    HWND set_pokerus_strain = nullptr;
    HWND set_pokerus_days_label = nullptr;
    HWND set_pokerus_days = nullptr;
    HWND set_met_location_label = nullptr;
    HWND set_met_location = nullptr;
    HWND set_met_level_label = nullptr;
    HWND set_met_level = nullptr;
    HWND set_origin_game_label = nullptr;
    HWND set_origin_game = nullptr;
    HWND set_ball_label = nullptr;
    HWND set_ball = nullptr;
    HWND set_ot_gender_label = nullptr;
    HWND set_ot_gender = nullptr;
    HWND set_language_label = nullptr;
    HWND set_language = nullptr;
    HWND set_ability_number_label = nullptr;
    HWND set_ability_number = nullptr;
    HWND run = nullptr;
    HWND cancel = nullptr;
    HWND summary = nullptr;
    HWND console = nullptr;
    HWND browse_exe = nullptr;
    HWND browse_input = nullptr;
    HWND browse_output = nullptr;
    HWND browse_root = nullptr;
    HWND browse_consolidate_root = nullptr;
    HWND browse_predictor = nullptr;
    HWND browse_unpack_dir = nullptr;
    HWND browse_report = nullptr;
    HWND browse_compare_report = nullptr;
    HWND browse_trainer_index = nullptr;
    PROCESS_INFORMATION worker_process{};
    HANDLE worker_stdin = nullptr;
    HANDLE worker_stdout = nullptr;
    std::string worker_exe_path;
    std::mutex process_mutex;
    int selected_mode_id = IdModeVerify;
    bool typed_checked = true;
    bool gpu_checked = true;
    bool internal_only_checked = true;
    bool external_predictor_checked = false;
    bool no_entropy_probe_checked = true;
    int selected_lane_select_id = IdLaneAll;
    int selected_pk3_state_id = IdPk3StateEgg;
    bool extra_settings_open = false;
    // UI run state and worker command state are separate so early Cancel clicks
    // can abort startup while idle worker replacement can still shut down cleanly.
    bool process_active = false;
    bool worker_active = false;
    bool worker_command_active = false;
    bool cancel_requested = false;
};

struct RunRequest {
    std::vector<std::string> args;
    std::string report_path;
    std::string compare_report_path;
};

AppState g_app;
std::atomic_bool g_window_alive{false};

struct DarkSelectorState {
    HWND hwnd = nullptr;
    HWND popup = nullptr;
    int control_id = 0;
    std::vector<std::string> items;
    int selected = 0;
    int hover = -1;
};

DarkSelectorState g_level_selector;
DarkSelectorState g_profile_selector;

struct MoveChoice {
    uint16_t id = 0;
    uint8_t pp = 0;
    std::string name;
    std::string label;
};

struct ValueChoice {
    int value = 0;
    std::string name;
    std::string label;
};

struct LocationChoice {
    uint8_t value = 0;
    bool ruby_sapphire = false;
    bool emerald = false;
    bool fire_red_leaf_green = false;
    std::string name;
    std::string label;
};

std::vector<MoveChoice> g_move_choices;
std::vector<ValueChoice> g_held_item_choices;
std::vector<LocationChoice> g_location_choices;

void hide_selector_popup(DarkSelectorState* selector);

const COLORREF kWindowBg = RGB(18, 20, 24);
const COLORREF kEditBg = RGB(27, 31, 36);
const COLORREF kConsoleBg = RGB(12, 14, 18);
const COLORREF kButtonBg = RGB(42, 48, 57);
const COLORREF kButtonPressedBg = RGB(52, 60, 72);
const COLORREF kSelectorHoverBg = RGB(48, 56, 68);
const COLORREF kSelectorSelectedBg = RGB(37, 68, 98);
const COLORREF kBorder = RGB(82, 92, 110);
const COLORREF kAccent = RGB(92, 176, 255);
const COLORREF kText = RGB(230, 235, 242);
const COLORREF kMutedText = RGB(162, 171, 184);
const COLORREF kDisabledText = RGB(103, 112, 126);

HBRUSH g_window_brush = nullptr;
HBRUSH g_edit_brush = nullptr;
HBRUSH g_console_brush = nullptr;
HBRUSH g_button_brush = nullptr;
HBRUSH g_button_pressed_brush = nullptr;
HBRUSH g_selector_hover_brush = nullptr;
HBRUSH g_selector_selected_brush = nullptr;
HBRUSH g_border_brush = nullptr;

std::string last_error_message(const char* operation) {
    const DWORD error = GetLastError();
    std::ostringstream out;
    out << operation << " failed";
    if (error != 0) {
        out << " (Win32 error " << error << ")";
    }
    return out.str();
}

template <typename Fn>
Fn resolve_proc(HMODULE module, const char* name) {
    FARPROC proc = GetProcAddress(module, name);
    Fn fn = nullptr;
    static_assert(sizeof(fn) == sizeof(proc));
    std::memcpy(&fn, &proc, sizeof(fn));
    return fn;
}

void ensure_theme_resources() {
    if (g_window_brush) {
        return;
    }
    g_window_brush = CreateSolidBrush(kWindowBg);
    g_edit_brush = CreateSolidBrush(kEditBg);
    g_console_brush = CreateSolidBrush(kConsoleBg);
    g_button_brush = CreateSolidBrush(kButtonBg);
    g_button_pressed_brush = CreateSolidBrush(kButtonPressedBg);
    g_selector_hover_brush = CreateSolidBrush(kSelectorHoverBg);
    g_selector_selected_brush = CreateSolidBrush(kSelectorSelectedBg);
    g_border_brush = CreateSolidBrush(kBorder);
}

void release_theme_resources() {
    for (HBRUSH brush : {
             g_window_brush,
             g_edit_brush,
             g_console_brush,
             g_button_brush,
             g_button_pressed_brush,
             g_selector_hover_brush,
             g_selector_selected_brush,
             g_border_brush,
         }) {
        if (brush) {
            DeleteObject(brush);
        }
    }
    g_window_brush = nullptr;
    g_edit_brush = nullptr;
    g_console_brush = nullptr;
    g_button_brush = nullptr;
    g_button_pressed_brush = nullptr;
    g_selector_hover_brush = nullptr;
    g_selector_selected_brush = nullptr;
    g_border_brush = nullptr;
}

void enable_dark_title_bar(HWND hwnd) {
    HMODULE dwm = LoadLibraryA("dwmapi.dll");
    if (!dwm) {
        return;
    }
    using DwmSetWindowAttributeFn = HRESULT(WINAPI*)(HWND, DWORD, LPCVOID, DWORD);
    auto set_window_attribute = resolve_proc<DwmSetWindowAttributeFn>(dwm, "DwmSetWindowAttribute");
    if (set_window_attribute) {
        BOOL enabled = TRUE;
        set_window_attribute(hwnd, 20, &enabled, sizeof(enabled));
        set_window_attribute(hwnd, 19, &enabled, sizeof(enabled));
    }
    FreeLibrary(dwm);
}

void apply_dark_control_theme(HWND hwnd) {
    using SetWindowThemeFn = HRESULT(WINAPI*)(HWND, LPCWSTR, LPCWSTR);
    static SetWindowThemeFn set_window_theme = []() -> SetWindowThemeFn {
        HMODULE theme = LoadLibraryA("uxtheme.dll");
        if (!theme) {
            return nullptr;
        }
        return resolve_proc<SetWindowThemeFn>(theme, "SetWindowTheme");
    }();
    if (set_window_theme) {
        set_window_theme(hwnd, L"DarkMode_Explorer", nullptr);
    }
}

std::string get_window_text(HWND hwnd) {
    const int length = GetWindowTextLengthA(hwnd);
    if (length <= 0) {
        return {};
    }
    std::vector<char> buffer(static_cast<size_t>(length) + 1, '\0');
    GetWindowTextA(hwnd, buffer.data(), static_cast<int>(buffer.size()));
    return std::string(buffer.data());
}

std::string trim_copy(const std::string& value) {
    size_t begin = 0;
    while (begin < value.size() && std::isspace(static_cast<unsigned char>(value[begin]))) {
        ++begin;
    }
    size_t end = value.size();
    while (end > begin && std::isspace(static_cast<unsigned char>(value[end - 1]))) {
        --end;
    }
    return value.substr(begin, end - begin);
}

bool read_text_file(const std::string& path, std::string& text, std::string& error) {
    std::ifstream in(path, std::ios::binary);
    if (!in) {
        error = "could not open " + path;
        return false;
    }
    std::ostringstream buffer;
    buffer << in.rdbuf();
    text = buffer.str();
    if (!in.good() && !in.eof()) {
        error = "could not read " + path;
        return false;
    }
    return true;
}

size_t skip_json_ws(const std::string& text, size_t pos) {
    while (pos < text.size() && std::isspace(static_cast<unsigned char>(text[pos]))) {
        ++pos;
    }
    return pos;
}

bool parse_json_string_at(const std::string& text, size_t pos, std::string& value, size_t& end_pos) {
    if (pos >= text.size() || text[pos] != '"') {
        return false;
    }
    value.clear();
    ++pos;
    while (pos < text.size()) {
        const char ch = text[pos++];
        if (ch == '"') {
            end_pos = pos;
            return true;
        }
        if (ch != '\\') {
            value.push_back(ch);
            continue;
        }
        if (pos >= text.size()) {
            return false;
        }
        const char escaped = text[pos++];
        switch (escaped) {
        case '"':
        case '\\':
        case '/':
            value.push_back(escaped);
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
        case 'u':
            value.push_back('?');
            pos = std::min(text.size(), pos + 4);
            break;
        default:
            value.push_back(escaped);
            break;
        }
    }
    return false;
}

size_t json_matching_bracket(const std::string& text, size_t pos) {
    if (pos >= text.size() || (text[pos] != '{' && text[pos] != '[')) {
        return std::string::npos;
    }
    const char open = text[pos];
    const char close = open == '{' ? '}' : ']';
    int depth = 0;
    bool in_string = false;
    bool escaped = false;
    for (size_t i = pos; i < text.size(); ++i) {
        const char ch = text[i];
        if (in_string) {
            if (escaped) {
                escaped = false;
            } else if (ch == '\\') {
                escaped = true;
            } else if (ch == '"') {
                in_string = false;
            }
            continue;
        }
        if (ch == '"') {
            in_string = true;
        } else if (ch == open) {
            ++depth;
        } else if (ch == close) {
            --depth;
            if (depth == 0) {
                return i + 1;
            }
        }
    }
    return std::string::npos;
}

size_t json_value_end(const std::string& text, size_t pos) {
    pos = skip_json_ws(text, pos);
    if (pos >= text.size()) {
        return pos;
    }
    if (text[pos] == '"') {
        std::string ignored;
        size_t end = pos;
        return parse_json_string_at(text, pos, ignored, end) ? end : text.size();
    }
    if (text[pos] == '{' || text[pos] == '[') {
        const size_t end = json_matching_bracket(text, pos);
        return end == std::string::npos ? text.size() : end;
    }
    size_t end = pos;
    while (end < text.size() && text[end] != ',' && text[end] != '}' && text[end] != ']') {
        ++end;
    }
    return end;
}

bool json_find_member_value(const std::string& object, const std::string& key, size_t& value_pos) {
    size_t pos = skip_json_ws(object, 0);
    if (pos >= object.size() || object[pos] != '{') {
        return false;
    }
    ++pos;
    while (pos < object.size()) {
        pos = skip_json_ws(object, pos);
        if (pos >= object.size() || object[pos] == '}') {
            return false;
        }
        std::string name;
        size_t name_end = pos;
        if (!parse_json_string_at(object, pos, name, name_end)) {
            return false;
        }
        pos = skip_json_ws(object, name_end);
        if (pos >= object.size() || object[pos] != ':') {
            return false;
        }
        pos = skip_json_ws(object, pos + 1);
        if (name == key) {
            value_pos = pos;
            return true;
        }
        pos = json_value_end(object, pos);
        pos = skip_json_ws(object, pos);
        if (pos < object.size() && object[pos] == ',') {
            ++pos;
        }
    }
    return false;
}

bool json_raw_value(const std::string& json, const std::vector<std::string>& path, std::string& raw) {
    std::string object = json;
    for (size_t i = 0; i < path.size(); ++i) {
        size_t value_pos = 0;
        if (!json_find_member_value(object, path[i], value_pos)) {
            return false;
        }
        const size_t value_end = json_value_end(object, value_pos);
        raw = object.substr(value_pos, value_end - value_pos);
        object = raw;
    }
    return !path.empty();
}

std::string json_scalar_text(const std::string& raw) {
    const std::string trimmed = trim_copy(raw);
    if (trimmed.empty()) {
        return {};
    }
    if (trimmed.front() == '"') {
        std::string value;
        size_t end = 0;
        if (parse_json_string_at(trimmed, 0, value, end)) {
            return value;
        }
    }
    return trimmed;
}

bool json_scalar(const std::string& json, const std::vector<std::string>& path, std::string& value) {
    std::string raw;
    if (!json_raw_value(json, path, raw)) {
        return false;
    }
    raw = trim_copy(raw);
    if (raw.empty() || raw.front() == '{' || raw.front() == '[') {
        return false;
    }
    value = json_scalar_text(raw);
    return true;
}

bool parse_number(const std::string& text, double& value) {
    const std::string trimmed = trim_copy(text);
    if (trimmed.empty() || trimmed == "true" || trimmed == "false" || trimmed == "null") {
        return false;
    }
    char* end = nullptr;
    value = std::strtod(trimmed.c_str(), &end);
    return end != trimmed.c_str() && *end == '\0';
}

std::string format_delta(double value) {
    std::ostringstream out;
    out << std::fixed << std::setprecision(3) << value;
    return out.str();
}

struct ReportFact {
    const char* label;
    std::vector<std::string> path;
};

const std::vector<ReportFact>& report_facts() {
    static const std::vector<ReportFact> facts = {
        {"schema", {"schema"}},
        {"mode", {"mode"}},
        {"ok", {"ok"}},
        {"version", {"version"}},
        {"level", {"level"}},
        {"lane count", {"lane_count"}},
        {"typed level 3", {"typed_level3"}},
        {"codec", {"codec"}},
        {"codec profile", {"codec_profile"}},
        {"input", {"input"}},
        {"output", {"output"}},
        {"unpack dir", {"unpack_dir"}},
        {"unpack format", {"unpack_format"}},
        {"pk3 state", {"pk3_state"}},
        {"trainer index", {"trainer_index"}},
        {"pk3 edits enabled", {"pk3_edits_enabled"}},
        {"lane select mode", {"lane_select_mode"}},
        {"lane select value", {"lane_select_value"}},
        {"lane select from", {"lane_select_from"}},
        {"lane select to", {"lane_select_to"}},
        {"consolidate root", {"consolidate_root"}},
        {"spc3 size bytes", {"spc3_size_bytes"}},
        {"source zip bytes", {"source_zip_bytes"}},
        {"raw payload bytes", {"raw_payload_bytes"}},
        {"hotloop backend", {"config", "hotloop_backend"}},
        {"predictor loaded", {"config", "predictor_loaded"}},
        {"limit zips", {"config", "limit_zips"}},
        {"zips found", {"config", "zips_found_for_run"}},
        {"roundtrip mismatches", {"roundtrip_mismatches"}},
        {"internal crc mismatches", {"internal_crc_mismatches"}},
        {"source compare enabled", {"source_compare_enabled"}},
        {"source compare mismatches", {"source_compare_mismatches"}},
        {"crc mismatches", {"crc_mismatches"}},
        {"input spc3 count", {"input_spc3_count"}},
        {"copy mode", {"copy_mode"}},
        {"build ms", {"build_ms"}},
        {"total ms", {"total_ms"}},
        {"gpu status", {"gpu_rebuild", "status"}},
        {"gpu requested", {"gpu_rebuild", "requested"}},
        {"gpu used", {"gpu_rebuild", "used"}},
        {"gpu fallback reason", {"gpu_rebuild", "fallback_reason"}},
        {"gpu download mode", {"gpu_rebuild", "download_mode"}},
        {"gpu runtime cache hit", {"gpu_rebuild", "runtime_cache_hit"}},
        {"gpu runtime failure cached", {"gpu_rebuild", "runtime_failure_cached"}},
        {"gpu runtime initializations", {"gpu_rebuild", "runtime_initializations"}},
        {"gpu output bytes", {"gpu_rebuild", "output_bytes"}},
        {"gpu value count", {"gpu_rebuild", "value_count"}},
        {"gpu compile ms", {"gpu_rebuild", "compile_ms"}},
        {"gpu upload ms", {"gpu_rebuild", "upload_ms"}},
        {"gpu kernel ms", {"gpu_rebuild", "kernel_ms"}},
        {"gpu download ms", {"gpu_rebuild", "download_ms"}},
        {"gpu host crc ms", {"gpu_rebuild", "host_crc_ms"}},
        {"gpu total ms", {"gpu_rebuild", "total_ms"}},
        {"gpu mismatched lanes", {"gpu_rebuild", "mismatched_lanes"}},
        {"gpu mismatched bytes", {"gpu_rebuild", "mismatched_bytes"}},
        {"cpu profile used", {"cpu_decode_profile", "used"}},
        {"cpu crc backend", {"cpu_decode_profile", "crc_backend"}},
        {"cpu profile lanes", {"cpu_decode_profile", "lane_count"}},
        {"cpu typed lanes", {"cpu_decode_profile", "typed_lanes"}},
        {"cpu legacy lanes", {"cpu_decode_profile", "legacy_lanes"}},
        {"cpu crc bytes", {"cpu_decode_profile", "crc_bytes"}},
        {"cpu stream decode ms", {"cpu_decode_profile", "stream_decode_ms"}},
        {"cpu iv expand ms", {"cpu_decode_profile", "iv_expand_ms"}},
        {"cpu rebuild encrypt ms", {"cpu_decode_profile", "rebuild_encrypt_ms"}},
        {"cpu crc ms", {"cpu_decode_profile", "crc_ms"}},
        {"cpu profile total ms", {"cpu_decode_profile", "total_ms"}},
        {"asm policy", {"asm_recommendation", "policy"}},
        {"asm largest slice", {"asm_recommendation", "largest_slice"}},
        {"asm decision", {"asm_recommendation", "decision"}},
        {"asm next action", {"asm_recommendation", "next_action"}},
    };
    return facts;
}

void append_report_summary(std::ostringstream& out, const std::string& title, const std::string& path, const std::string& json) {
    out << "\r\n" << title << "\r\n";
    out << "path: " << path << "\r\n";
    for (const ReportFact& fact : report_facts()) {
        std::string value;
        if (json_scalar(json, fact.path, value) && (!value.empty() || std::strcmp(fact.label, "gpu fallback reason") == 0)) {
            out << fact.label << ": " << value << "\r\n";
        }
    }
}

std::string build_report_view(const std::string& report_path, const std::string& compare_report_path) {
    std::ostringstream out;
    const std::string resolved_report_path = migrate_full_corpus_path(report_path);
    const std::string resolved_compare_path = migrate_full_corpus_path(compare_report_path);
    std::string report_json;
    std::string error;
    if (resolved_report_path.empty()) {
        return "\r\nReport summary skipped: report path is empty.\r\n";
    }
    if (!read_text_file(resolved_report_path, report_json, error)) {
        return "\r\nReport summary skipped: " + error + "\r\n";
    }

    append_report_summary(out, "Report summary", resolved_report_path, report_json);
    if (resolved_compare_path.empty()) {
        return out.str();
    }

    std::string compare_json;
    if (!read_text_file(resolved_compare_path, compare_json, error)) {
        out << "\r\nReport comparison skipped: " << error << "\r\n";
        return out.str();
    }

    out << "\r\nReport comparison\r\n";
    out << "current: " << resolved_report_path << "\r\n";
    out << "compare: " << resolved_compare_path << "\r\n";
    for (const ReportFact& fact : report_facts()) {
        std::string left;
        std::string right;
        const bool has_left = json_scalar(report_json, fact.path, left);
        const bool has_right = json_scalar(compare_json, fact.path, right);
        if (!has_left && !has_right) {
            continue;
        }
        out << fact.label << ": " << (has_left ? left : "") << " | " << (has_right ? right : "");
        double left_number = 0;
        double right_number = 0;
        if (has_left && has_right && parse_number(left, left_number) && parse_number(right, right_number)) {
            out << " delta=" << format_delta(right_number - left_number);
        }
        out << "\r\n";
    }
    return out.str();
}

DarkSelectorState* selector_for_hwnd(HWND hwnd) {
    if (hwnd == g_level_selector.hwnd || hwnd == g_level_selector.popup) {
        return &g_level_selector;
    }
    if (hwnd == g_profile_selector.hwnd || hwnd == g_profile_selector.popup) {
        return &g_profile_selector;
    }
    return nullptr;
}

std::string selector_text(const DarkSelectorState* selector) {
    if (!selector || selector->items.empty()) {
        return {};
    }
    int selected = selector->selected;
    if (selected < 0 || selected >= static_cast<int>(selector->items.size())) {
        selected = 0;
    }
    return selector->items[static_cast<size_t>(selected)];
}

std::string get_combo_text(HWND hwnd) {
    if (DarkSelectorState* selector = selector_for_hwnd(hwnd)) {
        return selector_text(selector);
    }
    LRESULT selected = SendMessageA(hwnd, CB_GETCURSEL, 0, 0);
    if (selected == CB_ERR) {
        return get_window_text(hwnd);
    }
    LRESULT length = SendMessageA(hwnd, CB_GETLBTEXTLEN, static_cast<WPARAM>(selected), 0);
    if (length == CB_ERR || length < 0) {
        return {};
    }
    std::vector<char> buffer(static_cast<size_t>(length) + 1, '\0');
    SendMessageA(hwnd, CB_GETLBTEXT, static_cast<WPARAM>(selected), reinterpret_cast<LPARAM>(buffer.data()));
    return std::string(buffer.data());
}

bool checked(HWND hwnd) {
    if (hwnd == g_app.mode_verify) {
        return g_app.selected_mode_id == IdModeVerify;
    }
    if (hwnd == g_app.mode_pack) {
        return g_app.selected_mode_id == IdModePack;
    }
    if (hwnd == g_app.mode_consolidate) {
        return g_app.selected_mode_id == IdModeConsolidate;
    }
    if (hwnd == g_app.mode_inspect) {
        return g_app.selected_mode_id == IdModeInspect;
    }
    if (hwnd == g_app.mode_unpack) {
        return g_app.selected_mode_id == IdModeUnpack;
    }
    if (hwnd == g_app.lane_all) {
        return g_app.selected_lane_select_id == IdLaneAll;
    }
    if (hwnd == g_app.lane_one) {
        return g_app.selected_lane_select_id == IdLaneOne;
    }
    if (hwnd == g_app.lane_range) {
        return g_app.selected_lane_select_id == IdLaneRange;
    }
    if (hwnd == g_app.pk3_state_egg) {
        return g_app.selected_pk3_state_id == IdPk3StateEgg;
    }
    if (hwnd == g_app.pk3_state_hatched_shiny) {
        return g_app.selected_pk3_state_id == IdPk3StateHatchedShiny;
    }
    if (hwnd == g_app.pk3_state_hatched_not_shiny) {
        return g_app.selected_pk3_state_id == IdPk3StateHatchedNotShiny;
    }
    if (hwnd == g_app.typed) {
        return g_app.typed_checked;
    }
    if (hwnd == g_app.gpu) {
        return g_app.gpu_checked;
    }
    if (hwnd == g_app.internal_only) {
        return g_app.internal_only_checked;
    }
    if (hwnd == g_app.external_predictor) {
        return g_app.external_predictor_checked;
    }
    if (hwnd == g_app.no_entropy_probe) {
        return g_app.no_entropy_probe_checked;
    }
    return SendMessageA(hwnd, BM_GETCHECK, 0, 0) == BST_CHECKED;
}

std::string selected_mode() {
    if (checked(g_app.mode_pack)) {
        return "pack";
    }
    if (checked(g_app.mode_consolidate)) {
        return "consolidate";
    }
    if (checked(g_app.mode_inspect)) {
        return "inspect";
    }
    if (checked(g_app.mode_unpack)) {
        return "unpack";
    }
    return "verify";
}

std::string selected_pk3_state() {
    if (checked(g_app.pk3_state_hatched_shiny)) {
        return "hatched-shiny";
    }
    if (checked(g_app.pk3_state_hatched_not_shiny)) {
        return "hatched-not-shiny";
    }
    return "egg";
}

std::string required_text(HWND hwnd, const char* label) {
    std::string value = migrate_full_corpus_path(get_window_text(hwnd));
    if (value != get_window_text(hwnd)) {
        SetWindowTextA(hwnd, value.c_str());
    }
    if (value.empty()) {
        throw std::runtime_error(std::string(label) + " is required");
    }
    return value;
}

void add_optional_pair(std::vector<std::string>& args, const char* flag, const std::string& value) {
    if (!value.empty()) {
        args.insert(args.end(), {flag, value});
    }
}

uint32_t packed_move_data(HWND combo) {
    const LRESULT selected = SendMessageA(combo, CB_GETCURSEL, 0, 0);
    if (selected == CB_ERR) {
        return 0;
    }
    const LRESULT data = SendMessageA(combo, CB_GETITEMDATA, static_cast<WPARAM>(selected), 0);
    if (data == CB_ERR) {
        return 0;
    }
    return static_cast<uint32_t>(data);
}

uint16_t selected_move_id(size_t slot) {
    return static_cast<uint16_t>(packed_move_data(g_app.move_combo[slot]) & 0xFFFFU);
}

uint8_t selected_move_base_pp(size_t slot) {
    return static_cast<uint8_t>((packed_move_data(g_app.move_combo[slot]) >> 16) & 0xFFU);
}

uint8_t selected_pp_ups(size_t slot) {
    const LRESULT selected = SendMessageA(g_app.pp_up_combo[slot], CB_GETCURSEL, 0, 0);
    if (selected == CB_ERR || selected < 0 || selected > 3) {
        return 0;
    }
    return static_cast<uint8_t>(selected);
}

uint8_t calculated_pp(size_t slot) {
    const uint8_t base = selected_move_base_pp(slot);
    const uint8_t ups = selected_pp_ups(slot);
    return static_cast<uint8_t>((static_cast<uint32_t>(base) * (5U + ups)) / 5U);
}

std::string csv4_u16(const std::array<uint16_t, 4>& values) {
    std::ostringstream out;
    out << values[0] << "," << values[1] << "," << values[2] << "," << values[3];
    return out.str();
}

std::string csv4_u8(const std::array<uint8_t, 4>& values) {
    std::ostringstream out;
    out << static_cast<uint32_t>(values[0]) << ","
        << static_cast<uint32_t>(values[1]) << ","
        << static_cast<uint32_t>(values[2]) << ","
        << static_cast<uint32_t>(values[3]);
    return out.str();
}

bool selected_moves_enabled() {
    for (size_t i = 0; i < 4; ++i) {
        if (selected_move_id(i) != 0) {
            return true;
        }
    }
    return false;
}

std::array<uint16_t, 4> selected_move_ids() {
    return {selected_move_id(0), selected_move_id(1), selected_move_id(2), selected_move_id(3)};
}

std::array<uint8_t, 4> selected_pp_values() {
    return {calculated_pp(0), calculated_pp(1), calculated_pp(2), calculated_pp(3)};
}

std::array<uint8_t, 4> selected_pp_up_values() {
    return {selected_pp_ups(0), selected_pp_ups(1), selected_pp_ups(2), selected_pp_ups(3)};
}

void update_move_pp_fields() {
    for (size_t i = 0; i < 4; ++i) {
        const std::string pp = std::to_string(static_cast<uint32_t>(calculated_pp(i)));
        SetWindowTextA(g_app.pp_value[i], pp.c_str());
    }
}

std::string quote_arg(const std::string& arg) {
    if (arg.empty()) {
        return "\"\"";
    }
    bool needs_quotes = false;
    for (const char ch : arg) {
        if (ch == ' ' || ch == '\t' || ch == '"') {
            needs_quotes = true;
            break;
        }
    }
    if (!needs_quotes) {
        return arg;
    }
    std::string quoted = "\"";
    unsigned backslashes = 0;
    for (const char ch : arg) {
        if (ch == '\\') {
            ++backslashes;
        } else if (ch == '"') {
            quoted.append(backslashes * 2 + 1, '\\');
            quoted.push_back('"');
            backslashes = 0;
        } else {
            quoted.append(backslashes, '\\');
            backslashes = 0;
            quoted.push_back(ch);
        }
    }
    quoted.append(backslashes * 2, '\\');
    quoted.push_back('"');
    return quoted;
}

std::string command_line(const std::vector<std::string>& args) {
    std::ostringstream out;
    for (size_t i = 0; i < args.size(); ++i) {
        if (i != 0) {
            out << ' ';
        }
        out << quote_arg(args[i]);
    }
    return out.str();
}

std::string hex_encode_arg(const std::string& arg) {
    static constexpr char kHex[] = "0123456789abcdef";
    std::string out;
    out.reserve(arg.size() * 2);
    for (const unsigned char ch : arg) {
        out.push_back(kHex[ch >> 4]);
        out.push_back(kHex[ch & 0x0F]);
    }
    return out;
}

std::string server_run_line(const std::vector<std::string>& args) {
    if (args.size() < 2) {
        throw std::runtime_error("server run requires executable plus arguments");
    }
    std::string line = "RUN";
    for (size_t i = 1; i < args.size(); ++i) {
        line.push_back('\t');
        line += hex_encode_arg(args[i]);
    }
    line.push_back('\n');
    return line;
}

fs::path module_directory() {
    char module[MAX_PATH] = {};
    const DWORD length = GetModuleFileNameA(nullptr, module, MAX_PATH);
    if (length == 0 || length >= MAX_PATH) {
        return fs::current_path();
    }
    return fs::path(module).parent_path();
}

fs::path workspace_root_path() {
    fs::path dir = module_directory();
    if (dir.filename() == "spc3_gui_native" &&
        dir.parent_path().filename() == "spinda" &&
        dir.parent_path().parent_path().filename() == "tools") {
        return dir.parent_path().parent_path().parent_path();
    }
    return fs::current_path();
}

fs::path worker_cwd_path() {
    fs::path dir = module_directory();
    if (dir.filename() == "spc3_gui_native" &&
        dir.parent_path().filename() == "spinda" &&
        dir.parent_path().parent_path().filename() == "tools") {
        return dir.parent_path().parent_path().parent_path();
    }
    return dir;
}

std::string workspace_path(const char* relative) {
    return (workspace_root_path() / relative).string();
}

bool starts_with(const std::string& value, const std::string& prefix) {
    return value.size() >= prefix.size() && value.compare(0, prefix.size(), prefix) == 0;
}

std::string migrate_full_corpus_path(const std::string& path_text) {
    if (path_text.empty()) {
        return {};
    }
    fs::path path(path_text);
    if (fs::exists(path)) {
        return path_text;
    }
    const std::string filename = path.filename().string();
    if (filename.empty()) {
        return path_text;
    }
    const bool is_full_corpus = starts_with(filename, "helper_full_") ||
                                starts_with(filename, "full_corpus_") ||
                                starts_with(filename, "_spc3_v02_typed_fast_real64_current") ||
                                starts_with(filename, "_spc3_gui_");
    if (!is_full_corpus) {
        return path_text;
    }
    const fs::path migrated = workspace_root_path() / "Helper-PC-Artifacts" / filename;
    if (fs::exists(migrated)) {
        return migrated.string();
    }
    return path_text;
}

std::string csv_unquote(std::string value) {
    value = trim_copy(value);
    if (value.size() < 2 || value.front() != '"' || value.back() != '"') {
        return value;
    }
    std::string out;
    out.reserve(value.size() - 2);
    for (size_t i = 1; i + 1 < value.size(); ++i) {
        if (value[i] == '"' && i + 2 < value.size() && value[i + 1] == '"') {
            out.push_back('"');
            ++i;
        } else {
            out.push_back(value[i]);
        }
    }
    return out;
}

std::string format_id_suffix(int id, int width = 3) {
    std::ostringstream out;
    out << " (#" << std::setw(width) << std::setfill('0') << id << ")";
    return out.str();
}

std::string ascii_lower_copy(std::string value) {
    std::transform(value.begin(), value.end(), value.begin(), [](unsigned char ch) {
        return static_cast<char>(std::tolower(ch));
    });
    return value;
}

std::string format_move_choice_label(const MoveChoice& choice) {
    return choice.name + format_id_suffix(choice.id);
}

void sort_and_label_move_choices(std::vector<MoveChoice>& choices) {
    std::stable_sort(choices.begin(), choices.end(), [](const MoveChoice& left, const MoveChoice& right) {
        if (left.id == 0 || right.id == 0) {
            return left.id == 0 && right.id != 0;
        }
        const std::string left_key = ascii_lower_copy(left.name);
        const std::string right_key = ascii_lower_copy(right.name);
        if (left_key != right_key) {
            return left_key < right_key;
        }
        return left.id < right.id;
    });
    for (MoveChoice& choice : choices) {
        choice.label = format_move_choice_label(choice);
    }
}

std::string format_value_choice_label(const ValueChoice& choice, int width = 3) {
    return choice.name + format_id_suffix(choice.value, width);
}

void sort_and_label_value_choices(std::vector<ValueChoice>& choices, int width = 3) {
    std::stable_sort(choices.begin(), choices.end(), [](const ValueChoice& left, const ValueChoice& right) {
        const std::string left_key = ascii_lower_copy(left.name);
        const std::string right_key = ascii_lower_copy(right.name);
        if (left_key != right_key) {
            return left_key < right_key;
        }
        return left.value < right.value;
    });
    for (ValueChoice& choice : choices) {
        choice.label = format_value_choice_label(choice, width);
    }
}

void add_combo_item(HWND combo, const std::string& label, int value) {
    const LRESULT item = SendMessageA(combo, CB_ADDSTRING, 0, reinterpret_cast<LPARAM>(label.c_str()));
    if (item != CB_ERR && item != CB_ERRSPACE) {
        SendMessageA(combo, CB_SETITEMDATA, static_cast<WPARAM>(item), static_cast<LPARAM>(value));
    }
}

void add_no_change_item(HWND combo) {
    add_combo_item(combo, "No change", kComboNoValue);
}

std::optional<int> selected_combo_value(HWND combo) {
    const LRESULT selected = SendMessageA(combo, CB_GETCURSEL, 0, 0);
    if (selected == CB_ERR) {
        return std::nullopt;
    }
    const LRESULT data = SendMessageA(combo, CB_GETITEMDATA, static_cast<WPARAM>(selected), 0);
    if (data == CB_ERR || static_cast<int>(data) == kComboNoValue) {
        return std::nullopt;
    }
    return static_cast<int>(data);
}

void add_optional_combo_pair(std::vector<std::string>& args, const char* flag, HWND combo) {
    const std::optional<int> value = selected_combo_value(combo);
    if (value) {
        args.insert(args.end(), {flag, std::to_string(*value)});
    }
}

std::optional<std::string> csv6_from_combos(const std::array<HWND, 6>& combos, const char* label) {
    std::array<int, 6> values{};
    bool any = false;
    bool all = true;
    for (size_t i = 0; i < combos.size(); ++i) {
        const std::optional<int> value = selected_combo_value(combos[i]);
        if (value) {
            values[i] = *value;
            any = true;
        } else {
            all = false;
        }
    }
    if (!any) {
        return std::nullopt;
    }
    if (!all) {
        throw std::runtime_error(std::string(label) + " requires all six dropdowns or all six set to No change");
    }
    std::ostringstream out;
    out << values[0] << "," << values[1] << "," << values[2] << ","
        << values[3] << "," << values[4] << "," << values[5];
    return out.str();
}

std::vector<MoveChoice> load_move_choices_from_csv(const fs::path& path) {
    std::string text;
    std::string error;
    if (!read_text_file(path.string(), text, error)) {
        return {};
    }

    std::vector<MoveChoice> choices;
    std::istringstream in(text);
    std::string line;
    bool header = true;
    while (std::getline(in, line)) {
        line = trim_copy(line);
        if (line.empty()) {
            continue;
        }
        if (header) {
            header = false;
            if (line.rfind("id,", 0) == 0) {
                continue;
            }
        }
        const size_t first = line.find(',');
        const size_t second = first == std::string::npos ? std::string::npos : line.find(',', first + 1);
        if (first == std::string::npos || second == std::string::npos) {
            continue;
        }
        const uint32_t id = static_cast<uint32_t>(std::stoul(line.substr(0, first)));
        const uint32_t pp = static_cast<uint32_t>(std::stoul(line.substr(first + 1, second - first - 1)));
        std::string name = csv_unquote(line.substr(second + 1));
        if (id > 0xFFFF || pp > 0xFF || name.empty()) {
            continue;
        }
        MoveChoice choice;
        choice.id = static_cast<uint16_t>(id);
        choice.pp = static_cast<uint8_t>(pp);
        choice.name = std::move(name);
        choices.push_back(std::move(choice));
    }
    const auto none = std::find_if(choices.begin(), choices.end(), [](const MoveChoice& choice) {
        return choice.id == 0;
    });
    if (choices.empty() || none == choices.end()) {
        choices.clear();
        return choices;
    }
    sort_and_label_move_choices(choices);
    return choices;
}

const std::vector<MoveChoice>& move_choices() {
    if (!g_move_choices.empty()) {
        return g_move_choices;
    }

    g_move_choices = load_move_choices_from_csv(module_directory() / "gen3_moves.csv");
    if (g_move_choices.empty()) {
        g_move_choices = load_move_choices_from_csv(workspace_root_path() / "tools" / "spinda" / "spc3_gui_native" / "gen3_moves.csv");
    }
    if (g_move_choices.empty()) {
        g_move_choices = {
            {0, 0, "None", ""},
            {1, 35, "Pound", ""},
            {33, 35, "Tackle", ""},
            {39, 30, "Tail Whip", ""},
            {45, 40, "Growl", ""},
        };
        sort_and_label_move_choices(g_move_choices);
    }
    return g_move_choices;
}

std::vector<ValueChoice> load_value_choices_from_csv(const fs::path& path) {
    std::string text;
    std::string error;
    if (!read_text_file(path.string(), text, error)) {
        return {};
    }

    std::vector<ValueChoice> choices;
    std::istringstream in(text);
    std::string line;
    bool header = true;
    while (std::getline(in, line)) {
        line = trim_copy(line);
        if (line.empty()) {
            continue;
        }
        if (header) {
            header = false;
            if (line.rfind("id,", 0) == 0) {
                continue;
            }
        }
        const size_t first = line.find(',');
        if (first == std::string::npos) {
            continue;
        }
        const uint32_t value = static_cast<uint32_t>(std::stoul(line.substr(0, first)));
        std::string name = csv_unquote(line.substr(first + 1));
        if (value > 0xFFFF || name.empty()) {
            continue;
        }
        choices.push_back({static_cast<int>(value), std::move(name), {}});
    }
    sort_and_label_value_choices(choices);
    return choices;
}

const std::vector<ValueChoice>& held_item_choices() {
    if (!g_held_item_choices.empty()) {
        return g_held_item_choices;
    }

    g_held_item_choices = load_value_choices_from_csv(module_directory() / "gen3_held_items.csv");
    if (g_held_item_choices.empty()) {
        g_held_item_choices = load_value_choices_from_csv(workspace_root_path() / "tools" / "spinda" / "spc3_gui_native" / "gen3_held_items.csv");
    }
    if (g_held_item_choices.empty()) {
        g_held_item_choices = {
            {0, "(None)", ""},
            {4, "Poke Ball", ""},
            {179, "BrightPowder", ""},
            {188, "Amulet Coin", ""},
        };
        sort_and_label_value_choices(g_held_item_choices);
    }
    return g_held_item_choices;
}

std::vector<LocationChoice> load_location_choices_from_csv(const fs::path& path) {
    std::string text;
    std::string error;
    if (!read_text_file(path.string(), text, error)) {
        return {};
    }

    std::vector<LocationChoice> choices;
    std::istringstream in(text);
    std::string line;
    bool header = true;
    while (std::getline(in, line)) {
        line = trim_copy(line);
        if (line.empty()) {
            continue;
        }
        if (header) {
            header = false;
            if (line.rfind("value,", 0) == 0) {
                continue;
            }
        }
        const size_t first = line.find(',');
        const size_t last = line.rfind(',');
        const size_t third = last == std::string::npos ? std::string::npos : line.rfind(',', last - 1);
        const size_t second = third == std::string::npos ? std::string::npos : line.rfind(',', third - 1);
        if (first == std::string::npos || second == std::string::npos || third == std::string::npos ||
            last == std::string::npos || first >= second || second >= third || third >= last) {
            continue;
        }
        const uint32_t value = static_cast<uint32_t>(std::stoul(line.substr(0, first)));
        std::string name = csv_unquote(line.substr(first + 1, second - first - 1));
        if (value > 0xFF || name.empty()) {
            continue;
        }
        LocationChoice choice;
        choice.value = static_cast<uint8_t>(value);
        choice.name = std::move(name);
        choice.ruby_sapphire = std::stoul(line.substr(second + 1, third - second - 1)) != 0;
        choice.emerald = std::stoul(line.substr(third + 1, last - third - 1)) != 0;
        choice.fire_red_leaf_green = std::stoul(line.substr(last + 1)) != 0;
        choice.label = choice.name + format_id_suffix(choice.value);
        choices.push_back(std::move(choice));
    }
    std::stable_sort(choices.begin(), choices.end(), [](const LocationChoice& left, const LocationChoice& right) {
        const std::string left_key = ascii_lower_copy(left.name);
        const std::string right_key = ascii_lower_copy(right.name);
        if (left_key != right_key) {
            return left_key < right_key;
        }
        return left.value < right.value;
    });
    for (LocationChoice& choice : choices) {
        choice.label = choice.name + format_id_suffix(choice.value);
    }
    return choices;
}

const std::vector<LocationChoice>& location_choices() {
    if (!g_location_choices.empty()) {
        return g_location_choices;
    }

    g_location_choices = load_location_choices_from_csv(module_directory() / "gen3_locations.csv");
    if (g_location_choices.empty()) {
        g_location_choices = load_location_choices_from_csv(workspace_root_path() / "tools" / "spinda" / "spc3_gui_native" / "gen3_locations.csv");
    }
    if (g_location_choices.empty()) {
        g_location_choices = {
            {32, true, true, false, "Petalburg Woods", ""},
            {146, false, false, true, "Four Island", ""},
            {253, true, true, true, "(gift egg)", ""},
        };
        std::stable_sort(g_location_choices.begin(), g_location_choices.end(), [](const LocationChoice& left, const LocationChoice& right) {
            return ascii_lower_copy(left.name) < ascii_lower_copy(right.name);
        });
        for (LocationChoice& choice : g_location_choices) {
            choice.label = choice.name + format_id_suffix(choice.value);
        }
    }
    return g_location_choices;
}

uint32_t spinda_fast_exp_for_level(int level) {
    level = std::clamp(level, 1, 100);
    const uint32_t n = static_cast<uint32_t>(level);
    return (4U * n * n * n) / 5U;
}

std::vector<ValueChoice> spinda_level_choices() {
    std::vector<ValueChoice> choices;
    choices.reserve(100);
    for (int level = 1; level <= 100; ++level) {
        const uint32_t exp = spinda_fast_exp_for_level(level);
        std::ostringstream name;
        name << "Level " << level << " - " << exp << " EXP";
        choices.push_back({static_cast<int>(exp), name.str(), name.str()});
    }
    return choices;
}

std::vector<ValueChoice> simple_numeric_choices(int min_value, int max_value, const char* prefix = nullptr) {
    std::vector<ValueChoice> choices;
    choices.reserve(static_cast<size_t>(max_value - min_value + 1));
    for (int value = min_value; value <= max_value; ++value) {
        std::string text;
        if (prefix && *prefix) {
            text = std::string(prefix) + ": " + std::to_string(value);
        } else {
            text = std::to_string(value);
        }
        choices.push_back({value, text, text});
    }
    return choices;
}

std::vector<ValueChoice> origin_game_choices() {
    return {
        {3, "Emerald", "Emerald (E #003)"},
        {4, "FireRed", "FireRed (FR #004)"},
        {5, "LeafGreen", "LeafGreen (LG #005)"},
        {2, "Ruby", "Ruby (R #002)"},
        {1, "Sapphire", "Sapphire (S #001)"},
    };
}

std::vector<ValueChoice> ball_choices() {
    std::vector<ValueChoice> choices = {
        {7, "Dive Ball", ""},
        {3, "Great Ball", ""},
        {11, "Luxury Ball", ""},
        {1, "Master Ball", ""},
        {8, "Nest Ball", ""},
        {6, "Net Ball", ""},
        {4, "Poke Ball", ""},
        {12, "Premier Ball", ""},
        {9, "Repeat Ball", ""},
        {5, "Safari Ball", ""},
        {10, "Timer Ball", ""},
        {2, "Ultra Ball", ""},
    };
    sort_and_label_value_choices(choices, 2);
    return choices;
}

std::vector<ValueChoice> language_choices() {
    std::vector<ValueChoice> choices = {
        {2, "English", ""},
        {3, "French", ""},
        {5, "German", ""},
        {4, "Italian", ""},
        {1, "Japanese", ""},
        {7, "Spanish", ""},
    };
    sort_and_label_value_choices(choices, 2);
    return choices;
}

std::vector<ValueChoice> ot_gender_choices() {
    return {
        {1, "Female", "Female (#1)"},
        {0, "Male", "Male (#0)"},
    };
}

std::vector<ValueChoice> ability_slot_choices() {
    return {
        {0, "Ability slot 0", "Ability slot 0"},
        {1, "Ability slot 1", "Ability slot 1"},
    };
}

std::string default_exe_path() {
    fs::path dir = module_directory();
    fs::path module_path;
    {
        char module[MAX_PATH] = {};
        const DWORD length = GetModuleFileNameA(nullptr, module, MAX_PATH);
        if (length != 0 && length < MAX_PATH) {
            module_path = fs::path(module);
        }
    }
    std::vector<fs::path> candidates;
    candidates.push_back(dir / "spc3_prototype.exe");
    candidates.push_back(dir / "spc3_prototype_baseline.exe");
    const fs::path maybe_gui_root = workspace_root_path() / "tools" / "spinda" / "spc3_gui_native";
    const fs::path maybe_prototype_root = workspace_root_path() / "tools" / "spinda" / "spc3_prototype";
    candidates.push_back(maybe_gui_root / "spc3_prototype.exe");
    candidates.push_back(maybe_gui_root / "spc3_prototype_baseline.exe");
    candidates.push_back(maybe_prototype_root / "spc3_prototype.exe");
    candidates.push_back(maybe_prototype_root / "spc3_prototype_baseline.exe");
    if (module_path.filename().string().find("baseline") != std::string::npos) {
        candidates.insert(candidates.begin(), dir / "spc3_prototype_baseline.exe");
    }
    fs::path best;
    fs::file_time_type best_time{};
    bool have_best = false;
    for (const fs::path& candidate : candidates) {
        if (!fs::exists(candidate)) {
            continue;
        }
        const fs::file_time_type candidate_time = fs::last_write_time(candidate);
        if (!have_best || candidate_time > best_time) {
            best = candidate;
            best_time = candidate_time;
            have_best = true;
        }
    }
    if (have_best) {
        return best.string();
    }
    return (workspace_root_path() / "tools" / "spinda" / "spc3_prototype" / "spc3_prototype.exe").string();
}

std::string initial_directory_for(const std::string& path_text) {
    if (path_text.empty()) {
        return fs::current_path().string();
    }
    fs::path path(path_text);
    if (fs::is_directory(path)) {
        return path.string();
    }
    if (path.has_parent_path() && fs::is_directory(path.parent_path())) {
        return path.parent_path().string();
    }
    return fs::current_path().string();
}

std::string filename_for(const std::string& path_text) {
    if (path_text.empty()) {
        return {};
    }
    fs::path path(path_text);
    if (path.has_filename() && !fs::is_directory(path)) {
        return path.filename().string();
    }
    return {};
}

bool choose_file_path(HWND owner, HWND target, const char* title, const char* filter, const char* default_ext, bool save) {
    std::string current = get_window_text(target);
    std::string initial_dir = initial_directory_for(current);
    std::string initial_file = filename_for(current);
    std::array<char, 4096> file_buffer{};
    if (!initial_file.empty()) {
        if (initial_file.size() >= file_buffer.size()) {
            initial_file.resize(file_buffer.size() - 1);
        }
        std::memcpy(file_buffer.data(), initial_file.data(), initial_file.size());
    }

    OPENFILENAMEA ofn{};
    ofn.lStructSize = sizeof(ofn);
    ofn.hwndOwner = owner;
    ofn.lpstrTitle = title;
    ofn.lpstrFilter = filter;
    ofn.lpstrFile = file_buffer.data();
    ofn.nMaxFile = static_cast<DWORD>(file_buffer.size());
    ofn.lpstrInitialDir = initial_dir.c_str();
    ofn.lpstrDefExt = default_ext;
    ofn.Flags = OFN_EXPLORER | OFN_HIDEREADONLY | OFN_NOCHANGEDIR | OFN_PATHMUSTEXIST;
    if (save) {
        ofn.Flags |= OFN_OVERWRITEPROMPT;
        if (!GetSaveFileNameA(&ofn)) {
            return false;
        }
    } else {
        ofn.Flags |= OFN_FILEMUSTEXIST;
        if (!GetOpenFileNameA(&ofn)) {
            return false;
        }
    }
    SetWindowTextA(target, file_buffer.data());
    return true;
}

int CALLBACK browse_folder_callback(HWND hwnd, UINT message, LPARAM, LPARAM data) {
    if (message == BFFM_INITIALIZED && data != 0) {
        SendMessageA(hwnd, BFFM_SETSELECTIONA, TRUE, data);
    }
    return 0;
}

bool choose_folder_path(HWND owner, HWND target, const char* title) {
    std::string current = get_window_text(target);
    std::string initial_dir = initial_directory_for(current);

    BROWSEINFOA browse{};
    browse.hwndOwner = owner;
    browse.lpszTitle = title;
    browse.ulFlags = BIF_RETURNONLYFSDIRS | BIF_NEWDIALOGSTYLE;
    browse.lpfn = browse_folder_callback;
    browse.lParam = reinterpret_cast<LPARAM>(initial_dir.c_str());

    PIDLIST_ABSOLUTE selected = SHBrowseForFolderA(&browse);
    if (!selected) {
        return false;
    }

    std::array<char, MAX_PATH> path{};
    bool ok = SHGetPathFromIDListA(selected, path.data()) != FALSE;
    CoTaskMemFree(selected);
    if (!ok) {
        return false;
    }
    SetWindowTextA(target, path.data());
    return true;
}

std::vector<std::string> build_args() {
    const std::string mode = selected_mode();
    const std::string exe = required_text(g_app.exe, "SPC3 executable path");
    const std::string report = required_text(g_app.report, "Report JSON path");
    std::vector<std::string> args{exe, "--mode", mode, "--report", report};

    if (mode == "verify") {
        args.insert(args.end(), {"--input", required_text(g_app.input, "Input .spc3")});
        add_optional_pair(args, "--predictor", get_window_text(g_app.predictor));
        if (!checked(g_app.internal_only)) {
            args.insert(args.end(), {"--root", required_text(g_app.root, "Lane ZIP root")});
        } else {
            args.push_back("--no-source-compare");
        }
        if (checked(g_app.gpu)) {
            args.push_back("--gpu-rebuild");
        }
    } else if (mode == "pack") {
        const std::string level = get_combo_text(g_app.level);
        const bool level_zero = level == "0";
        const bool level_three = level == "3";
        args.insert(args.end(), {
            "--root", required_text(g_app.root, "Lane ZIP root"),
            "--limit-zips", required_text(g_app.limit, "Limit"),
            "--level", level,
            "--output", required_text(g_app.output, "Output .spc3"),
        });
        add_optional_pair(args, "--predictor", get_window_text(g_app.predictor));
        if (level_three && checked(g_app.typed)) {
            args.push_back("--typed-level3");
        }
        const std::string profile = get_combo_text(g_app.profile);
        if (!level_zero && profile != "auto") {
            args.insert(args.end(), {"--codec-profile", profile});
        }
        if (level_three && checked(g_app.external_predictor)) {
            args.push_back("--external-predictor");
        }
        if (checked(g_app.no_entropy_probe)) {
            args.push_back("--no-entropy-probe");
        }
    } else if (mode == "consolidate") {
        args.insert(args.end(), {
            "--consolidate-root", required_text(g_app.consolidate_root, "SPC3 shard root"),
            "--output", required_text(g_app.output, "Output .spc3"),
        });
    } else if (mode == "inspect") {
        args.insert(args.end(), {"--input", required_text(g_app.input, "Input .spc3")});
    } else if (mode == "unpack") {
        args.insert(args.end(), {
            "--input", required_text(g_app.input, "Input .spc3"),
            "--unpack-dir", required_text(g_app.unpack_dir, "Output ZIP dir"),
            "--unpack-format", "zip",
        });
        const std::string pk3_state = selected_pk3_state();
        args.insert(args.end(), {"--pk3-state", pk3_state});
        if (pk3_state != "egg") {
            args.insert(args.end(), {"--trainer-index", required_text(g_app.trainer_index, "Trainer index JSON")});
        }
        add_optional_pair(args, "--set-nickname", get_window_text(g_app.set_nickname));
        add_optional_pair(args, "--set-ot-name", get_window_text(g_app.set_ot_name));
        if (selected_moves_enabled()) {
            args.insert(args.end(), {"--set-moves", csv4_u16(selected_move_ids())});
            args.insert(args.end(), {"--set-pp", csv4_u8(selected_pp_values())});
            args.insert(args.end(), {"--set-pp-ups", csv4_u8(selected_pp_up_values())});
        }
        if (const std::optional<std::string> evs = csv6_from_combos(g_app.ev_combo, "EVs")) {
            args.insert(args.end(), {"--set-evs", *evs});
        }
        if (const std::optional<std::string> ivs = csv6_from_combos(g_app.iv_combo, "IVs")) {
            args.insert(args.end(), {"--set-ivs", *ivs});
        }
        if (const std::optional<std::string> contest = csv6_from_combos(g_app.contest_combo, "Contest stats")) {
            args.insert(args.end(), {"--set-contest", *contest});
        }
        add_optional_combo_pair(args, "--set-held-item", g_app.set_held_item);
        add_optional_combo_pair(args, "--set-experience", g_app.set_experience);
        add_optional_combo_pair(args, "--set-friendship", g_app.set_friendship);
        const std::optional<int> pokerus_strain = selected_combo_value(g_app.set_pokerus_strain);
        const std::optional<int> pokerus_days = selected_combo_value(g_app.set_pokerus_days);
        if (pokerus_strain || pokerus_days) {
            if (!pokerus_strain || !pokerus_days) {
                throw std::runtime_error("Pokerus requires both strain and days, or both set to No change");
            }
            const int pokerus = ((*pokerus_strain & 0x0F) << 4) | (*pokerus_days & 0x0F);
            args.insert(args.end(), {"--set-pokerus", std::to_string(pokerus)});
        }
        add_optional_combo_pair(args, "--set-met-location", g_app.set_met_location);
        add_optional_combo_pair(args, "--set-met-level", g_app.set_met_level);
        add_optional_combo_pair(args, "--set-origin-game", g_app.set_origin_game);
        add_optional_combo_pair(args, "--set-ball", g_app.set_ball);
        add_optional_combo_pair(args, "--set-ot-gender", g_app.set_ot_gender);
        add_optional_combo_pair(args, "--set-language", g_app.set_language);
        add_optional_combo_pair(args, "--set-ability-number", g_app.set_ability_number);
        add_optional_pair(args, "--predictor", get_window_text(g_app.predictor));
        if (checked(g_app.lane_one)) {
            args.insert(args.end(), {"--lane", required_text(g_app.lane_value, "Lane")});
        } else if (checked(g_app.lane_range)) {
            args.insert(args.end(), {
                "--lane-from", required_text(g_app.lane_from, "Lane from"),
                "--lane-to", required_text(g_app.lane_to, "Lane to"),
            });
        } else {
            args.insert(args.end(), {"--lane-select", "all"});
        }
        if (checked(g_app.gpu)) {
            args.push_back("--gpu-rebuild");
        }
    }
    return args;
}

void append_console(const std::string& text) {
    const int length = GetWindowTextLengthA(g_app.console);
    SendMessageA(g_app.console, EM_SETSEL, static_cast<WPARAM>(length), static_cast<LPARAM>(length));
    SendMessageA(g_app.console, EM_REPLACESEL, FALSE, reinterpret_cast<LPARAM>(text.c_str()));
}

void set_running(bool running) {
    EnableWindow(g_app.summary, running ? FALSE : TRUE);
    EnableWindow(g_app.run, running ? FALSE : TRUE);
    EnableWindow(g_app.cancel, running ? TRUE : FALSE);
}

// Worker threads can outlive the main window when the user closes during a run.
void post_output(const std::string& text) {
    auto* payload = new std::string(text);
    if (!g_window_alive.load(std::memory_order_acquire) ||
        !PostMessageA(g_app.window, kAppendOutput, 0, reinterpret_cast<LPARAM>(payload))) {
        delete payload;
    }
}

void post_run_finished(DWORD exit_code) {
    if (g_window_alive.load(std::memory_order_acquire)) {
        PostMessageA(g_app.window, kRunFinished, static_cast<WPARAM>(exit_code), 0);
    }
}

void close_worker_locked(bool clear_process_active = true) {
    if (g_app.worker_active && g_app.worker_process.hProcess) {
        DWORD exit_code = 0;
        const bool alive =
            GetExitCodeProcess(g_app.worker_process.hProcess, &exit_code) &&
            exit_code == STILL_ACTIVE;
        if (alive && !g_app.worker_command_active && g_app.worker_stdin) {
            DWORD bytes_written = 0;
            const char stop_line[] = "STOP\n";
            WriteFile(
                g_app.worker_stdin,
                stop_line,
                static_cast<DWORD>(sizeof(stop_line) - 1),
                &bytes_written,
                nullptr);
            WaitForSingleObject(g_app.worker_process.hProcess, 1500);
        }
        if (GetExitCodeProcess(g_app.worker_process.hProcess, &exit_code) &&
            exit_code == STILL_ACTIVE) {
            TerminateProcess(g_app.worker_process.hProcess, 1);
        }
    }
    if (g_app.worker_stdin) {
        CloseHandle(g_app.worker_stdin);
        g_app.worker_stdin = nullptr;
    }
    if (g_app.worker_stdout) {
        CloseHandle(g_app.worker_stdout);
        g_app.worker_stdout = nullptr;
    }
    if (g_app.worker_process.hThread) {
        CloseHandle(g_app.worker_process.hThread);
    }
    if (g_app.worker_process.hProcess) {
        CloseHandle(g_app.worker_process.hProcess);
    }
    g_app.worker_process = {};
    g_app.worker_exe_path.clear();
    g_app.worker_active = false;
    g_app.worker_command_active = false;
    if (clear_process_active) {
        g_app.process_active = false;
    }
}

bool worker_is_alive_locked(bool clear_process_active = true) {
    if (!g_app.worker_active || !g_app.worker_process.hProcess) {
        return false;
    }
    DWORD exit_code = 0;
    if (!GetExitCodeProcess(g_app.worker_process.hProcess, &exit_code)) {
        close_worker_locked(clear_process_active);
        return false;
    }
    if (exit_code != STILL_ACTIVE) {
        close_worker_locked(clear_process_active);
        return false;
    }
    return true;
}

void ensure_worker_process(const std::string& exe_path) {
    std::lock_guard<std::mutex> lock(g_app.process_mutex);
    if (worker_is_alive_locked(false) && g_app.worker_exe_path == exe_path) {
        return;
    }
    close_worker_locked(false);

    SECURITY_ATTRIBUTES security{};
    security.nLength = sizeof(security);
    security.bInheritHandle = TRUE;

    HANDLE stdout_read = nullptr;
    HANDLE stdout_write = nullptr;
    HANDLE stdin_read = nullptr;
    HANDLE stdin_write = nullptr;
    auto cleanup_partial = [&]() {
        if (stdout_read) { CloseHandle(stdout_read); }
        if (stdout_write) { CloseHandle(stdout_write); }
        if (stdin_read) { CloseHandle(stdin_read); }
        if (stdin_write) { CloseHandle(stdin_write); }
    };

    if (!CreatePipe(&stdout_read, &stdout_write, &security, 0)) {
        throw std::runtime_error(last_error_message("CreatePipe stdout"));
    }
    if (!SetHandleInformation(stdout_read, HANDLE_FLAG_INHERIT, 0)) {
        const std::string error = last_error_message("SetHandleInformation stdout");
        cleanup_partial();
        throw std::runtime_error(error);
    }
    if (!CreatePipe(&stdin_read, &stdin_write, &security, 0)) {
        const std::string error = last_error_message("CreatePipe stdin");
        cleanup_partial();
        throw std::runtime_error(error);
    }
    if (!SetHandleInformation(stdin_write, HANDLE_FLAG_INHERIT, 0)) {
        const std::string error = last_error_message("SetHandleInformation stdin");
        cleanup_partial();
        throw std::runtime_error(error);
    }

    STARTUPINFOA startup{};
    startup.cb = sizeof(startup);
    startup.dwFlags = STARTF_USESTDHANDLES;
    startup.hStdOutput = stdout_write;
    startup.hStdError = stdout_write;
    startup.hStdInput = stdin_read;

    PROCESS_INFORMATION process{};
    std::string command = command_line({exe_path, "--server"});
    std::vector<char> command_buffer(command.begin(), command.end());
    command_buffer.push_back('\0');
    const std::string cwd = worker_cwd_path().string();

    const BOOL created = CreateProcessA(
        nullptr,
        command_buffer.data(),
        nullptr,
        nullptr,
        TRUE,
        CREATE_NO_WINDOW,
        nullptr,
        cwd.empty() ? nullptr : cwd.c_str(),
        &startup,
        &process);
    CloseHandle(stdin_read);
    stdin_read = nullptr;
    CloseHandle(stdout_write);
    stdout_write = nullptr;

    if (!created) {
        const std::string error = last_error_message("CreateProcess");
        cleanup_partial();
        throw std::runtime_error(error + ". Check the SPC3 executable path.");
    }

    g_app.worker_process = process;
    g_app.worker_stdin = stdin_write;
    g_app.worker_stdout = stdout_read;
    g_app.worker_exe_path = exe_path;
    g_app.worker_active = true;
}

bool append_worker_output_until_done(DWORD& exit_code) {
    std::string pending;
    std::array<char, 4096> buffer{};
    DWORD bytes_read = 0;
    while (ReadFile(g_app.worker_stdout, buffer.data(), static_cast<DWORD>(buffer.size()), &bytes_read, nullptr) &&
           bytes_read > 0) {
        pending.append(buffer.data(), bytes_read);
        for (;;) {
            const size_t newline = pending.find('\n');
            if (newline == std::string::npos) {
                break;
            }
            std::string line = pending.substr(0, newline);
            pending.erase(0, newline + 1);
            if (!line.empty() && line.back() == '\r') {
                line.pop_back();
            }
            constexpr const char* marker = "SPC3_SERVER_DONE exit_code=";
            const size_t marker_len = std::strlen(marker);
            if (line.rfind(marker, 0) == 0) {
                exit_code = static_cast<DWORD>(std::strtoul(line.c_str() + marker_len, nullptr, 10));
                if (!pending.empty()) {
                    post_output(pending);
                }
                return true;
            }
            post_output(line + "\n");
        }
    }
    return false;
}

void worker_thread(RunRequest request) {
    DWORD exit_code = 1;
    try {
        const std::string exe_path = request.args.empty() ? std::string{} : request.args.front();
        {
            std::lock_guard<std::mutex> lock(g_app.process_mutex);
            if (g_app.cancel_requested) {
                throw std::runtime_error("run canceled before worker startup");
            }
        }
        ensure_worker_process(exe_path);
        const std::string line = server_run_line(request.args);
        DWORD bytes_written = 0;
        {
            std::lock_guard<std::mutex> lock(g_app.process_mutex);
            if (g_app.cancel_requested) {
                close_worker_locked();
                throw std::runtime_error("run canceled before command dispatch");
            }
            g_app.worker_command_active = true;
            if (!WriteFile(
                    g_app.worker_stdin,
                    line.data(),
                    static_cast<DWORD>(line.size()),
                    &bytes_written,
                    nullptr) ||
                bytes_written != line.size()) {
                throw std::runtime_error(last_error_message("WriteFile worker stdin"));
            }
        }
        if (!append_worker_output_until_done(exit_code)) {
            throw std::runtime_error("SPC3 worker exited before returning a completion marker");
        }
        {
            std::lock_guard<std::mutex> lock(g_app.process_mutex);
            g_app.worker_command_active = false;
        }
    } catch (const std::exception& error) {
        post_output(std::string("worker error: ") + error.what() + "\n");
        std::lock_guard<std::mutex> lock(g_app.process_mutex);
        close_worker_locked();
        exit_code = 1;
    }
    {
        std::lock_guard<std::mutex> lock(g_app.process_mutex);
        g_app.process_active = false;
        g_app.worker_command_active = false;
        g_app.cancel_requested = false;
    }

    post_output("\nexit_code=" + std::to_string(exit_code) + "\n");
    post_output(build_report_view(request.report_path, request.compare_report_path));
    post_run_finished(exit_code);
}

HWND make_label(HWND parent, const char* text, int x, int y, int w, int h) {
    HWND hwnd = CreateWindowExA(0, "STATIC", text, WS_CHILD | WS_VISIBLE, x, y, w, h, parent, nullptr, nullptr, nullptr);
    apply_dark_control_theme(hwnd);
    return hwnd;
}

HWND make_edit(HWND parent, int id, const std::string& value, int x, int y, int w, int h) {
    HWND hwnd = CreateWindowExA(
        WS_EX_CLIENTEDGE,
        "EDIT",
        value.c_str(),
        WS_CHILD | WS_VISIBLE | WS_TABSTOP | ES_AUTOHSCROLL,
        x,
        y,
        w,
        h,
        parent,
        reinterpret_cast<HMENU>(id),
        nullptr,
        nullptr);
    apply_dark_control_theme(hwnd);
    return hwnd;
}

HWND make_combo(HWND parent, int id, int x, int y, int w, int h) {
    HWND hwnd = CreateWindowExA(
        0,
        "COMBOBOX",
        "",
        WS_CHILD | WS_VISIBLE | WS_TABSTOP | WS_VSCROLL | CBS_DROPDOWNLIST,
        x,
        y,
        w,
        h,
        parent,
        reinterpret_cast<HMENU>(id),
        nullptr,
        nullptr);
    apply_dark_control_theme(hwnd);
    return hwnd;
}

void fill_value_combo(HWND combo, const std::vector<ValueChoice>& choices, bool include_no_change = true) {
    SendMessageA(combo, CB_RESETCONTENT, 0, 0);
    if (include_no_change) {
        add_no_change_item(combo);
    }
    for (const ValueChoice& choice : choices) {
        add_combo_item(combo, choice.label.empty() ? choice.name : choice.label, choice.value);
    }
    SendMessageA(combo, CB_SETCURSEL, 0, 0);
}

bool location_visible_for_origin(const LocationChoice& choice, std::optional<int> origin_game) {
    if (!origin_game) {
        return true;
    }
    switch (*origin_game) {
    case 1:
    case 2:
        return choice.ruby_sapphire;
    case 3:
        return choice.emerald;
    case 4:
    case 5:
        return choice.fire_red_leaf_green;
    default:
        return true;
    }
}

void reload_met_location_combo() {
    if (!g_app.set_met_location) {
        return;
    }
    const std::optional<int> previous = selected_combo_value(g_app.set_met_location);
    SendMessageA(g_app.set_met_location, CB_RESETCONTENT, 0, 0);
    add_no_change_item(g_app.set_met_location);
    const std::optional<int> origin = selected_combo_value(g_app.set_origin_game);
    int selected_index = 0;
    int index = 1;
    for (const LocationChoice& choice : location_choices()) {
        if (!location_visible_for_origin(choice, origin)) {
            continue;
        }
        add_combo_item(g_app.set_met_location, choice.label, choice.value);
        if (previous && *previous == choice.value) {
            selected_index = index;
        }
        ++index;
    }
    SendMessageA(g_app.set_met_location, CB_SETCURSEL, selected_index, 0);
}

HWND make_check(HWND parent, int id, const char* text, bool is_checked, int x, int y, int w, int h) {
    HWND hwnd = CreateWindowExA(
        0,
        "BUTTON",
        text,
        WS_CHILD | WS_VISIBLE | WS_TABSTOP | BS_OWNERDRAW,
        x,
        y,
        w,
        h,
        parent,
        reinterpret_cast<HMENU>(id),
        nullptr,
        nullptr);
    SendMessageA(hwnd, BM_SETCHECK, is_checked ? BST_CHECKED : BST_UNCHECKED, 0);
    apply_dark_control_theme(hwnd);
    return hwnd;
}

HWND make_radio(HWND parent, int id, const char* text, bool is_checked, bool starts_group, int x, int y, int w, int h) {
    DWORD style = WS_CHILD | WS_VISIBLE | WS_TABSTOP | BS_OWNERDRAW;
    if (starts_group) {
        style |= WS_GROUP;
    }
    HWND hwnd = CreateWindowExA(
        0,
        "BUTTON",
        text,
        style,
        x,
        y,
        w,
        h,
        parent,
        reinterpret_cast<HMENU>(id),
        nullptr,
        nullptr);
    SendMessageA(hwnd, BM_SETCHECK, is_checked ? BST_CHECKED : BST_UNCHECKED, 0);
    apply_dark_control_theme(hwnd);
    return hwnd;
}

HWND make_dark_button(HWND parent, int id, const char* text, int x, int y, int w, int h, bool default_button = false) {
    DWORD style = WS_CHILD | WS_VISIBLE | WS_TABSTOP | BS_OWNERDRAW;
    if (default_button) {
        style |= BS_DEFPUSHBUTTON;
    }
    HWND hwnd = CreateWindowExA(
        0,
        "BUTTON",
        text,
        style,
        x,
        y,
        w,
        h,
        parent,
        reinterpret_cast<HMENU>(id),
        nullptr,
        nullptr);
    apply_dark_control_theme(hwnd);
    return hwnd;
}

bool is_mode_radio_id(int id) {
    return id == IdModeVerify || id == IdModePack || id == IdModeConsolidate || id == IdModeInspect || id == IdModeUnpack;
}

bool is_lane_select_radio_id(int id) {
    return id == IdLaneAll || id == IdLaneOne || id == IdLaneRange;
}

bool is_pk3_state_radio_id(int id) {
    return id == IdPk3StateEgg || id == IdPk3StateHatchedShiny || id == IdPk3StateHatchedNotShiny;
}

bool is_move_editor_combo_id(int id) {
    return (id >= IdMove1 && id <= IdMove4) || (id >= IdPpUp1 && id <= IdPpUp4);
}

bool is_check_option_id(int id) {
    return id == IdTyped || id == IdGpu || id == IdInternalOnly || id == IdExternalPredictor || id == IdNoEntropyProbe;
}

bool is_dark_option_id(int id) {
    return is_mode_radio_id(id) || is_lane_select_radio_id(id) || is_pk3_state_radio_id(id) || is_check_option_id(id);
}

HWND option_hwnd(int id) {
    switch (id) {
    case IdModeVerify:
        return g_app.mode_verify;
    case IdModePack:
        return g_app.mode_pack;
    case IdModeConsolidate:
        return g_app.mode_consolidate;
    case IdModeInspect:
        return g_app.mode_inspect;
    case IdModeUnpack:
        return g_app.mode_unpack;
    case IdTyped:
        return g_app.typed;
    case IdGpu:
        return g_app.gpu;
    case IdInternalOnly:
        return g_app.internal_only;
    case IdExternalPredictor:
        return g_app.external_predictor;
    case IdNoEntropyProbe:
        return g_app.no_entropy_probe;
    case IdLaneAll:
        return g_app.lane_all;
    case IdLaneOne:
        return g_app.lane_one;
    case IdLaneRange:
        return g_app.lane_range;
    case IdPk3StateEgg:
        return g_app.pk3_state_egg;
    case IdPk3StateHatchedShiny:
        return g_app.pk3_state_hatched_shiny;
    case IdPk3StateHatchedNotShiny:
        return g_app.pk3_state_hatched_not_shiny;
    default:
        return nullptr;
    }
}

void set_button_checked(HWND hwnd, bool is_checked) {
    if (!hwnd) {
        return;
    }
    if (is_checked) {
        if (hwnd == g_app.mode_verify) {
            g_app.selected_mode_id = IdModeVerify;
        } else if (hwnd == g_app.mode_pack) {
            g_app.selected_mode_id = IdModePack;
        } else if (hwnd == g_app.mode_consolidate) {
            g_app.selected_mode_id = IdModeConsolidate;
        } else if (hwnd == g_app.mode_inspect) {
            g_app.selected_mode_id = IdModeInspect;
        } else if (hwnd == g_app.mode_unpack) {
            g_app.selected_mode_id = IdModeUnpack;
        } else if (hwnd == g_app.lane_all) {
            g_app.selected_lane_select_id = IdLaneAll;
        } else if (hwnd == g_app.lane_one) {
            g_app.selected_lane_select_id = IdLaneOne;
        } else if (hwnd == g_app.lane_range) {
            g_app.selected_lane_select_id = IdLaneRange;
        } else if (hwnd == g_app.pk3_state_egg) {
            g_app.selected_pk3_state_id = IdPk3StateEgg;
        } else if (hwnd == g_app.pk3_state_hatched_shiny) {
            g_app.selected_pk3_state_id = IdPk3StateHatchedShiny;
        } else if (hwnd == g_app.pk3_state_hatched_not_shiny) {
            g_app.selected_pk3_state_id = IdPk3StateHatchedNotShiny;
        }
    }
    if (hwnd == g_app.typed) {
        g_app.typed_checked = is_checked;
    } else if (hwnd == g_app.gpu) {
        g_app.gpu_checked = is_checked;
    } else if (hwnd == g_app.internal_only) {
        g_app.internal_only_checked = is_checked;
    } else if (hwnd == g_app.external_predictor) {
        g_app.external_predictor_checked = is_checked;
    } else if (hwnd == g_app.no_entropy_probe) {
        g_app.no_entropy_probe_checked = is_checked;
    }
    SendMessageA(hwnd, BM_SETCHECK, is_checked ? BST_CHECKED : BST_UNCHECKED, 0);
    InvalidateRect(hwnd, nullptr, TRUE);
}

void select_mode_radio(int id) {
    g_app.selected_mode_id = id;
    set_button_checked(g_app.mode_verify, id == IdModeVerify);
    set_button_checked(g_app.mode_pack, id == IdModePack);
    set_button_checked(g_app.mode_consolidate, id == IdModeConsolidate);
    set_button_checked(g_app.mode_inspect, id == IdModeInspect);
    set_button_checked(g_app.mode_unpack, id == IdModeUnpack);
}

void select_lane_radio(int id) {
    g_app.selected_lane_select_id = id;
    set_button_checked(g_app.lane_all, id == IdLaneAll);
    set_button_checked(g_app.lane_one, id == IdLaneOne);
    set_button_checked(g_app.lane_range, id == IdLaneRange);
}

void select_pk3_state_radio(int id) {
    g_app.selected_pk3_state_id = id;
    set_button_checked(g_app.pk3_state_egg, id == IdPk3StateEgg);
    set_button_checked(g_app.pk3_state_hatched_shiny, id == IdPk3StateHatchedShiny);
    set_button_checked(g_app.pk3_state_hatched_not_shiny, id == IdPk3StateHatchedNotShiny);
}

void toggle_check_option(int id) {
    HWND hwnd = option_hwnd(id);
    if (!hwnd) {
        return;
    }
    set_button_checked(hwnd, !checked(hwnd));
}

HWND make_dark_selector(HWND parent, int id, DarkSelectorState& selector, const std::vector<std::string>& items, int selected, int x, int y, int w, int h) {
    selector.items = items;
    if (selector.items.empty() || selected < 0) {
        selector.selected = 0;
    } else if (selected >= static_cast<int>(selector.items.size())) {
        selector.selected = static_cast<int>(selector.items.size()) - 1;
    } else {
        selector.selected = selected;
    }
    selector.control_id = id;
    selector.hover = -1;
    selector.popup = nullptr;
    HWND hwnd = CreateWindowExA(
        0,
        kDarkSelectorClass,
        "",
        WS_CHILD | WS_VISIBLE | WS_TABSTOP,
        x,
        y,
        w,
        h,
        parent,
        reinterpret_cast<HMENU>(id),
        nullptr,
        &selector);
    selector.hwnd = hwnd;
    apply_dark_control_theme(hwnd);
    return hwnd;
}

bool is_combo_box_control(HWND control) {
    if (!control) {
        return false;
    }
    char class_name[32] = {};
    return GetClassNameA(control, class_name, static_cast<int>(sizeof(class_name))) != 0 &&
        std::strcmp(class_name, "ComboBox") == 0;
}

int row_layout_height(HWND control, int row_height) {
    return is_combo_box_control(control) ? 240 : row_height;
}

void layout_controls(HWND hwnd, int width, int height) {
    width = std::max(width, kMinWindowWidth);
    height = std::max(height, kMinWindowHeight);
    const int margin = 10;
    const int label_w = 112;
    const int edit_x = margin + label_w;
    const int browse_w = 34;
    const int gap = 6;
    const int edit_w = std::max(180, width - edit_x - margin - browse_w - gap);
    const int browse_x = edit_x + edit_w + gap;
    const int row_h = 24;
    const std::string mode = selected_mode();
    const bool verify = mode == "verify";
    const bool pack = mode == "pack";
    const bool consolidate = mode == "consolidate";
    const bool inspect = mode == "inspect";
    const bool unpack = mode == "unpack";
    const std::string pack_level = get_combo_text(g_app.level);
    const bool pack_level_zero = pack && pack_level == "0";
    const bool pack_level_three = pack && pack_level == "3";
    const bool source_compare = verify && !checked(g_app.internal_only);
    const bool lane_one = unpack && checked(g_app.lane_one);
    const bool lane_range = unpack && checked(g_app.lane_range);
    const bool hatched_pk3_state = unpack && !checked(g_app.pk3_state_egg);
    const bool show_extra_settings = unpack && g_app.extra_settings_open;
    int y = margin;

    const auto move = [](HWND control, int x, int y, int w, int h) {
        MoveWindow(control, x, y, w, h, TRUE);
    };
    const auto show = [](HWND control, bool visible) {
        if (!control) {
            return;
        }
        if (!visible) {
            if (control == g_app.level) {
                hide_selector_popup(&g_level_selector);
            } else if (control == g_app.profile) {
                hide_selector_popup(&g_profile_selector);
            }
        }
        ShowWindow(control, visible ? SW_SHOW : SW_HIDE);
        EnableWindow(control, visible ? TRUE : FALSE);
    };
    const auto show_path_row = [&](HWND label, HWND edit, HWND browse, bool visible) {
        show(label, visible);
        show(edit, visible);
        show(browse, visible);
        if (visible) {
            move(label, margin, y + 4, label_w, row_h);
            move(edit, edit_x, y, edit_w, row_h);
            move(browse, browse_x, y, browse_w, row_h);
            y += 28;
        }
    };
    const auto show_option = [&](HWND control, bool visible, int x, int w) {
        show(control, visible);
        if (visible) {
            move(control, x, y, w, row_h);
        }
    };
    const auto show_label_control = [&](HWND label, HWND control, bool visible, int label_x, int label_w_local, int control_x, int control_w, int control_h) {
        show(label, visible);
        show(control, visible);
        if (visible) {
            move(label, label_x, y + 4, label_w_local, row_h);
            move(control, control_x, y, control_w, control_h);
        }
    };
    const auto show_extra_pair = [&](HWND label_a, HWND edit_a, HWND label_b, HWND edit_b, bool visible) {
        const int label_a_w = 140;
        const int edit_a_x = margin + label_a_w;
        const int edit_a_w = std::max(140, (width - margin * 2 - label_a_w * 2 - 32) / 2);
        const int label_b_x = edit_a_x + edit_a_w + 18;
        const int edit_b_x = label_b_x + label_a_w;
        const int edit_b_w = std::max(120, width - edit_b_x - margin);
        show(label_a, visible);
        show(edit_a, visible);
        show(label_b, visible);
        show(edit_b, visible);
        if (visible) {
            move(label_a, margin, y + 4, label_a_w, row_h);
            move(edit_a, edit_a_x, y, edit_a_w, row_layout_height(edit_a, row_h));
            if (label_b && edit_b) {
                move(label_b, label_b_x, y + 4, label_a_w, row_h);
                move(edit_b, edit_b_x, y, edit_b_w, row_layout_height(edit_b, row_h));
            }
            y += 28;
        }
    };
    const auto show_move_row = [&](size_t slot, bool visible) {
        show(g_app.move_label[slot], visible);
        show(g_app.move_combo[slot], visible);
        show(g_app.pp_up_label[slot], visible);
        show(g_app.pp_up_combo[slot], visible);
        show(g_app.pp_label[slot], visible);
        show(g_app.pp_value[slot], visible);
        if (visible) {
            const int move_label_w = 64;
            const int move_x = margin + move_label_w;
            const int pp_up_label_w = 54;
            const int pp_up_w = 56;
            const int pp_label_w = 28;
            const int pp_w = 54;
            const int pp_x = width - margin - pp_w;
            const int pp_label_x = pp_x - pp_label_w - 6;
            const int pp_up_x = pp_label_x - pp_up_w - 12;
            const int pp_up_label_x = pp_up_x - pp_up_label_w - 6;
            const int move_w = std::max(180, pp_up_label_x - move_x - 12);
            move(g_app.move_label[slot], margin, y + 4, move_label_w, row_h);
            move(g_app.move_combo[slot], move_x, y, move_w, 260);
            move(g_app.pp_up_label[slot], pp_up_label_x, y + 4, pp_up_label_w, row_h);
            move(g_app.pp_up_combo[slot], pp_up_x, y, pp_up_w, 120);
            move(g_app.pp_label[slot], pp_label_x, y + 4, pp_label_w, row_h);
            move(g_app.pp_value[slot], pp_x, y, pp_w, row_h);
            y += 28;
        }
    };
    const auto show_stat_row = [&](HWND label, const std::array<HWND, 6>& combos, bool visible) {
        show(label, visible);
        for (HWND combo : combos) {
            show(combo, visible);
        }
        if (visible) {
            const int row_label_w = 92;
            const int combo_x = margin + row_label_w;
            const int combo_gap = 6;
            const int combo_w = std::max(74, (width - combo_x - margin - combo_gap * 5) / 6);
            move(label, margin, y + 4, row_label_w, row_h);
            for (size_t i = 0; i < combos.size(); ++i) {
                move(combos[i], combo_x + static_cast<int>(i) * (combo_w + combo_gap), y, combo_w, 180);
            }
            y += 28;
        }
    };
    const auto show_pokerus_row = [&](bool visible) {
        show(g_app.set_pokerus_label, visible);
        show(g_app.set_pokerus_strain_label, visible);
        show(g_app.set_pokerus_strain, visible);
        show(g_app.set_pokerus_days_label, visible);
        show(g_app.set_pokerus_days, visible);
        if (visible) {
            move(g_app.set_pokerus_label, margin, y + 4, 92, row_h);
            move(g_app.set_pokerus_strain_label, margin + 100, y + 4, 52, row_h);
            move(g_app.set_pokerus_strain, margin + 154, y, 128, 160);
            move(g_app.set_pokerus_days_label, margin + 294, y + 4, 42, row_h);
            move(g_app.set_pokerus_days, margin + 338, y, 112, 150);
            y += 28;
        }
    };

    move(g_app.mode_label, margin, y + 4, label_w, row_h);
    move(g_app.mode_verify, edit_x, y, 72, row_h);
    move(g_app.mode_pack, edit_x + 78, y, 62, row_h);
    move(g_app.mode_consolidate, edit_x + 146, y, 104, row_h);
    move(g_app.mode_inspect, edit_x + 258, y, 80, row_h);
    move(g_app.mode_unpack, edit_x + 346, y, 78, row_h);
    y += 34;

    show_path_row(g_app.exe_label, g_app.exe, g_app.browse_exe, true);
    show_path_row(g_app.input_label, g_app.input, g_app.browse_input, verify || inspect || unpack);
    show_path_row(g_app.output_label, g_app.output, g_app.browse_output, pack || consolidate);
    show_path_row(g_app.root_label, g_app.root, g_app.browse_root, pack || source_compare);
    show_path_row(g_app.consolidate_root_label, g_app.consolidate_root, g_app.browse_consolidate_root, consolidate);
    show_path_row(g_app.predictor_label, g_app.predictor, g_app.browse_predictor, pack || verify || unpack);
    SetWindowTextA(g_app.unpack_dir_label, unpack ? "Output ZIP dir" : "Unpack dir");
    show_path_row(g_app.unpack_dir_label, g_app.unpack_dir, g_app.browse_unpack_dir, unpack);
    if (unpack) {
        show(g_app.pk3_state_label, true);
        show(g_app.pk3_state_egg, true);
        show(g_app.pk3_state_hatched_shiny, true);
        show(g_app.pk3_state_hatched_not_shiny, true);
        move(g_app.pk3_state_label, margin, y + 4, label_w, row_h);
        move(g_app.pk3_state_egg, edit_x, y, 64, row_h);
        move(g_app.pk3_state_hatched_shiny, edit_x + 74, y, 120, row_h);
        move(g_app.pk3_state_hatched_not_shiny, edit_x + 204, y, 150, row_h);
        y += 28;
    } else {
        show(g_app.pk3_state_label, false);
        show(g_app.pk3_state_egg, false);
        show(g_app.pk3_state_hatched_shiny, false);
        show(g_app.pk3_state_hatched_not_shiny, false);
    }
    show_path_row(g_app.trainer_index_label, g_app.trainer_index, g_app.browse_trainer_index, hatched_pk3_state);
    show(g_app.extra_settings, unpack);
    if (unpack) {
        SetWindowTextA(g_app.extra_settings, g_app.extra_settings_open ? "Extra settings -" : "Extra settings ...");
        move(g_app.extra_settings, edit_x, y, 150, row_h + 4);
        y += 34;
    }
    show_extra_pair(g_app.set_nickname_label, g_app.set_nickname, g_app.set_ot_name_label, g_app.set_ot_name, show_extra_settings);
    for (size_t i = 0; i < 4; ++i) {
        show_move_row(i, show_extra_settings);
    }
    show_extra_pair(g_app.set_experience_label, g_app.set_experience, g_app.set_held_item_label, g_app.set_held_item, show_extra_settings);
    show_extra_pair(g_app.set_friendship_label, g_app.set_friendship, g_app.set_ball_label, g_app.set_ball, show_extra_settings);
    show_extra_pair(g_app.set_origin_game_label, g_app.set_origin_game, g_app.set_met_location_label, g_app.set_met_location, show_extra_settings);
    show_extra_pair(g_app.set_met_level_label, g_app.set_met_level, g_app.set_ot_gender_label, g_app.set_ot_gender, show_extra_settings);
    show_extra_pair(g_app.set_language_label, g_app.set_language, g_app.set_ability_number_label, g_app.set_ability_number, show_extra_settings);
    show_pokerus_row(show_extra_settings);
    show_stat_row(g_app.set_ivs_label, g_app.iv_combo, show_extra_settings);
    show_stat_row(g_app.set_evs_label, g_app.ev_combo, show_extra_settings);
    show_stat_row(g_app.set_contest_label, g_app.contest_combo, show_extra_settings);
    show_path_row(g_app.report_label, g_app.report, g_app.browse_report, true);
    show_path_row(g_app.compare_report_label, g_app.compare_report, g_app.browse_compare_report, true);
    y += 32;

    show_label_control(g_app.limit_label, g_app.limit, pack, margin, 54, 72, 88, row_h);
    show_label_control(g_app.level_label, g_app.level, pack, 174, 42, 220, 70, row_h);
    show_label_control(g_app.profile_label, g_app.profile, pack && !pack_level_zero, 306, 52, 360, 100, row_h);
    if (pack) {
        y += 34;
    }

    int option_x = margin;
    show_option(g_app.typed, pack_level_three, option_x, 110);
    option_x += pack_level_three ? 122 : 0;
    show_option(g_app.gpu, verify || unpack, option_x, 90);
    option_x += (verify || unpack) ? 104 : 0;
    show_option(g_app.internal_only, verify, option_x, 130);
    option_x += verify ? 144 : 0;
    show_option(g_app.external_predictor, pack_level_three, option_x, 140);
    option_x += pack_level_three ? 154 : 0;
    show_option(g_app.no_entropy_probe, pack, option_x, 135);
    if (unpack) {
        y += 34;
        show(g_app.lane_select_label, true);
        move(g_app.lane_select_label, margin, y + 4, label_w, row_h);
        show_option(g_app.lane_all, true, edit_x, 92);
        show_option(g_app.lane_one, true, edit_x + 100, 92);
        show_option(g_app.lane_range, true, edit_x + 200, 100);
        y += 32;
        show_label_control(g_app.lane_value_label, g_app.lane_value, lane_one, margin, 54, 72, 88, row_h);
        show_label_control(g_app.lane_from_label, g_app.lane_from, lane_range, 174, 42, 220, 88, row_h);
        show_label_control(g_app.lane_to_label, g_app.lane_to, lane_range, 326, 28, 360, 88, row_h);
        if (lane_one || lane_range) {
            y += 34;
        }
    } else {
        show(g_app.lane_select_label, false);
        show(g_app.lane_all, false);
        show(g_app.lane_one, false);
        show(g_app.lane_range, false);
        show(g_app.lane_value_label, false);
        show(g_app.lane_value, false);
        show(g_app.lane_from_label, false);
        show(g_app.lane_from, false);
        show(g_app.lane_to_label, false);
        show(g_app.lane_to, false);
    }
    move(g_app.summary, width - 282, y, 82, 28);
    move(g_app.run, width - 190, y, 82, 28);
    move(g_app.cancel, width - 100, y, 82, 28);
    y += 40;

    move(g_app.console, margin, y, width - margin * 2, std::max(80, height - y - margin));
    (void)hwnd;
}

void draw_dark_button(const DRAWITEMSTRUCT* item) {
    ensure_theme_resources();
    const bool pressed = (item->itemState & ODS_SELECTED) != 0;
    const bool disabled = (item->itemState & ODS_DISABLED) != 0;
    HBRUSH fill = pressed ? g_button_pressed_brush : g_button_brush;
    FillRect(item->hDC, &item->rcItem, fill);
    FrameRect(item->hDC, &item->rcItem, g_border_brush);

    char text[64] = {};
    GetWindowTextA(item->hwndItem, text, static_cast<int>(sizeof(text)));
    SetBkMode(item->hDC, TRANSPARENT);
    SetTextColor(item->hDC, disabled ? kDisabledText : kText);
    RECT text_rect = item->rcItem;
    if (pressed) {
        OffsetRect(&text_rect, 1, 1);
    }
    DrawTextA(item->hDC, text, -1, &text_rect, DT_CENTER | DT_VCENTER | DT_SINGLELINE);

    if ((item->itemState & ODS_FOCUS) != 0 && !disabled) {
        RECT focus = item->rcItem;
        InflateRect(&focus, -4, -4);
        HBRUSH accent = CreateSolidBrush(kAccent);
        FrameRect(item->hDC, &focus, accent);
        DeleteObject(accent);
    }
}

void draw_dark_option(const DRAWITEMSTRUCT* item) {
    ensure_theme_resources();
    const bool disabled = (item->itemState & ODS_DISABLED) != 0;
    const bool pressed = (item->itemState & ODS_SELECTED) != 0;
    const bool is_radio = is_mode_radio_id(static_cast<int>(item->CtlID)) ||
        is_lane_select_radio_id(static_cast<int>(item->CtlID)) ||
        is_pk3_state_radio_id(static_cast<int>(item->CtlID));
    const bool is_checked = checked(item->hwndItem);

    FillRect(item->hDC, &item->rcItem, g_window_brush);
    RECT mark{};
    mark.left = item->rcItem.left + 2;
    mark.top = item->rcItem.top + (item->rcItem.bottom - item->rcItem.top - 14) / 2;
    mark.right = mark.left + 14;
    mark.bottom = mark.top + 14;
    if (pressed) {
        OffsetRect(&mark, 1, 1);
    }

    HPEN border_pen = CreatePen(PS_SOLID, 1, disabled ? kDisabledText : kBorder);
    HGDIOBJ old_pen = SelectObject(item->hDC, border_pen);
    HGDIOBJ old_brush = SelectObject(item->hDC, g_edit_brush);
    if (is_radio) {
        Ellipse(item->hDC, mark.left, mark.top, mark.right, mark.bottom);
    } else {
        Rectangle(item->hDC, mark.left, mark.top, mark.right, mark.bottom);
    }
    SelectObject(item->hDC, old_brush);
    SelectObject(item->hDC, old_pen);
    DeleteObject(border_pen);

    if (is_checked) {
        HPEN check_pen = CreatePen(PS_SOLID, 2, disabled ? kDisabledText : kAccent);
        old_pen = SelectObject(item->hDC, check_pen);
        if (is_radio) {
            HBRUSH accent = CreateSolidBrush(disabled ? kDisabledText : kAccent);
            old_brush = SelectObject(item->hDC, accent);
            Ellipse(item->hDC, mark.left + 4, mark.top + 4, mark.right - 4, mark.bottom - 4);
            SelectObject(item->hDC, old_brush);
            DeleteObject(accent);
        } else {
            MoveToEx(item->hDC, mark.left + 3, mark.top + 7, nullptr);
            LineTo(item->hDC, mark.left + 6, mark.top + 10);
            LineTo(item->hDC, mark.right - 3, mark.top + 3);
        }
        SelectObject(item->hDC, old_pen);
        DeleteObject(check_pen);
    }

    char text[96] = {};
    GetWindowTextA(item->hwndItem, text, static_cast<int>(sizeof(text)));
    SetBkMode(item->hDC, TRANSPARENT);
    SetTextColor(item->hDC, disabled ? kDisabledText : kText);
    RECT text_rect = item->rcItem;
    text_rect.left += 22;
    if (pressed) {
        OffsetRect(&text_rect, 1, 1);
    }
    DrawTextA(item->hDC, text, -1, &text_rect, DT_LEFT | DT_VCENTER | DT_SINGLELINE);

    if ((item->itemState & ODS_FOCUS) != 0 && !disabled) {
        RECT focus = item->rcItem;
        InflateRect(&focus, -2, -2);
        HPEN focus_pen = CreatePen(PS_SOLID, 1, kAccent);
        old_pen = SelectObject(item->hDC, focus_pen);
        HGDIOBJ hollow = GetStockObject(HOLLOW_BRUSH);
        old_brush = SelectObject(item->hDC, hollow);
        Rectangle(item->hDC, focus.left, focus.top, focus.right, focus.bottom);
        SelectObject(item->hDC, old_brush);
        SelectObject(item->hDC, old_pen);
        DeleteObject(focus_pen);
    }
}

void draw_dark_selector_face(HWND hwnd, HDC dc, const DarkSelectorState* selector) {
    ensure_theme_resources();
    RECT rect{};
    GetClientRect(hwnd, &rect);
    const bool enabled = IsWindowEnabled(hwnd) != FALSE;
    FillRect(dc, &rect, g_edit_brush);
    FrameRect(dc, &rect, g_border_brush);

    RECT text_rect = rect;
    text_rect.left += 8;
    text_rect.right -= 26;
    SetBkMode(dc, TRANSPARENT);
    SetTextColor(dc, enabled ? kText : kDisabledText);
    std::string text = selector_text(selector);
    DrawTextA(dc, text.c_str(), -1, &text_rect, DT_LEFT | DT_VCENTER | DT_SINGLELINE | DT_END_ELLIPSIS);

    HPEN separator = CreatePen(PS_SOLID, 1, kBorder);
    HGDIOBJ old_pen = SelectObject(dc, separator);
    MoveToEx(dc, rect.right - 24, rect.top + 2, nullptr);
    LineTo(dc, rect.right - 24, rect.bottom - 2);
    SelectObject(dc, old_pen);
    DeleteObject(separator);

    POINT arrow[3] = {
        {rect.right - 17, rect.top + (rect.bottom - rect.top) / 2 - 2},
        {rect.right - 7, rect.top + (rect.bottom - rect.top) / 2 - 2},
        {rect.right - 12, rect.top + (rect.bottom - rect.top) / 2 + 4},
    };
    HBRUSH arrow_brush = CreateSolidBrush(enabled ? kText : kDisabledText);
    HGDIOBJ old_brush = SelectObject(dc, arrow_brush);
    old_pen = SelectObject(dc, GetStockObject(NULL_PEN));
    Polygon(dc, arrow, 3);
    SelectObject(dc, old_pen);
    SelectObject(dc, old_brush);
    DeleteObject(arrow_brush);

    if (GetFocus() == hwnd && enabled) {
        RECT focus = rect;
        InflateRect(&focus, -3, -3);
        HPEN focus_pen = CreatePen(PS_SOLID, 1, kAccent);
        old_pen = SelectObject(dc, focus_pen);
        old_brush = SelectObject(dc, GetStockObject(HOLLOW_BRUSH));
        Rectangle(dc, focus.left, focus.top, focus.right, focus.bottom);
        SelectObject(dc, old_brush);
        SelectObject(dc, old_pen);
        DeleteObject(focus_pen);
    }
}

void draw_dark_selector_popup(HWND hwnd, HDC dc, const DarkSelectorState* selector) {
    ensure_theme_resources();
    RECT rect{};
    GetClientRect(hwnd, &rect);
    FillRect(dc, &rect, g_edit_brush);
    FrameRect(dc, &rect, g_border_brush);
    SetBkMode(dc, TRANSPARENT);

    if (!selector) {
        return;
    }
    for (int index = 0; index < static_cast<int>(selector->items.size()); ++index) {
        RECT item_rect{1, 1 + index * kSelectorItemHeight, rect.right - 1, 1 + (index + 1) * kSelectorItemHeight};
        if (index == selector->hover) {
            FillRect(dc, &item_rect, g_selector_hover_brush);
        } else if (index == selector->selected) {
            FillRect(dc, &item_rect, g_selector_selected_brush);
        }
        RECT text_rect = item_rect;
        text_rect.left += 8;
        SetTextColor(dc, kText);
        DrawTextA(dc, selector->items[static_cast<size_t>(index)].c_str(), -1, &text_rect, DT_LEFT | DT_VCENTER | DT_SINGLELINE | DT_END_ELLIPSIS);
    }
}

void hide_selector_popup(DarkSelectorState* selector) {
    if (!selector || !selector->popup) {
        return;
    }
    if (GetCapture() == selector->popup) {
        ReleaseCapture();
    }
    ShowWindow(selector->popup, SW_HIDE);
    selector->hover = -1;
}

void notify_selector_changed(const DarkSelectorState* selector) {
    if (!selector || !selector->hwnd) {
        return;
    }
    HWND parent = GetParent(selector->hwnd);
    SendMessageA(parent, WM_COMMAND, MAKEWPARAM(selector->control_id, CBN_SELCHANGE), reinterpret_cast<LPARAM>(selector->hwnd));
}

void select_dark_selector_item(DarkSelectorState* selector, int index) {
    if (!selector || index < 0 || index >= static_cast<int>(selector->items.size())) {
        return;
    }
    if (selector->selected != index) {
        selector->selected = index;
        InvalidateRect(selector->hwnd, nullptr, TRUE);
        notify_selector_changed(selector);
    }
}

void show_selector_popup(DarkSelectorState* selector) {
    if (!selector || !selector->hwnd || selector->items.empty()) {
        return;
    }
    if (selector->popup && IsWindowVisible(selector->popup)) {
        hide_selector_popup(selector);
        return;
    }
    if (selector != &g_level_selector) {
        hide_selector_popup(&g_level_selector);
    }
    if (selector != &g_profile_selector) {
        hide_selector_popup(&g_profile_selector);
    }

    RECT anchor{};
    GetWindowRect(selector->hwnd, &anchor);
    const int width = anchor.right - anchor.left;
    const int height = 2 + static_cast<int>(selector->items.size()) * kSelectorItemHeight;
    if (!selector->popup) {
        HINSTANCE instance = reinterpret_cast<HINSTANCE>(GetWindowLongPtrA(g_app.window, GWLP_HINSTANCE));
        selector->popup = CreateWindowExA(
            WS_EX_TOOLWINDOW | WS_EX_TOPMOST,
            kDarkSelectorPopupClass,
            "",
            WS_POPUP,
            anchor.left,
            anchor.bottom,
            width,
            height,
            g_app.window,
            nullptr,
            instance,
            selector);
        if (!selector->popup) {
            return;
        }
        SetWindowLongPtrA(selector->popup, GWLP_USERDATA, reinterpret_cast<LONG_PTR>(selector));
    }
    selector->hover = selector->selected;
    SetWindowPos(selector->popup, HWND_TOPMOST, anchor.left, anchor.bottom, width, height, SWP_SHOWWINDOW);
    SetFocus(selector->popup);
    SetCapture(selector->popup);
    InvalidateRect(selector->popup, nullptr, TRUE);
}

LRESULT CALLBACK dark_selector_proc(HWND hwnd, UINT message, WPARAM wparam, LPARAM lparam) {
    DarkSelectorState* selector = reinterpret_cast<DarkSelectorState*>(GetWindowLongPtrA(hwnd, GWLP_USERDATA));
    if (message == WM_NCCREATE) {
        auto* create = reinterpret_cast<CREATESTRUCTA*>(lparam);
        SetWindowLongPtrA(hwnd, GWLP_USERDATA, reinterpret_cast<LONG_PTR>(create->lpCreateParams));
        return TRUE;
    }
    switch (message) {
    case WM_PAINT: {
        PAINTSTRUCT ps{};
        HDC dc = BeginPaint(hwnd, &ps);
        draw_dark_selector_face(hwnd, dc, selector);
        EndPaint(hwnd, &ps);
        return 0;
    }
    case WM_LBUTTONDOWN:
        SetFocus(hwnd);
        show_selector_popup(selector);
        return 0;
    case WM_KEYDOWN:
        if (wparam == VK_RETURN || wparam == VK_SPACE || wparam == VK_DOWN) {
            show_selector_popup(selector);
            return 0;
        }
        if (wparam == VK_UP && selector && !selector->items.empty()) {
            int next = selector->selected - 1;
            if (next < 0) {
                next = static_cast<int>(selector->items.size()) - 1;
            }
            select_dark_selector_item(selector, next);
            return 0;
        }
        break;
    case WM_SETFOCUS:
    case WM_KILLFOCUS:
    case WM_ENABLE:
        InvalidateRect(hwnd, nullptr, TRUE);
        return 0;
    default:
        break;
    }
    return DefWindowProcA(hwnd, message, wparam, lparam);
}

LRESULT CALLBACK dark_selector_popup_proc(HWND hwnd, UINT message, WPARAM wparam, LPARAM lparam) {
    DarkSelectorState* selector = reinterpret_cast<DarkSelectorState*>(GetWindowLongPtrA(hwnd, GWLP_USERDATA));
    if (message == WM_NCCREATE) {
        auto* create = reinterpret_cast<CREATESTRUCTA*>(lparam);
        SetWindowLongPtrA(hwnd, GWLP_USERDATA, reinterpret_cast<LONG_PTR>(create->lpCreateParams));
        return TRUE;
    }
    switch (message) {
    case WM_PAINT: {
        PAINTSTRUCT ps{};
        HDC dc = BeginPaint(hwnd, &ps);
        draw_dark_selector_popup(hwnd, dc, selector);
        EndPaint(hwnd, &ps);
        return 0;
    }
    case WM_MOUSEMOVE:
    case WM_LBUTTONDOWN:
    case WM_LBUTTONUP: {
        POINT point{static_cast<short>(LOWORD(lparam)), static_cast<short>(HIWORD(lparam))};
        RECT rect{};
        GetClientRect(hwnd, &rect);
        if (!PtInRect(&rect, point)) {
            if (message == WM_LBUTTONDOWN || message == WM_LBUTTONUP) {
                hide_selector_popup(selector);
            }
            return 0;
        }
        int hover = (point.y - 1) / kSelectorItemHeight;
        if (!selector || hover < 0 || hover >= static_cast<int>(selector->items.size())) {
            hover = -1;
        }
        if (selector && selector->hover != hover) {
            selector->hover = hover;
            InvalidateRect(hwnd, nullptr, TRUE);
        }
        if (message == WM_LBUTTONDOWN && selector && hover >= 0) {
            select_dark_selector_item(selector, hover);
            hide_selector_popup(selector);
            SetFocus(selector->hwnd);
        }
        return 0;
    }
    case WM_KEYDOWN:
        if (!selector) {
            return 0;
        }
        if (wparam == VK_ESCAPE) {
            hide_selector_popup(selector);
            SetFocus(selector->hwnd);
            return 0;
        }
        if (wparam == VK_RETURN || wparam == VK_SPACE) {
            if (selector->hover >= 0) {
                select_dark_selector_item(selector, selector->hover);
            }
            hide_selector_popup(selector);
            SetFocus(selector->hwnd);
            return 0;
        }
        if (wparam == VK_UP || wparam == VK_DOWN) {
            int hover = selector->hover;
            if (hover < 0) {
                hover = selector->selected;
            }
            hover += (wparam == VK_DOWN) ? 1 : -1;
            if (hover < 0) {
                hover = static_cast<int>(selector->items.size()) - 1;
            } else if (hover >= static_cast<int>(selector->items.size())) {
                hover = 0;
            }
            selector->hover = hover;
            InvalidateRect(hwnd, nullptr, TRUE);
            return 0;
        }
        return 0;
    case WM_KILLFOCUS:
        hide_selector_popup(selector);
        return 0;
    default:
        break;
    }
    return DefWindowProcA(hwnd, message, wparam, lparam);
}

HBRUSH color_control(WPARAM wparam, LPARAM lparam, UINT message) {
    ensure_theme_resources();
    HDC dc = reinterpret_cast<HDC>(wparam);
    HWND control = reinterpret_cast<HWND>(lparam);
    SetBkMode(dc, OPAQUE);
    SetTextColor(dc, kText);

    if (control == g_app.console) {
        SetBkColor(dc, kConsoleBg);
        return g_console_brush;
    }
    if (message == WM_CTLCOLOREDIT || message == WM_CTLCOLORLISTBOX) {
        SetBkColor(dc, kEditBg);
        return g_edit_brush;
    }
    if (message == WM_CTLCOLORBTN || message == WM_CTLCOLORSTATIC) {
        SetBkColor(dc, kWindowBg);
        return g_window_brush;
    }
    SetBkColor(dc, kWindowBg);
    return g_window_brush;
}

void create_ui(HWND hwnd) {
    enable_dark_title_bar(hwnd);
    g_app.selected_mode_id = IdModeVerify;
    g_app.selected_lane_select_id = IdLaneAll;
    g_app.selected_pk3_state_id = IdPk3StateEgg;
    g_app.typed_checked = true;
    g_app.gpu_checked = true;
    g_app.internal_only_checked = true;
    g_app.external_predictor_checked = false;
    g_app.no_entropy_probe_checked = true;
    g_app.extra_settings_open = false;
    g_app.mode_label = make_label(hwnd, "Mode", 10, 12, 100, 22);
    g_app.mode_verify = make_radio(hwnd, IdModeVerify, "Verify", true, true, 122, 10, 72, 24);
    g_app.mode_pack = make_radio(hwnd, IdModePack, "Pack", false, false, 200, 10, 62, 24);
    g_app.mode_consolidate = make_radio(hwnd, IdModeConsolidate, "Consolidate", false, false, 268, 10, 104, 24);
    g_app.mode_inspect = make_radio(hwnd, IdModeInspect, "Inspect", false, false, 380, 10, 80, 24);
    g_app.mode_unpack = make_radio(hwnd, IdModeUnpack, "Unpack", false, false, 468, 10, 78, 24);

    int y = 42;
    g_app.exe_label = make_label(hwnd, "SPC3 exe", 10, y + 4, 100, 22);
    g_app.exe = make_edit(hwnd, IdExe, default_exe_path(), 122, y, 780, 24);
    g_app.browse_exe = make_dark_button(hwnd, IdBrowseExe, "...", 906, y, 34, 24);
    y += 28;
    g_app.input_label = make_label(hwnd, "Input .spc3", 10, y + 4, 100, 22);
    g_app.input = make_edit(hwnd, IdInput, workspace_path("Helper-PC-Artifacts\\helper_full_corpus_65536.spc3"), 122, y, 780, 24);
    g_app.browse_input = make_dark_button(hwnd, IdBrowseInput, "...", 906, y, 34, 24);
    y += 28;
    g_app.output_label = make_label(hwnd, "Output .spc3", 10, y + 4, 100, 22);
    g_app.output = make_edit(hwnd, IdOutput, workspace_path("Helper-PC-Artifacts\\helper_full_corpus_65536.spc3"), 122, y, 780, 24);
    g_app.browse_output = make_dark_button(hwnd, IdBrowseOutput, "...", 906, y, 34, 24);
    y += 28;
    g_app.root_label = make_label(hwnd, "Lane ZIP root", 10, y + 4, 100, 22);
    g_app.root = make_edit(hwnd, IdRoot, workspace_path("Helper-PC-Artifacts\\full_corpus_consolidate_20260511_154934"), 122, y, 780, 24);
    g_app.browse_root = make_dark_button(hwnd, IdBrowseRoot, "...", 906, y, 34, 24);
    y += 28;
    g_app.consolidate_root_label = make_label(hwnd, "SPC3 shard root", 10, y + 4, 106, 22);
    g_app.consolidate_root = make_edit(hwnd, IdConsolidateRoot, workspace_path("Helper-PC-Artifacts\\full_corpus_consolidate_20260511_154934"), 122, y, 780, 24);
    g_app.browse_consolidate_root = make_dark_button(hwnd, IdBrowseConsolidateRoot, "...", 906, y, 34, 24);
    y += 28;
    g_app.predictor_label = make_label(hwnd, "Predictor JSON", 10, y + 4, 100, 22);
    g_app.predictor = make_edit(hwnd, IdPredictor, workspace_path("Phase3SpindaBlocks\\_phase3_pid_second_half_iv_reference.json"), 122, y, 780, 24);
    g_app.browse_predictor = make_dark_button(hwnd, IdBrowsePredictor, "...", 906, y, 34, 24);
    y += 28;
    g_app.unpack_dir_label = make_label(hwnd, "Output ZIP dir", 10, y + 4, 100, 22);
    g_app.unpack_dir = make_edit(hwnd, IdUnpackDir, workspace_path("Helper-PC-Artifacts\\full_corpus_consolidate_20260511_154934"), 122, y, 780, 24);
    g_app.browse_unpack_dir = make_dark_button(hwnd, IdBrowseUnpackDir, "...", 906, y, 34, 24);
    y += 28;
    g_app.pk3_state_label = make_label(hwnd, "PK3 state", 10, y + 4, 100, 22);
    g_app.pk3_state_egg = make_radio(hwnd, IdPk3StateEgg, "Egg", true, true, 122, y, 64, 24);
    g_app.pk3_state_hatched_shiny = make_radio(hwnd, IdPk3StateHatchedShiny, "Hatched shiny", false, false, 196, y, 120, 24);
    g_app.pk3_state_hatched_not_shiny = make_radio(hwnd, IdPk3StateHatchedNotShiny, "Hatched not shiny", false, false, 326, y, 150, 24);
    y += 28;
    g_app.trainer_index_label = make_label(hwnd, "Trainer index", 10, y + 4, 100, 22);
    g_app.trainer_index = make_edit(hwnd, IdTrainerIndex, workspace_path("TSVs\\_spinda_tsv_trainer_index_tid_0x0000.json"), 122, y, 780, 24);
    g_app.browse_trainer_index = make_dark_button(hwnd, IdBrowseTrainerIndex, "...", 906, y, 34, 24);
    y += 28;
    g_app.extra_settings = make_dark_button(hwnd, IdExtraSettings, "Extra settings ...", 122, y, 150, 28);
    y += 28;
    g_app.set_nickname_label = make_label(hwnd, "Nickname", 10, y + 4, 110, 22);
    g_app.set_nickname = make_edit(hwnd, IdSetNickname, "", 122, y, 260, 24);
    g_app.set_ot_name_label = make_label(hwnd, "OT name", 410, y + 4, 110, 22);
    g_app.set_ot_name = make_edit(hwnd, IdSetOtName, "", 522, y, 260, 24);
    y += 28;
    const std::array<int, 4> move_ids{IdMove1, IdMove2, IdMove3, IdMove4};
    const std::array<int, 4> pp_up_ids{IdPpUp1, IdPpUp2, IdPpUp3, IdPpUp4};
    const std::array<int, 4> pp_ids{IdMovePp1, IdMovePp2, IdMovePp3, IdMovePp4};
    const std::vector<MoveChoice>& moves = move_choices();
    for (size_t i = 0; i < 4; ++i) {
        const std::string move_label = "Move " + std::to_string(i + 1);
        g_app.move_label[i] = make_label(hwnd, move_label.c_str(), 10, y + 4, 64, 22);
        g_app.move_combo[i] = make_combo(hwnd, move_ids[i], 82, y, 260, 240);
        for (const MoveChoice& move_choice : moves) {
            const LRESULT item = SendMessageA(g_app.move_combo[i], CB_ADDSTRING, 0, reinterpret_cast<LPARAM>(move_choice.label.c_str()));
            if (item != CB_ERR && item != CB_ERRSPACE) {
                const uint32_t packed = static_cast<uint32_t>(move_choice.id) |
                    (static_cast<uint32_t>(move_choice.pp) << 16);
                SendMessageA(g_app.move_combo[i], CB_SETITEMDATA, static_cast<WPARAM>(item), static_cast<LPARAM>(packed));
            }
        }
        SendMessageA(g_app.move_combo[i], CB_SETCURSEL, 0, 0);

        g_app.pp_up_label[i] = make_label(hwnd, "PP Ups", 410, y + 4, 54, 22);
        g_app.pp_up_combo[i] = make_combo(hwnd, pp_up_ids[i], 468, y, 56, 120);
        for (int value = 0; value <= 3; ++value) {
            const std::string text = std::to_string(value);
            SendMessageA(g_app.pp_up_combo[i], CB_ADDSTRING, 0, reinterpret_cast<LPARAM>(text.c_str()));
        }
        SendMessageA(g_app.pp_up_combo[i], CB_SETCURSEL, 0, 0);

        g_app.pp_label[i] = make_label(hwnd, "PP", 540, y + 4, 28, 22);
        g_app.pp_value[i] = make_edit(hwnd, pp_ids[i], "0", 572, y, 54, 24);
        SendMessageA(g_app.pp_value[i], EM_SETREADONLY, TRUE, 0);
        y += 28;
    }
    g_app.set_experience_label = make_label(hwnd, "Level", 410, y + 4, 110, 22);
    g_app.set_experience = make_combo(hwnd, IdSetExperience, 522, y, 260, 220);
    fill_value_combo(g_app.set_experience, spinda_level_choices());
    g_app.set_held_item_label = make_label(hwnd, "Held item", 10, y + 4, 110, 22);
    g_app.set_held_item = make_combo(hwnd, IdSetHeldItem, 122, y, 260, 260);
    fill_value_combo(g_app.set_held_item, held_item_choices());
    y += 28;
    g_app.set_friendship_label = make_label(hwnd, "Friendship", 10, y + 4, 110, 22);
    g_app.set_friendship = make_combo(hwnd, IdSetFriendship, 122, y, 260, 220);
    fill_value_combo(g_app.set_friendship, simple_numeric_choices(0, 255));
    g_app.set_ball_label = make_label(hwnd, "Ball", 410, y + 4, 110, 22);
    g_app.set_ball = make_combo(hwnd, IdSetBall, 522, y, 260, 220);
    fill_value_combo(g_app.set_ball, ball_choices());
    y += 28;
    g_app.set_origin_game_label = make_label(hwnd, "Origin game", 10, y + 4, 110, 22);
    g_app.set_origin_game = make_combo(hwnd, IdSetOriginGame, 122, y, 260, 170);
    fill_value_combo(g_app.set_origin_game, origin_game_choices());
    g_app.set_met_location_label = make_label(hwnd, "Met location", 410, y + 4, 110, 22);
    g_app.set_met_location = make_combo(hwnd, IdSetMetLocation, 522, y, 260, 260);
    reload_met_location_combo();
    y += 28;
    g_app.set_met_level_label = make_label(hwnd, "Met level", 10, y + 4, 110, 22);
    g_app.set_met_level = make_combo(hwnd, IdSetMetLevel, 122, y, 260, 220);
    fill_value_combo(g_app.set_met_level, simple_numeric_choices(0, 100));
    g_app.set_ot_gender_label = make_label(hwnd, "OT gender", 410, y + 4, 110, 22);
    g_app.set_ot_gender = make_combo(hwnd, IdSetOtGender, 522, y, 260, 120);
    fill_value_combo(g_app.set_ot_gender, ot_gender_choices());
    y += 28;
    g_app.set_language_label = make_label(hwnd, "Language", 410, y + 4, 110, 22);
    g_app.set_language = make_combo(hwnd, IdSetLanguage, 522, y, 260, 220);
    fill_value_combo(g_app.set_language, language_choices());
    g_app.set_ability_number_label = make_label(hwnd, "Ability slot", 10, y + 4, 110, 22);
    g_app.set_ability_number = make_combo(hwnd, IdSetAbilityNumber, 122, y, 260, 120);
    fill_value_combo(g_app.set_ability_number, ability_slot_choices());
    y += 28;
    g_app.set_pokerus_label = make_label(hwnd, "Pokerus", 10, y + 4, 82, 22);
    g_app.set_pokerus_strain_label = make_label(hwnd, "Strain", 110, y + 4, 52, 22);
    g_app.set_pokerus_strain = make_combo(hwnd, IdPokerusStrain, 164, y, 120, 160);
    fill_value_combo(g_app.set_pokerus_strain, simple_numeric_choices(0, 15));
    g_app.set_pokerus_days_label = make_label(hwnd, "Days", 300, y + 4, 42, 22);
    g_app.set_pokerus_days = make_combo(hwnd, IdPokerusDays, 344, y, 120, 150);
    fill_value_combo(g_app.set_pokerus_days, simple_numeric_choices(0, 4));
    y += 28;
    const std::array<int, 6> ev_ids{IdEvHp, IdEvAtk, IdEvDef, IdEvSpa, IdEvSpd, IdEvSpe};
    const std::array<int, 6> iv_ids{IdIvHp, IdIvAtk, IdIvDef, IdIvSpa, IdIvSpd, IdIvSpe};
    const std::array<int, 6> contest_ids{IdContestCool, IdContestBeauty, IdContestCute, IdContestSmart, IdContestTough, IdContestFeel};
    const std::array<const char*, 6> stat_prefixes{"HP", "Atk", "Def", "SpA", "SpD", "Spe"};
    const std::array<const char*, 6> contest_prefixes{"Cool", "Beauty", "Cute", "Smart", "Tough", "Feel"};
    g_app.set_ivs_label = make_label(hwnd, "IVs", 10, y + 4, 90, 22);
    for (size_t i = 0; i < g_app.iv_combo.size(); ++i) {
        g_app.iv_combo[i] = make_combo(hwnd, iv_ids[i], 122 + static_cast<int>(i) * 78, y, 74, 180);
        fill_value_combo(g_app.iv_combo[i], simple_numeric_choices(0, 31, stat_prefixes[i]));
    }
    y += 28;
    g_app.set_evs_label = make_label(hwnd, "EVs", 10, y + 4, 90, 22);
    for (size_t i = 0; i < g_app.ev_combo.size(); ++i) {
        g_app.ev_combo[i] = make_combo(hwnd, ev_ids[i], 122 + static_cast<int>(i) * 78, y, 74, 220);
        fill_value_combo(g_app.ev_combo[i], simple_numeric_choices(0, 255, stat_prefixes[i]));
    }
    y += 28;
    g_app.set_contest_label = make_label(hwnd, "Contest", 10, y + 4, 90, 22);
    for (size_t i = 0; i < g_app.contest_combo.size(); ++i) {
        g_app.contest_combo[i] = make_combo(hwnd, contest_ids[i], 122 + static_cast<int>(i) * 78, y, 74, 220);
        fill_value_combo(g_app.contest_combo[i], simple_numeric_choices(0, 255, contest_prefixes[i]));
    }
    y += 28;
    g_app.report_label = make_label(hwnd, "Report JSON", 10, y + 4, 100, 22);
    g_app.report = make_edit(hwnd, IdReport, workspace_path("Helper-PC-Artifacts\\helper_full_corpus_65536_cpu_internal_verify_report.json"), 122, y, 780, 24);
    g_app.browse_report = make_dark_button(hwnd, IdBrowseReport, "...", 906, y, 34, 24);
    y += 28;
    g_app.compare_report_label = make_label(hwnd, "Compare JSON", 10, y + 4, 100, 22);
    g_app.compare_report = make_edit(hwnd, IdCompareReport, "", 122, y, 780, 24);
    g_app.browse_compare_report = make_dark_button(hwnd, IdBrowseCompareReport, "...", 906, y, 34, 24);

    g_app.limit_label = make_label(hwnd, "Limit", 10, 266, 54, 22);
    g_app.limit = make_edit(hwnd, IdLimit, "65536", 72, 262, 88, 24);
    g_app.level_label = make_label(hwnd, "Level", 174, 266, 42, 22);
    g_app.level = make_dark_selector(hwnd, IdLevel, g_level_selector, {"0", "1", "2", "3"}, 3, 220, 262, 70, 24);
    g_app.profile_label = make_label(hwnd, "Profile", 306, 266, 52, 22);
    g_app.profile = make_dark_selector(hwnd, IdProfile, g_profile_selector, {"fast", "auto", "compat", "small"}, 0, 360, 262, 100, 24);

    g_app.typed = make_check(hwnd, IdTyped, "Typed v0.2", true, 10, 296, 110, 24);
    g_app.gpu = make_check(hwnd, IdGpu, "Use GPU", true, 132, 296, 90, 24);
    g_app.internal_only = make_check(hwnd, IdInternalOnly, "Internal only", true, 236, 296, 130, 24);
    g_app.external_predictor = make_check(hwnd, IdExternalPredictor, "External predictor", false, 380, 296, 140, 24);
    g_app.no_entropy_probe = make_check(hwnd, IdNoEntropyProbe, "No entropy probe", true, 536, 296, 135, 24);
    g_app.lane_select_label = make_label(hwnd, "Lanes", 10, 326, 100, 22);
    g_app.lane_all = make_radio(hwnd, IdLaneAll, "All lanes", true, true, 122, 322, 92, 24);
    g_app.lane_one = make_radio(hwnd, IdLaneOne, "One lane", false, false, 222, 322, 92, 24);
    g_app.lane_range = make_radio(hwnd, IdLaneRange, "Range", false, false, 322, 322, 100, 24);
    g_app.lane_value_label = make_label(hwnd, "Lane", 10, 358, 54, 22);
    g_app.lane_value = make_edit(hwnd, IdLaneValue, "0001", 72, 354, 88, 24);
    g_app.lane_from_label = make_label(hwnd, "From", 174, 358, 42, 22);
    g_app.lane_from = make_edit(hwnd, IdLaneFrom, "0001", 220, 354, 88, 24);
    g_app.lane_to_label = make_label(hwnd, "To", 326, 358, 28, 22);
    g_app.lane_to = make_edit(hwnd, IdLaneTo, "FFFF", 360, 354, 88, 24);
    g_app.summary = CreateWindowExA(0, "BUTTON", "Summary", WS_CHILD | WS_VISIBLE | WS_TABSTOP | BS_OWNERDRAW,
        632, 296, 82, 28, hwnd, reinterpret_cast<HMENU>(IdSummary), nullptr, nullptr);
    g_app.run = CreateWindowExA(0, "BUTTON", "Run", WS_CHILD | WS_VISIBLE | WS_TABSTOP | BS_DEFPUSHBUTTON | BS_OWNERDRAW,
        722, 296, 82, 28, hwnd, reinterpret_cast<HMENU>(IdRun), nullptr, nullptr);
    g_app.cancel = CreateWindowExA(0, "BUTTON", "Cancel", WS_CHILD | WS_VISIBLE | WS_TABSTOP | BS_OWNERDRAW,
        812, 296, 82, 28, hwnd, reinterpret_cast<HMENU>(IdCancel), nullptr, nullptr);
    apply_dark_control_theme(g_app.summary);
    apply_dark_control_theme(g_app.run);
    apply_dark_control_theme(g_app.cancel);
    EnableWindow(g_app.cancel, FALSE);

    g_app.console = CreateWindowExA(
        WS_EX_CLIENTEDGE,
        "EDIT",
        "",
        WS_CHILD | WS_VISIBLE | WS_VSCROLL | WS_HSCROLL | ES_LEFT | ES_MULTILINE | ES_AUTOVSCROLL | ES_AUTOHSCROLL | ES_READONLY,
        10,
        336,
        884,
        300,
        hwnd,
        reinterpret_cast<HMENU>(IdConsole),
        nullptr,
        nullptr);
    apply_dark_control_theme(g_app.console);

    HFONT font = static_cast<HFONT>(GetStockObject(DEFAULT_GUI_FONT));
    for (HWND control : {
              g_app.mode_label, g_app.mode_verify, g_app.mode_pack, g_app.mode_consolidate, g_app.mode_inspect,
              g_app.mode_unpack, g_app.exe_label, g_app.input_label, g_app.output_label, g_app.root_label,
              g_app.consolidate_root_label, g_app.predictor_label, g_app.unpack_dir_label, g_app.report_label,
              g_app.compare_report_label, g_app.pk3_state_label, g_app.trainer_index_label,
              g_app.set_nickname_label, g_app.set_ot_name_label,
              g_app.set_evs_label, g_app.set_ivs_label, g_app.set_contest_label,
              g_app.set_held_item_label, g_app.set_experience_label, g_app.set_friendship_label,
              g_app.set_pokerus_label, g_app.set_pokerus_strain_label, g_app.set_pokerus_days_label,
              g_app.set_met_location_label, g_app.set_met_level_label,
              g_app.set_origin_game_label, g_app.set_ball_label, g_app.set_ot_gender_label,
              g_app.set_language_label, g_app.set_ability_number_label,
              g_app.limit_label, g_app.level_label, g_app.profile_label,
              g_app.exe, g_app.input, g_app.output, g_app.root, g_app.consolidate_root,
              g_app.predictor, g_app.unpack_dir, g_app.trainer_index, g_app.report, g_app.compare_report,
              g_app.set_nickname, g_app.set_ot_name,
              g_app.set_held_item, g_app.set_experience,
              g_app.set_friendship, g_app.set_pokerus_strain, g_app.set_pokerus_days,
              g_app.set_met_location, g_app.set_met_level,
              g_app.set_origin_game, g_app.set_ball, g_app.set_ot_gender, g_app.set_language,
              g_app.set_ability_number,
              g_app.limit, g_app.level, g_app.profile,
              g_app.typed, g_app.gpu, g_app.internal_only, g_app.external_predictor, g_app.no_entropy_probe,
              g_app.lane_select_label, g_app.lane_all, g_app.lane_one, g_app.lane_range,
              g_app.lane_value_label, g_app.lane_value, g_app.lane_from_label, g_app.lane_from,
              g_app.lane_to_label, g_app.lane_to,
              g_app.pk3_state_egg, g_app.pk3_state_hatched_shiny, g_app.pk3_state_hatched_not_shiny,
              g_app.summary, g_app.run, g_app.cancel, g_app.extra_settings, g_app.console, g_app.browse_exe, g_app.browse_input,
              g_app.browse_output, g_app.browse_root, g_app.browse_consolidate_root,
              g_app.browse_predictor, g_app.browse_unpack_dir, g_app.browse_report,
              g_app.browse_compare_report, g_app.browse_trainer_index}) {
        SendMessageA(control, WM_SETFONT, reinterpret_cast<WPARAM>(font), TRUE);
    }
    for (size_t i = 0; i < 4; ++i) {
        for (HWND control : {g_app.move_label[i], g_app.move_combo[i], g_app.pp_up_label[i],
                 g_app.pp_up_combo[i], g_app.pp_label[i], g_app.pp_value[i]}) {
            SendMessageA(control, WM_SETFONT, reinterpret_cast<WPARAM>(font), TRUE);
        }
    }
    for (size_t i = 0; i < 6; ++i) {
        for (HWND control : {g_app.ev_combo[i], g_app.iv_combo[i], g_app.contest_combo[i]}) {
            SendMessageA(control, WM_SETFONT, reinterpret_cast<WPARAM>(font), TRUE);
        }
    }
    update_move_pp_fields();
    RECT rect{};
    GetClientRect(hwnd, &rect);
    layout_controls(hwnd, rect.right - rect.left, rect.bottom - rect.top);
}

void run_command_from_ui() {
    try {
        RunRequest request;
        request.args = build_args();
        request.report_path = migrate_full_corpus_path(get_window_text(g_app.report));
        request.compare_report_path = migrate_full_corpus_path(get_window_text(g_app.compare_report));
        SetWindowTextA(g_app.report, request.report_path.c_str());
        SetWindowTextA(g_app.compare_report, request.compare_report_path.c_str());
        SetWindowTextA(g_app.console, "");
        append_console("> " + command_line(request.args) + "\r\n\r\n");
        {
            std::lock_guard<std::mutex> lock(g_app.process_mutex);
            g_app.cancel_requested = false;
            g_app.process_active = true;
        }
        set_running(true);
        try {
            std::thread(worker_thread, std::move(request)).detach();
        } catch (...) {
            std::lock_guard<std::mutex> lock(g_app.process_mutex);
            g_app.process_active = false;
            g_app.cancel_requested = false;
            throw;
        }
    } catch (const std::exception& error) {
        set_running(false);
        MessageBoxA(g_app.window, error.what(), "SPC3", MB_ICONERROR | MB_OK);
    }
}

void summarize_from_ui() {
    SetWindowTextA(g_app.console, "");
    append_console(build_report_view(
        migrate_full_corpus_path(get_window_text(g_app.report)),
        migrate_full_corpus_path(get_window_text(g_app.compare_report))));
}

void cancel_process() {
    std::lock_guard<std::mutex> lock(g_app.process_mutex);
    if (g_app.process_active) {
        g_app.cancel_requested = true;
        close_worker_locked();
        append_console("\r\ncancel requested\r\n");
    }
}

bool is_owner_draw_button_id(WPARAM id) {
    switch (id) {
    case IdSummary:
    case IdRun:
    case IdCancel:
    case IdBrowseExe:
    case IdBrowseInput:
    case IdBrowseOutput:
    case IdBrowseRoot:
    case IdBrowseConsolidateRoot:
    case IdBrowsePredictor:
    case IdBrowseUnpackDir:
    case IdBrowseReport:
    case IdBrowseCompareReport:
    case IdBrowseTrainerIndex:
    case IdExtraSettings:
        return true;
    default:
        return false;
    }
}

void browse_from_ui(int id) {
    static const char exe_filter[] = "Executable files (*.exe)\0*.exe\0All files (*.*)\0*.*\0";
    static const char spc3_filter[] = "SPC3 files (*.spc3)\0*.spc3\0All files (*.*)\0*.*\0";
    static const char json_filter[] = "JSON files (*.json)\0*.json\0All files (*.*)\0*.*\0";

    switch (id) {
    case IdBrowseExe:
        choose_file_path(g_app.window, g_app.exe, "Select SPC3 executable", exe_filter, "exe", false);
        return;
    case IdBrowseInput:
        choose_file_path(g_app.window, g_app.input, "Select input SPC3 file", spc3_filter, "spc3", false);
        return;
    case IdBrowseOutput:
        choose_file_path(g_app.window, g_app.output, "Select output SPC3 file", spc3_filter, "spc3", true);
        return;
    case IdBrowseRoot:
        choose_folder_path(g_app.window, g_app.root, "Select lane ZIP root folder");
        return;
    case IdBrowseConsolidateRoot:
        choose_folder_path(g_app.window, g_app.consolidate_root, "Select SPC3 shard root folder");
        return;
    case IdBrowsePredictor:
        choose_file_path(g_app.window, g_app.predictor, "Select predictor JSON file", json_filter, "json", false);
        return;
    case IdBrowseTrainerIndex:
        choose_file_path(g_app.window, g_app.trainer_index, "Select TSV trainer index JSON file", json_filter, "json", false);
        return;
    case IdBrowseUnpackDir:
        choose_folder_path(g_app.window, g_app.unpack_dir, "Select output ZIP folder");
        return;
    case IdBrowseReport:
        choose_file_path(g_app.window, g_app.report, "Select report JSON file", json_filter, "json", true);
        return;
    case IdBrowseCompareReport:
        choose_file_path(g_app.window, g_app.compare_report, "Select report JSON file to compare", json_filter, "json", false);
        return;
    default:
        return;
    }
}

bool register_dark_selector_classes(HINSTANCE instance) {
    static bool registered = false;
    if (registered) {
        return true;
    }

    WNDCLASSA selector{};
    selector.lpfnWndProc = dark_selector_proc;
    selector.hInstance = instance;
    selector.lpszClassName = kDarkSelectorClass;
    selector.hCursor = LoadCursor(nullptr, IDC_ARROW);
    selector.hbrBackground = g_edit_brush;
    if (!RegisterClassA(&selector) && GetLastError() != ERROR_CLASS_ALREADY_EXISTS) {
        return false;
    }

    WNDCLASSA popup{};
    popup.lpfnWndProc = dark_selector_popup_proc;
    popup.hInstance = instance;
    popup.lpszClassName = kDarkSelectorPopupClass;
    popup.hCursor = LoadCursor(nullptr, IDC_ARROW);
    popup.hbrBackground = g_edit_brush;
    if (!RegisterClassA(&popup) && GetLastError() != ERROR_CLASS_ALREADY_EXISTS) {
        return false;
    }

    registered = true;
    return true;
}

LRESULT CALLBACK window_proc(HWND hwnd, UINT message, WPARAM wparam, LPARAM lparam) {
    switch (message) {
    case WM_CREATE:
        g_app.window = hwnd;
        g_window_alive.store(true, std::memory_order_release);
        create_ui(hwnd);
        return 0;
    case WM_ERASEBKGND: {
        ensure_theme_resources();
        RECT rect{};
        GetClientRect(hwnd, &rect);
        FillRect(reinterpret_cast<HDC>(wparam), &rect, g_window_brush);
        return 1;
    }
    case WM_GETMINMAXINFO: {
        auto* info = reinterpret_cast<MINMAXINFO*>(lparam);
        info->ptMinTrackSize.x = kMinWindowWidth;
        info->ptMinTrackSize.y = kMinWindowHeight;
        return 0;
    }
    case WM_SIZE:
        layout_controls(hwnd, LOWORD(lparam), HIWORD(lparam));
        return 0;
    case WM_CTLCOLORSTATIC:
    case WM_CTLCOLOREDIT:
    case WM_CTLCOLORLISTBOX:
    case WM_CTLCOLORBTN:
        return reinterpret_cast<LRESULT>(color_control(wparam, lparam, message));
    case WM_DRAWITEM:
        if (is_owner_draw_button_id(wparam)) {
            draw_dark_button(reinterpret_cast<DRAWITEMSTRUCT*>(lparam));
            return TRUE;
        }
        if (is_dark_option_id(static_cast<int>(wparam))) {
            draw_dark_option(reinterpret_cast<DRAWITEMSTRUCT*>(lparam));
            return TRUE;
        }
        return DefWindowProcA(hwnd, message, wparam, lparam);
    case WM_COMMAND:
        if (is_dark_option_id(LOWORD(wparam)) && HIWORD(wparam) == BN_CLICKED) {
            const int id = LOWORD(wparam);
            if (is_mode_radio_id(id)) {
                select_mode_radio(id);
            } else if (is_lane_select_radio_id(id)) {
                select_lane_radio(id);
            } else if (is_pk3_state_radio_id(id)) {
                select_pk3_state_radio(id);
            } else {
                toggle_check_option(id);
            }
            RECT rect{};
            GetClientRect(hwnd, &rect);
            layout_controls(hwnd, rect.right - rect.left, rect.bottom - rect.top);
            InvalidateRect(hwnd, nullptr, TRUE);
            return 0;
        }
        if (is_move_editor_combo_id(LOWORD(wparam)) && HIWORD(wparam) == CBN_SELCHANGE) {
            update_move_pp_fields();
            return 0;
        }
        if (LOWORD(wparam) == IdSetOriginGame && HIWORD(wparam) == CBN_SELCHANGE) {
            reload_met_location_combo();
            return 0;
        }
        if (LOWORD(wparam) == IdLevel && HIWORD(wparam) == CBN_SELCHANGE) {
            RECT rect{};
            GetClientRect(hwnd, &rect);
            layout_controls(hwnd, rect.right - rect.left, rect.bottom - rect.top);
            InvalidateRect(hwnd, nullptr, TRUE);
            return 0;
        }
        if (LOWORD(wparam) == IdExtraSettings && HIWORD(wparam) == BN_CLICKED) {
            g_app.extra_settings_open = !g_app.extra_settings_open;
            RECT rect{};
            GetClientRect(hwnd, &rect);
            layout_controls(hwnd, rect.right - rect.left, rect.bottom - rect.top);
            InvalidateRect(hwnd, nullptr, TRUE);
            return 0;
        }
        if (LOWORD(wparam) == IdRun && HIWORD(wparam) == BN_CLICKED) {
            run_command_from_ui();
            return 0;
        }
        if (LOWORD(wparam) == IdSummary && HIWORD(wparam) == BN_CLICKED) {
            summarize_from_ui();
            return 0;
        }
        if (LOWORD(wparam) == IdCancel && HIWORD(wparam) == BN_CLICKED) {
            cancel_process();
            return 0;
        }
        if (is_owner_draw_button_id(LOWORD(wparam)) && HIWORD(wparam) == BN_CLICKED) {
            browse_from_ui(LOWORD(wparam));
            return 0;
        }
        return 0;
    case kAppendOutput: {
        std::string* text = reinterpret_cast<std::string*>(lparam);
        append_console(*text);
        delete text;
        return 0;
    }
    case kRunFinished:
        set_running(false);
        return 0;
    case WM_DESTROY:
        g_window_alive.store(false, std::memory_order_release);
        {
            std::lock_guard<std::mutex> lock(g_app.process_mutex);
            close_worker_locked();
        }
        release_theme_resources();
        PostQuitMessage(0);
        return 0;
    default:
        return DefWindowProcA(hwnd, message, wparam, lparam);
    }
}

} // namespace

int WINAPI WinMain(HINSTANCE instance, HINSTANCE, LPSTR, int show) {
    HRESULT ole_result = OleInitialize(nullptr);
    const bool ole_initialized = SUCCEEDED(ole_result);
    ensure_theme_resources();
    if (!register_dark_selector_classes(instance)) {
        release_theme_resources();
        if (ole_initialized) {
            OleUninitialize();
        }
        return 1;
    }
    const char* class_name = "SPC3NativeVerifierGui";
    WNDCLASSA wc{};
    wc.lpfnWndProc = window_proc;
    wc.hInstance = instance;
    wc.lpszClassName = class_name;
    wc.hCursor = LoadCursor(nullptr, IDC_ARROW);
    wc.hbrBackground = g_window_brush;
    RegisterClassA(&wc);

    HWND hwnd = CreateWindowExA(
        0,
        class_name,
        "SPC3 Native Compressor",
        WS_OVERLAPPEDWINDOW,
        CW_USEDEFAULT,
        CW_USEDEFAULT,
        980,
        720,
        nullptr,
        nullptr,
        instance,
        nullptr);
    if (!hwnd) {
        release_theme_resources();
        if (ole_initialized) {
            OleUninitialize();
        }
        return 1;
    }

    ShowWindow(hwnd, show);
    UpdateWindow(hwnd);

    MSG msg{};
    while (GetMessageA(&msg, nullptr, 0, 0) > 0) {
        TranslateMessage(&msg);
        DispatchMessageA(&msg);
    }
    if (ole_initialized) {
        OleUninitialize();
    }
    return static_cast<int>(msg.wParam);
}
