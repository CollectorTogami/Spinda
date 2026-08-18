r"""Shared helpers for the runnable mGBA Python examples.

These examples are meant to be run with the dedicated venv:

    <repo-root>\.venv-mgba\bin\python.exe
"""

from __future__ import annotations

import argparse
from pathlib import Path

import mgba.core
from mgba.gba import GBA
from mgba.gb import GB


def build_parser(description: str) -> argparse.ArgumentParser:
    """Create a parser with sane defaults for example scripts."""
    return argparse.ArgumentParser(
        description=description,
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )


def add_rom_argument(parser: argparse.ArgumentParser) -> None:
    """Add the common positional ROM path argument."""
    parser.add_argument(
        "rom",
        help="Path to a ROM file (.gba, .gb, or .gbc).",
    )


def load_core(rom_path: str):
    """Load a ROM and return the initialized core plus the resolved path."""
    rom = Path(rom_path).expanduser().resolve()
    core = mgba.core.load_path(str(rom))
    if not core:
        raise SystemExit(f"Could not load ROM: {rom}")
    return core, rom


def platform_name(core) -> str:
    """Return a readable platform name for the detected core."""
    if isinstance(core, GBA):
        return "GBA"
    if isinstance(core, GB):
        return "GB"
    return f"platform={core.platform}"


def print_core_summary(core, rom: Path) -> None:
    """Print the fields most examples care about."""
    print(f"ROM: {rom}")
    print(f"Platform: {platform_name(core)}")
    print(f"Title: {core.game_title!r}")
    print(f"Code: {core.game_code!r}")
    print(f"Frequency: {core.frequency}")


def require_gba(core) -> GBA:
    """Exit with a helpful message if the loaded core is not GBA."""
    if not isinstance(core, GBA):
        raise SystemExit("This example requires a GBA ROM.")
    return core


def require_gb(core) -> GB:
    """Exit with a helpful message if the loaded core is not GB."""
    if not isinstance(core, GB):
        raise SystemExit("This example requires a GB or GBC ROM.")
    return core
