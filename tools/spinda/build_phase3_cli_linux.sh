#!/usr/bin/env bash
set -euo pipefail

# Linux-only Phase 3 CLI build helper. It builds the headless native worker
# target and skips Qt/Python/frontend pieces that helper nodes do not need.
# Do not use this for Phase 1/2 or visual inspection; it intentionally builds
# only the Phase 3 CLI executable used by helper nodes.
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"

BUILD_DIR="${PHASE3_CLI_BUILD_DIR:-$ROOT/build-linux-spinda-cli}"
CMAKE_EXE="${CMAKE_EXE:-cmake}"
JOBS="${PHASE3_CLI_BUILD_JOBS:-}"
if [[ -z "$JOBS" ]]; then
    JOBS="$(command -v nproc >/dev/null 2>&1 && nproc || printf '6')"
fi

LTO_FLAG="ON"
if [[ "${PHASE3_CLI_LTO:-1}" == "0" ]]; then
    LTO_FLAG="OFF"
fi

RELEASE_C_FLAGS="-O3 -DNDEBUG"
RELEASE_CXX_FLAGS="-O3 -DNDEBUG"
RELEASE_LINKER_FLAGS=""
if [[ "${PHASE3_CLI_NATIVE_CPU:-0}" == "1" ]]; then
    RELEASE_C_FLAGS="$RELEASE_C_FLAGS -march=native"
    RELEASE_CXX_FLAGS="$RELEASE_CXX_FLAGS -march=native"
fi

PGO_DIR="${PHASE3_CLI_PGO_DIR:-$ROOT/Phase3SpindaBlocks/_pgo/cli-linux}"
if [[ "${PHASE3_CLI_PGO_GENERATE:-0}" == "1" && "${PHASE3_CLI_PGO_USE:-0}" == "1" ]]; then
    echo "PHASE3_CLI_PGO_GENERATE and PHASE3_CLI_PGO_USE cannot both be 1." >&2
    exit 2
fi
if [[ "${PHASE3_CLI_PGO_GENERATE:-0}" == "1" ]]; then
    RELEASE_C_FLAGS="$RELEASE_C_FLAGS -fprofile-generate=$PGO_DIR"
    RELEASE_CXX_FLAGS="$RELEASE_CXX_FLAGS -fprofile-generate=$PGO_DIR"
    RELEASE_LINKER_FLAGS="$RELEASE_LINKER_FLAGS -fprofile-generate=$PGO_DIR"
fi
if [[ "${PHASE3_CLI_PGO_USE:-0}" == "1" ]]; then
    RELEASE_C_FLAGS="$RELEASE_C_FLAGS -fprofile-use=$PGO_DIR -fprofile-correction -Wno-error=coverage-mismatch"
    RELEASE_CXX_FLAGS="$RELEASE_CXX_FLAGS -fprofile-use=$PGO_DIR -fprofile-correction -Wno-error=coverage-mismatch"
    RELEASE_LINKER_FLAGS="$RELEASE_LINKER_FLAGS -fprofile-use=$PGO_DIR -fprofile-correction"
fi

GENERATOR_ARGS=()
if [[ -z "${CMAKE_GENERATOR:-}" ]] && command -v ninja >/dev/null 2>&1; then
    GENERATOR_ARGS=(-G Ninja)
fi

echo "Phase 3 Linux headless CLI build"
echo "Root: $ROOT"
echo "Build dir: $BUILD_DIR"
echo "CMake: $CMAKE_EXE"
echo "Jobs: $JOBS"
echo "LTO: $LTO_FLAG"
echo "Native CPU: ${PHASE3_CLI_NATIVE_CPU:-0}"
echo "PGO generate: ${PHASE3_CLI_PGO_GENERATE:-0}"
echo "PGO use: ${PHASE3_CLI_PGO_USE:-0}"
echo "PGO dir: $PGO_DIR"

# shellcheck disable=SC2086
"$CMAKE_EXE" -S "$ROOT" -B "$BUILD_DIR" "${GENERATOR_ARGS[@]}" \
    -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
    -DCMAKE_BUILD_TYPE=Release \
    -DBUILD_LTO="$LTO_FLAG" \
    -DBUILD_SHARED=OFF \
    -DBUILD_STATIC=ON \
    -DBUILD_QT=OFF \
    -DBUILD_SDL=OFF \
    -DBUILD_PYTHON=OFF \
    -DBUILD_SPINDA_PHASE3_CLI=ON \
    -DBUILD_PERF=OFF \
    -DBUILD_TEST=OFF \
    -DBUILD_SUITE=OFF \
    -DBUILD_CINEMA=OFF \
    -DBUILD_ROM_TEST=OFF \
    -DBUILD_EXAMPLE=OFF \
    -DBUILD_LIBRETRO=OFF \
    -DENABLE_SCRIPTING=OFF \
    -DUSE_LUA=OFF \
    -DUSE_DEBUGGERS=OFF \
    -DUSE_GDB_STUB=OFF \
    -DUSE_DISCORD_RPC=OFF \
    -DUSE_FFMPEG=OFF \
    -DUSE_PNG=OFF \
    -DUSE_LIBZIP=OFF \
    -DUSE_MINIZIP=OFF \
    -DUSE_LZMA=OFF \
    -DUSE_SQLITE3=OFF \
    -DUSE_ELF=OFF \
    -DBUILD_GL=OFF \
    -DBUILD_GLES2=OFF \
    -DBUILD_GLES3=OFF \
    -DM_CORE_GBA=ON \
    -DM_CORE_GB=OFF \
    "-DCMAKE_C_FLAGS_RELEASE=$RELEASE_C_FLAGS" \
    "-DCMAKE_CXX_FLAGS_RELEASE=$RELEASE_CXX_FLAGS" \
    "-DCMAKE_EXE_LINKER_FLAGS_RELEASE=$RELEASE_LINKER_FLAGS" \
    ${PHASE3_CLI_EXTRA_CMAKE_ARGS:-}

"$CMAKE_EXE" --build "$BUILD_DIR" --target mgba-spinda-phase3 -j "$JOBS"

echo "Built: $BUILD_DIR/mgba-spinda-phase3"
