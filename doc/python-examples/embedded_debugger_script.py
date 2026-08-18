"""Example for mGBA's embedded Python debugger environment.

This is not meant to be run with:

    python embedded_debugger_script.py

It is meant to be loaded by mGBA's embedded Python debugger path, where the
script engine injects:

- debugger
- symbols

What this demonstrates:
- checking for the injected debugger object
- redirecting print output to the CLI debugger when available
- exposing integer symbols back to mGBA
- registering a debugger-enter callback
"""

from __future__ import annotations


if "debugger" not in globals():
    raise SystemExit(
        "This script must be run inside mGBA's embedded Python debugger environment."
    )

if hasattr(debugger, "install_print"):
    debugger.install_print()

print("Embedded debugger script loaded.")
print(f"Debugger type: {type(debugger).__name__}")
print(f"Core title: {debugger._core.game_title!r}")


def _safe_pc() -> int:
    """Return the current PC if the debugger CPU wrapper exposes it."""

    return getattr(debugger._core.cpu, "pc", 0)


symbols["pc"] = _safe_pc
symbols["frame"] = lambda: debugger._core.frame_counter


def on_debugger_entered(reason, info) -> None:
    """Log one debugger-enter event through the embedded print bridge."""

    pc = _safe_pc()
    print(f"Debugger entered: reason={reason} pc=0x{pc:X} info={info}")


debugger.add_callback(on_debugger_entered)
print("Registered symbols: pc, frame")
print("Registered debugger callback.")
