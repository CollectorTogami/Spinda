#!/usr/bin/env python3
"""Validate the Phase 3 Linux helper-node packaging path.

This is a source/package readiness check. It does not build mGBA, launch an
emulator, claim lanes, open ZIP contents, or touch production workers.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).absolute().parents[2]
LINUX_BUILD = Path("tools/spinda/build_phase3_cli_linux.sh")
LINUX_HELPER = Path("tools/spinda/run_phase3_ledger_helper.sh")
WORKER_POOL = Path("tools/spinda/native_phase3_worker_pool.py")
LEDGER_CLIENT = Path("tools/spinda/phase3_ledger_worker_client.py")
LINUX_TEST = Path("src/platform/python/tests/examples/test_phase3_linux_helper_port.py")
LINUX_DOC = Path("docs/PHASE3_LINUX_HELPER_NODE.md")
MAIN_LINUX_DOC = Path("markdown-files/PHASE3_LINUX_HELPER_NODE.md")


@dataclass(frozen=True)
class CheckResult:
    """One validator row."""

    name: str
    ok: bool
    detail: str


def detect_mode(root: Path) -> str:
    """Infer source, clean, or assisted tree layout from marker files."""

    if (root / "ASSISTED_PACKAGE_MANIFEST.json").is_file():
        return "assisted"
    if root.name == "github-clean" or (root / "docs").is_dir() and not (root / "markdown-files").is_dir():
        return "clean"
    return "source"


def read_text(root: Path, rel: Path) -> str:
    """Read a UTF-8 file relative to root."""

    return (root / rel).read_text(encoding="utf-8")


def add_file_check(results: list[CheckResult], root: Path, rel: Path) -> None:
    """Append a required-file existence check."""

    path = root / rel
    results.append(CheckResult(str(rel), path.is_file(), "present" if path.is_file() else "missing"))


def check_text_contains(results: list[CheckResult], root: Path, rel: Path, needle: str, name: str) -> None:
    """Append a text containment check."""

    try:
        text = read_text(root, rel)
    except OSError as exc:
        results.append(CheckResult(name, False, f"could not read {rel}: {exc}"))
        return
    results.append(CheckResult(name, needle in text, "found" if needle in text else f"missing {needle!r}"))


def check_assisted_manifest_flag(results: list[CheckResult], root: Path) -> None:
    """Check assisted-package manifest booleans without depending on JSON spacing."""

    manifest = root / "ASSISTED_PACKAGE_MANIFEST.json"
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        results.append(CheckResult("assisted manifest marks Linux helper", False, f"could not parse manifest: {exc}"))
        return
    included = payload.get("linux_helper_included") is True
    results.append(CheckResult("assisted manifest marks Linux helper", included, "found" if included else "missing true linux_helper_included"))


def shell_file_shape_check(root: Path, rel: Path) -> list[CheckResult]:
    """Check Linux shell-file details that `bash -n` does not catch.

    A Windows-edited shell script can parse with some Bash builds while still
    failing on Linux because the shebang becomes `/usr/bin/env bash\r`.
    """

    path = root / rel
    try:
        data = path.read_bytes()
    except OSError as exc:
        return [CheckResult(f"linux shell shape {rel}", False, str(exc))]
    return [
        CheckResult(f"linux shell shebang {rel}", data.startswith(b"#!/usr/bin/env bash\n"), "ok" if data.startswith(b"#!/usr/bin/env bash\n") else "bad or CRLF shebang"),
        CheckResult(f"linux shell LF endings {rel}", b"\r\n" not in data, "LF only" if b"\r\n" not in data else "CRLF found"),
    ]


def shell_syntax_check(root: Path, rel: Path, bash: str | None) -> CheckResult:
    """Run `bash -n` when Bash is available."""

    bash_path = bash or shutil.which("bash")
    if not bash_path:
        return CheckResult(f"bash syntax {rel}", True, "skipped: bash not found")
    try:
        completed = subprocess.run(
            [bash_path, "-n", str(root / rel)],
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return CheckResult(f"bash syntax {rel}", False, str(exc))
    ok = completed.returncode == 0
    detail = "passed" if ok else (completed.stderr or completed.stdout).replace("\x00", "").strip()
    if not ok and not bash and "Windows Subsystem for Linux has no installed distributions" in detail:
        return CheckResult(f"bash syntax {rel}", True, "skipped: WSL bash shim is not installed")
    return CheckResult(f"bash syntax {rel}", ok, detail)


def count_phase2_states(root: Path) -> int:
    """Count final Phase 2 state files by name without opening them."""

    folder = root / "Phase2PickupStates"
    if not folder.is_dir():
        return 0
    return sum(1 for path in folder.iterdir() if path.is_file() and path.name.startswith("0x") and path.suffix == ".ss0")


def scan_for_clean_artifacts(root: Path) -> list[Path]:
    """Return private/generated artifacts that must not exist in github-clean."""

    bad: list[Path] = []
    blocked_dirs = {"Phase2PickupStates", "Phase3SpindaBlocks", "Assisted-baking", "portable-python"}
    blocked_suffixes = {".gba", ".sav", ".ss0", ".pk3"}
    for path in root.rglob("*"):
        rel_parts = set(path.relative_to(root).parts)
        if rel_parts & blocked_dirs:
            bad.append(path)
            continue
        if path.is_file():
            lower = path.name.lower()
            if path.suffix.lower() in blocked_suffixes or lower.endswith(".spinda80.zip"):
                bad.append(path)
    return bad[:20]


def validate(root: Path, mode: str, *, bash: str | None = None, skip_phase2_count: bool = False) -> list[CheckResult]:
    """Run Linux-helper readiness checks for one tree."""

    results: list[CheckResult] = []
    for rel in (LINUX_BUILD, LINUX_HELPER, WORKER_POOL, LEDGER_CLIENT, LINUX_TEST):
        add_file_check(results, root, rel)

    doc_rel = MAIN_LINUX_DOC if mode == "source" else LINUX_DOC
    add_file_check(results, root, doc_rel)

    build_rel = LINUX_BUILD
    helper_rel = LINUX_HELPER
    check_text_contains(results, root, build_rel, "-DBUILD_QT=OFF", "linux build disables Qt")
    check_text_contains(results, root, build_rel, "-DBUILD_SPINDA_PHASE3_CLI=ON", "linux build enables Phase 3 CLI")
    check_text_contains(results, root, build_rel, "--target mgba-spinda-phase3", "linux build target")
    check_text_contains(results, root, helper_rel, "--runner cli", "linux helper uses CLI runner")
    check_text_contains(results, root, helper_rel, "build-linux-spinda-cli/mgba-spinda-phase3", "linux helper default CLI path")
    check_text_contains(results, root, helper_rel, 'ROM="${ROM:-$ROOT/inputs/lg.gba}"', "linux helper default ROM path")

    try:
        helper_text = read_text(root, helper_rel)
        output_dir_count = helper_text.count('--output-dir "$OUTPUT_DIR"')
        results.append(
            CheckResult(
                "linux helper output-dir ownership",
                output_dir_count == 1,
                f"--output-dir occurrences: {output_dir_count}",
            )
        )
        qt_mentions = ["mGBA.exe", "mgba-qt"]
        found_qt = [needle for needle in qt_mentions if needle in helper_text]
        results.append(CheckResult("linux helper has no Qt executable references", not found_qt, ", ".join(found_qt) or "none"))
    except OSError as exc:
        results.append(CheckResult("linux helper text audit", False, str(exc)))

    for shell_rel in (LINUX_BUILD, LINUX_HELPER):
        results.extend(shell_file_shape_check(root, shell_rel))
        results.append(shell_syntax_check(root, shell_rel, bash))

    if mode == "clean":
        bad = scan_for_clean_artifacts(root)
        results.append(CheckResult("clean repo has no private artifacts", not bad, ", ".join(str(p.relative_to(root)) for p in bad) or "none"))

    if mode == "assisted":
        for rel in (Path("inputs/lg.gba"), Path("inputs/secondhalf.csv"), Path("ASSISTED_PACKAGE_MANIFEST.json")):
            add_file_check(results, root, rel)
        phase2_count = count_phase2_states(root) if not skip_phase2_count else 65536
        results.append(
            CheckResult(
                "assisted Phase2 state count",
                phase2_count == 65536,
                "skipped by option" if skip_phase2_count else str(phase2_count),
            )
        )
        check_assisted_manifest_flag(results, root)

    return results


def build_parser() -> argparse.ArgumentParser:
    """Build CLI parser."""

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--mode", choices=("auto", "source", "clean", "assisted"), default="auto")
    parser.add_argument("--bash", help="Optional bash executable for `bash -n` checks.")
    parser.add_argument("--skip-phase2-count", action="store_true", help="Skip the 65,536-state count in assisted mode.")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    """Run checks and return process exit status."""

    args = build_parser().parse_args(argv)
    root = args.root.absolute()
    mode = detect_mode(root) if args.mode == "auto" else args.mode
    results = validate(root, mode, bash=args.bash, skip_phase2_count=args.skip_phase2_count)
    payload = {
        "root": str(root),
        "mode": mode,
        "ok": all(result.ok for result in results),
        "checks": [asdict(result) for result in results],
    }
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"Linux helper port check: {root} ({mode})")
        for result in results:
            status = "PASS" if result.ok else "FAIL"
            print(f"{status:4} {result.name}: {result.detail}")
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
