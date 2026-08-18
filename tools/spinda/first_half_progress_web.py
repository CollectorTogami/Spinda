"""Flask dashboard for the live FR/LG Spinda first-half raw CSV run.

This is an external read-only monitor. It watches the output folder and status
JSON files that `Egg-First-Half-Hitter.py` already writes. It never talks to
mGBA, never steps frames, and never writes save/state files.

The event stream follows the same broad browser pattern as pokebot's web UI:
serve a static page, expose plain JSON, and push live updates through Server
Sent Events so the page does not need constant manual refresh.
"""

from __future__ import annotations

import argparse
import json
import math
import socket
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import Any, Iterable

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from first_half_raw_csv_audit import (  # noqa: E402
    DEFAULT_RAW_CSV_DIR,
    EXPECTED_SAVE_SIZE,
    EXPECTED_STATE_SIZE,
    EXPECTED_TARGETS,
    audit_raw_csv_folder,
)

try:
    from flask import Flask, Response, jsonify, render_template_string, request
except ImportError as exc:  # pragma: no cover - exercised by CLI users without Flask.
    raise SystemExit(
        "Flask is required for the web dashboard. Install it with:\n"
        r"<repo-root>\.venv-mgba\bin\python.exe -m pip install Flask"
    ) from exc

DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parents[2] / "1sthalves"
DEFAULT_STATUS_PATH = DEFAULT_OUTPUT_DIR / "_egg_first_half_hitter_status.json"
DEFAULT_BATCH_STATUS_PATH = DEFAULT_OUTPUT_DIR / "_batch_status.json"
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 233
DEFAULT_SAMPLE_LIMIT = 8
DEFAULT_SAMPLE_INTERVAL_SECONDS = 1.0


@dataclass
class RateSnapshot:
    """Rate estimate from the first observed sample to the current sample."""

    rate_pairs_per_second: float | None
    elapsed_seconds: float
    eta_seconds: float | None
    finish_time_local: str | None


class ProgressRateTracker:
    """Small in-memory rate estimator shared by API and SSE clients."""

    def __init__(self, target_pairs: int = EXPECTED_TARGETS) -> None:
        self.target_pairs = target_pairs
        self._lock = Lock()
        self._first_time: float | None = None
        self._first_pairs: int | None = None
        self._last_time: float | None = None
        self._last_pairs: int | None = None

    def update(self, complete_pairs: int, now: float | None = None) -> RateSnapshot:
        """Record one progress point and return current rate/ETA."""

        now = time.time() if now is None else now
        with self._lock:
            if (
                self._first_time is None
                or self._first_pairs is None
                or self._last_pairs is None
                or complete_pairs < self._last_pairs
            ):
                self._first_time = now
                self._first_pairs = complete_pairs

            self._last_time = now
            self._last_pairs = complete_pairs
            first_time = self._first_time if self._first_time is not None else now
            first_pairs = self._first_pairs if self._first_pairs is not None else complete_pairs
            elapsed = max(0.0, now - first_time)
            pair_delta = complete_pairs - first_pairs

        rate = pair_delta / elapsed if elapsed > 0 and pair_delta > 0 else None
        remaining = max(0, self.target_pairs - complete_pairs)
        eta = remaining / rate if rate and rate > 0 else None
        finish = None
        if eta is not None and math.isfinite(eta):
            finish = (datetime.now().astimezone() + timedelta(seconds=eta)).strftime(
                "%Y-%m-%d %H:%M:%S %Z"
            )
        return RateSnapshot(rate, elapsed, eta, finish)


class ProgressPayloadCache:
    """Throttle filesystem scans so many browser clients do not multiply audit cost."""

    def __init__(
        self,
        *,
        raw_csv_dir: Path = DEFAULT_RAW_CSV_DIR,
        status_path: Path = DEFAULT_STATUS_PATH,
        batch_status_path: Path = DEFAULT_BATCH_STATUS_PATH,
        target_pairs: int = EXPECTED_TARGETS,
        sample_limit: int = DEFAULT_SAMPLE_LIMIT,
        sample_interval_seconds: float = DEFAULT_SAMPLE_INTERVAL_SECONDS,
    ) -> None:
        self.raw_csv_dir = raw_csv_dir
        self.status_path = status_path
        self.batch_status_path = batch_status_path
        self.target_pairs = target_pairs
        self.sample_limit = sample_limit
        self.sample_interval_seconds = max(0.1, sample_interval_seconds)
        self._tracker = ProgressRateTracker(target_pairs)
        self._lock = Lock()
        self._payload: dict[str, Any] | None = None
        self._sampled_at_monotonic = 0.0

    def get(self, *, force: bool = False) -> dict[str, Any]:
        """Return cached progress, rescanning only after the sample interval."""

        now = time.monotonic()
        with self._lock:
            cache_age = now - self._sampled_at_monotonic
            if not force and self._payload is not None and cache_age < self.sample_interval_seconds:
                payload = dict(self._payload)
                payload["cache_age_seconds"] = cache_age
                return payload

            payload = build_progress_payload(
                raw_csv_dir=self.raw_csv_dir,
                status_path=self.status_path,
                batch_status_path=self.batch_status_path,
                target_pairs=self.target_pairs,
                sample_limit=self.sample_limit,
                tracker=self._tracker,
            )
            self._sampled_at_monotonic = time.monotonic()
            payload["cache_age_seconds"] = 0.0
            payload["sample_interval_seconds"] = self.sample_interval_seconds
            self._payload = payload
            return dict(payload)


def _read_json_file(path: Path) -> dict[str, Any] | None:
    """Read optional status JSON without treating absent files as fatal."""

    try:
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"error": str(exc), "path": str(path)}


def _duration_text(seconds: float | None) -> str:
    """Human ETA string for dashboard display."""

    if seconds is None or not math.isfinite(seconds) or seconds < 0:
        return "unknown"
    total = int(seconds + 0.5)
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes}m {secs}s"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def _detect_lan_ip() -> str:
    """Return best non-loopback IPv4 address for the local network."""

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("8.8.8.8", 80))
            address = probe.getsockname()[0]
            if address and not address.startswith("127."):
                return address
    except OSError:
        pass

    try:
        for address in socket.gethostbyname_ex(socket.gethostname())[2]:
            if address and not address.startswith("127."):
                return address
    except OSError:
        pass
    return "127.0.0.1"


def _display_host(bind_host: str) -> str:
    """Map wildcard or loopback bind hosts to the operator-facing LAN address."""

    if bind_host in {"", "0.0.0.0", "::", "127.0.0.1", "localhost"}:
        return _detect_lan_ip()
    return bind_host


def build_progress_payload(
    *,
    raw_csv_dir: Path = DEFAULT_RAW_CSV_DIR,
    status_path: Path = DEFAULT_STATUS_PATH,
    batch_status_path: Path = DEFAULT_BATCH_STATUS_PATH,
    target_pairs: int = EXPECTED_TARGETS,
    sample_limit: int = DEFAULT_SAMPLE_LIMIT,
    tracker: ProgressRateTracker | None = None,
) -> dict[str, Any]:
    """Build one read-only status payload for JSON or SSE output."""

    audit = audit_raw_csv_folder(
        raw_csv_dir,
        expected_targets=target_pairs,
        sample_limit=sample_limit,
    )
    tracker = tracker or ProgressRateTracker(target_pairs)
    rate = tracker.update(audit.complete_pairs)
    rate_per_minute = (
        rate.rate_pairs_per_second * 60.0 if rate.rate_pairs_per_second is not None else None
    )

    progress = {
        "complete_pairs": audit.complete_pairs,
        "target_pairs": target_pairs,
        "remaining_pairs": max(0, target_pairs - audit.complete_pairs),
        "percent": audit.progress_percent,
        "rate_pairs_per_minute": rate_per_minute,
        "elapsed_seconds": rate.elapsed_seconds,
        "eta_seconds": rate.eta_seconds,
        "eta_text": _duration_text(rate.eta_seconds),
        "finish_time_local": rate.finish_time_local,
    }
    health = {
        "save_files": audit.save_files,
        "state_files": audit.state_files,
        "organic_lanes_present": audit.organic_lanes_present,
        "organic_lanes_missing": audit.organic_lanes_missing,
        "endpoint_exceptions_present": audit.endpoint_exceptions_present,
        "endpoint_exceptions_missing": audit.endpoint_exceptions_missing,
        "missing_pairs": audit.missing_pairs,
        "missing_save_for_state": audit.missing_save_for_state,
        "missing_state_for_save": audit.missing_state_for_save,
        "absent_targets": audit.absent_targets,
        "duplicate_target_entries": audit.duplicate_target_entries,
        "bad_names": audit.bad_names,
        "bad_target_naming": audit.bad_target_naming,
        "bad_sizes": audit.bad_sizes,
        "unsettled_files": audit.unsettled_files,
        "ignored_directories": audit.ignored_directories,
        "hash_check_enabled": audit.hash_check_enabled,
        "hashes_checked": audit.hashes_checked,
        "hash_mismatches": audit.hash_mismatches,
        "missing_hash_files": audit.missing_hash_files,
        "samples": audit.samples,
    }

    return {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "folder": audit.folder,
        "progress": progress,
        "health": health,
        "status": _read_json_file(status_path),
        "batch_status": _read_json_file(batch_status_path),
        "audit": asdict(audit),
    }


def _sse_message(event_type: str, data: Any) -> str:
    """Format one Server Sent Events message."""

    return f"event: {event_type}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Spinda First-Half Progress</title>
  <style>
    :root {
      --bg: #16140f;
      --panel: #fff7df;
      --ink: #231b12;
      --muted: #715f45;
      --good: #26805d;
      --warn: #a96218;
      --bad: #a82e2e;
      --line: rgba(35, 27, 18, 0.16);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      color: var(--ink);
      font-family: Georgia, "Times New Roman", serif;
      background:
        radial-gradient(circle at 10% 10%, rgba(255, 214, 102, 0.28), transparent 26rem),
        radial-gradient(circle at 90% 20%, rgba(86, 164, 122, 0.24), transparent 22rem),
        linear-gradient(135deg, #2a1f15, var(--bg));
      padding: 28px;
    }
    main {
      width: min(1180px, 100%);
      margin: 0 auto;
    }
    header {
      display: flex;
      gap: 18px;
      justify-content: space-between;
      align-items: end;
      color: #fff7df;
      margin-bottom: 20px;
    }
    h1 {
      font-size: clamp(2rem, 5vw, 4.3rem);
      letter-spacing: -0.06em;
      line-height: 0.9;
      margin: 0;
    }
    .subtitle { color: #e5d5ad; max-width: 42rem; }
    .status-pill {
      border: 1px solid rgba(255, 247, 223, 0.4);
      border-radius: 999px;
      padding: 10px 14px;
      white-space: nowrap;
      background: rgba(255, 247, 223, 0.08);
    }
    .grid {
      display: grid;
      grid-template-columns: 1.25fr 0.75fr;
      gap: 18px;
    }
    .panel {
      background: var(--panel);
      border: 1px solid rgba(255, 255, 255, 0.3);
      border-radius: 28px;
      box-shadow: 0 24px 80px rgba(0, 0, 0, 0.28);
      padding: 22px;
    }
    .hero-number {
      font-size: clamp(3.4rem, 10vw, 8rem);
      line-height: 0.86;
      letter-spacing: -0.08em;
      margin: 12px 0;
    }
    .bar {
      height: 24px;
      background: rgba(35, 27, 18, 0.1);
      border-radius: 999px;
      overflow: hidden;
      border: 1px solid var(--line);
    }
    .fill {
      height: 100%;
      width: 0%;
      background: linear-gradient(90deg, #e68a2e, #58a77b);
      transition: width 420ms ease;
    }
    .cards {
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      gap: 12px;
      margin-top: 18px;
    }
    .card {
      border: 1px solid var(--line);
      border-radius: 18px;
      padding: 14px;
      background: rgba(255, 255, 255, 0.35);
    }
    .label {
      color: var(--muted);
      font-size: 0.84rem;
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }
    .value {
      font-size: 1.55rem;
      margin-top: 6px;
      font-variant-numeric: tabular-nums;
    }
    .warn { color: var(--warn); }
    .bad { color: var(--bad); }
    .good { color: var(--good); }
    table {
      width: 100%;
      border-collapse: collapse;
      margin-top: 8px;
    }
    td {
      border-bottom: 1px solid var(--line);
      padding: 9px 0;
      font-variant-numeric: tabular-nums;
    }
    td:last-child { text-align: right; font-weight: 700; }
    pre {
      white-space: pre-wrap;
      word-break: break-word;
      background: rgba(35, 27, 18, 0.08);
      border-radius: 18px;
      padding: 14px;
      max-height: 210px;
      overflow: auto;
    }
    @media (max-width: 820px) {
      body { padding: 14px; }
      header, .grid { display: block; }
      .status-pill { margin-top: 14px; display: inline-block; }
      .panel { margin-bottom: 14px; }
      .cards { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
<main>
  <header>
    <div>
      <h1>Spinda<br>first-half run</h1>
      <p class="subtitle">Live read-only view of <code>.\1sthalves</code>. Uses EventSource/SSE like pokebot-style web overlays. No emulator control.</p>
    </div>
    <div class="status-pill" id="connection">connecting...</div>
  </header>
  <section class="grid">
    <div class="panel">
      <div class="label">Complete pairs</div>
      <div class="hero-number"><span id="complete">0</span><small style="font-size: 20%"> / 65536</small></div>
      <div class="bar"><div class="fill" id="fill"></div></div>
      <div class="cards">
        <div class="card"><div class="label">Percent</div><div class="value" id="percent">0%</div></div>
        <div class="card"><div class="label">Rate</div><div class="value" id="rate">unknown</div></div>
        <div class="card"><div class="label">ETA</div><div class="value" id="eta">unknown</div></div>
      </div>
    </div>
    <div class="panel">
      <div class="label">Run facts</div>
      <table>
        <tr><td>Run status</td><td id="run-status">unknown</td></tr>
        <tr><td>Timer 1 seed</td><td>0xFBC7</td></tr>
        <tr><td>Route seed</td><td id="route-seed">0xFB91</td></tr>
        <tr><td>Output mode</td><td id="output-mode">raw-csv</td></tr>
        <tr><td>Dashboard URL</td><td id="dashboard-url">unknown</td></tr>
        <tr><td>Generated</td><td id="generated-at">unknown</td></tr>
      </table>
    </div>
  </section>
  <section class="grid" style="margin-top: 18px;">
    <div class="panel">
      <div class="label">Health</div>
      <table>
        <tr><td>.sav files</td><td id="sav">0</td></tr>
        <tr><td>.ss0 files</td><td id="ss0">0</td></tr>
        <tr><td>Missing .sav for .ss0</td><td id="missing-save">0</td></tr>
        <tr><td>Missing .ss0 for .sav</td><td id="missing-state">0</td></tr>
        <tr><td>Bad names</td><td id="bad-names">0</td></tr>
        <tr><td>Bad sizes</td><td id="bad-sizes">0</td></tr>
        <tr><td>Unsettled writes</td><td id="unsettled">0</td></tr>
      </table>
    </div>
    <div class="panel">
      <div class="label">Samples / status JSON</div>
      <pre id="samples">{}</pre>
    </div>
  </section>
</main>
<script>
  const nf = new Intl.NumberFormat();
  const pct = value => `${Number(value || 0).toFixed(3)}%`;
  const setText = (id, value) => { document.getElementById(id).textContent = value; };
  function setClassByCount(id, count) {
    const node = document.getElementById(id);
    node.classList.remove("good", "warn", "bad");
    node.classList.add(count ? "warn" : "good");
  }
  function render(data) {
    const progress = data.progress || {};
    const health = data.health || {};
    const status = data.status || {};
    const server = data.server || {};
    setText("complete", nf.format(progress.complete_pairs || 0));
    setText("percent", pct(progress.percent));
    setText("rate", progress.rate_pairs_per_minute ? `${progress.rate_pairs_per_minute.toFixed(2)} pairs/min` : "warming up");
    setText("eta", progress.eta_text || "unknown");
    setText("generated-at", data.generated_at || "unknown");
    setText("run-status", status.run_status || "unknown");
    setText("route-seed", status.state_initial_seed || "0xFB91");
    setText("output-mode", status.output_key_mode || "raw-csv");
    setText("dashboard-url", server.display_url || window.location.href);
    setText("sav", nf.format(health.save_files || 0));
    setText("ss0", nf.format(health.state_files || 0));
    setText("missing-save", nf.format(health.missing_save_for_state || 0));
    setText("missing-state", nf.format(health.missing_state_for_save || 0));
    setText("bad-names", nf.format(health.bad_names || 0));
    setText("bad-sizes", nf.format(health.bad_sizes || 0));
    setText("unsettled", nf.format(health.unsettled_files || 0));
    ["missing-save", "missing-state", "bad-names", "bad-sizes", "unsettled"].forEach(id => setClassByCount(id, Number(document.getElementById(id).textContent.replaceAll(",", ""))));
    document.getElementById("fill").style.width = `${Math.min(100, Number(progress.percent || 0))}%`;
    document.getElementById("samples").textContent = JSON.stringify({samples: health.samples || {}, status}, null, 2);
  }
  async function initialLoad() {
    const response = await fetch("/api/status");
    render(await response.json());
  }
  initialLoad().catch(() => setText("connection", "initial load failed"));
  const source = new EventSource("/events?interval=2");
  source.addEventListener("open", () => setText("connection", "live"));
  source.addEventListener("progress", event => render(JSON.parse(event.data)));
  source.addEventListener("error", () => setText("connection", "reconnecting..."));
</script>
</body>
</html>"""


def create_app(
    *,
    raw_csv_dir: Path = DEFAULT_RAW_CSV_DIR,
    status_path: Path = DEFAULT_STATUS_PATH,
    batch_status_path: Path = DEFAULT_BATCH_STATUS_PATH,
    target_pairs: int = EXPECTED_TARGETS,
    sample_interval_seconds: float = DEFAULT_SAMPLE_INTERVAL_SECONDS,
    display_url: str | None = None,
) -> Flask:
    """Create Flask app. Dependency-injected paths keep tests file-local."""

    app = Flask(__name__)
    payload_cache = ProgressPayloadCache(
        raw_csv_dir=raw_csv_dir,
        status_path=status_path,
        batch_status_path=batch_status_path,
        target_pairs=target_pairs,
        sample_interval_seconds=sample_interval_seconds,
    )

    @app.get("/")
    def index() -> str:
        return render_template_string(INDEX_HTML)

    @app.get("/api/status")
    def api_status():
        payload = payload_cache.get()
        payload["server"] = {"display_url": display_url}
        return jsonify(payload)

    @app.get("/events")
    def events() -> Response:
        interval = request.args.get("interval", "2")
        try:
            interval_seconds = min(60.0, max(0.5, float(interval)))
        except ValueError:
            interval_seconds = 2.0

        def stream():
            yield "retry: 2500\n\n"
            while True:
                payload = payload_cache.get()
                payload["server"] = {"display_url": display_url}
                yield _sse_message("progress", payload)
                time.sleep(interval_seconds)

        return Response(stream(), mimetype="text/event-stream")

    return app


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    """Parse CLI options."""

    parser = argparse.ArgumentParser(
        description="Run Flask dashboard for the live Spinda first-half raw CSV run.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--folder", type=Path, default=DEFAULT_RAW_CSV_DIR)
    parser.add_argument("--status", type=Path, default=DEFAULT_STATUS_PATH)
    parser.add_argument("--batch-status", type=Path, default=DEFAULT_BATCH_STATUS_PATH)
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--display-host",
        help="Override the LAN host shown in stdout, JSON, SSE, and the dashboard.",
    )
    parser.add_argument("--target-pairs", type=int, default=EXPECTED_TARGETS)
    parser.add_argument(
        "--sample-interval",
        type=float,
        default=DEFAULT_SAMPLE_INTERVAL_SECONDS,
        help="Minimum seconds between folder scans shared by JSON and SSE clients.",
    )
    parser.add_argument("--debug", action="store_true")
    return parser.parse_args(argv)


def main(argv: Iterable[str] | None = None) -> int:
    """Run Flask development server for local operator use."""

    args = parse_args(argv)
    display_host = args.display_host or _display_host(args.host)
    display_url = f"http://{display_host}:{args.port}/"
    app = create_app(
        raw_csv_dir=args.folder,
        status_path=args.status,
        batch_status_path=args.batch_status,
        target_pairs=args.target_pairs,
        sample_interval_seconds=args.sample_interval,
        display_url=display_url,
    )
    print(f"Spinda progress dashboard: {display_url}")
    print(f"Binding Flask server on {args.host}:{args.port}")
    app.run(host=args.host, port=args.port, debug=args.debug, threaded=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
