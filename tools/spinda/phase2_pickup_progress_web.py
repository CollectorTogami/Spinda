"""Flask dashboard for Phase 2 pickup-state generation.

This read-only monitor watches `<repo-root>\\Phase2PickupStates` and the
`_phase2_pickup_status.json` file written by `Build-Phase2-Pickup-States.py`.
It never controls mGBA and never writes save or state artifacts.
"""

from __future__ import annotations

import argparse
import json
import math
import socket
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import Any, Iterable

try:
    from flask import Flask, Response, jsonify, render_template_string, request
except ImportError as exc:  # pragma: no cover - operator environment check.
    raise SystemExit(
        "Flask is required for the Phase 2 dashboard. Install it with:\n"
        r"<repo-root>\.venv-mgba\bin\python.exe -m pip install Flask"
    ) from exc


DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parents[2] / "Phase2PickupStates"
DEFAULT_STATUS_PATH = DEFAULT_OUTPUT_DIR / "_phase2_pickup_status.json"
DEFAULT_ERROR_PATH = DEFAULT_OUTPUT_DIR / "_phase2_pickup_errors.jsonl"
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 234
DEFAULT_TARGET_STATES = 0x10000
DEFAULT_STATE_SIZE = 397_312
DEFAULT_SAMPLE_LIMIT = 8
DEFAULT_SAMPLE_INTERVAL_SECONDS = 1.0
STATE_NAME_RE = r"^0x[0-9A-Fa-f]{4}\.ss0$"
TMP_STATE_NAME_RE = r"^0x[0-9A-Fa-f]{4}\.ss0\.tmp$"
IGNORED_OUTPUT_NAMES = {
    "_phase2_pickup_status.json",
    "_phase2_pickup_errors.jsonl",
    "_phase2_pickup_control.json",
}


@dataclass(frozen=True)
class Phase2PickupAudit:
    """Read-only filesystem audit of Phase 2 pickup-state outputs."""

    folder: str
    state_files: int
    complete_states: int
    target_states: int
    missing_states: int
    bad_names: int
    bad_sizes: int
    unsettled_files: int
    samples: dict[str, list[str]]

    @property
    def progress_percent(self) -> float:
        """Return completion percent through the target state count."""

        if self.target_states <= 0:
            return 0.0
        return round((self.complete_states / self.target_states) * 100.0, 6)


@dataclass
class RateSnapshot:
    """Rate estimate from first observed sample to current sample."""

    rate_states_per_second: float | None
    elapsed_seconds: float
    eta_seconds: float | None
    finish_time_local: str | None


class ProgressRateTracker:
    """Small in-memory rate estimator shared by API and SSE clients."""

    def __init__(self, target_states: int = DEFAULT_TARGET_STATES) -> None:
        self.target_states = target_states
        self._lock = Lock()
        self._first_time: float | None = None
        self._first_states: int | None = None
        self._last_states: int | None = None

    def update(self, complete_states: int, now: float | None = None) -> RateSnapshot:
        """Record one progress sample and return rate/ETA."""

        now = time.time() if now is None else now
        with self._lock:
            if (
                self._first_time is None
                or self._first_states is None
                or self._last_states is None
                or complete_states < self._last_states
            ):
                self._first_time = now
                self._first_states = complete_states
            self._last_states = complete_states
            first_time = self._first_time
            first_states = self._first_states

        elapsed = max(0.0, now - first_time)
        state_delta = complete_states - first_states
        rate = state_delta / elapsed if elapsed > 0 and state_delta > 0 else None
        remaining = max(0, self.target_states - complete_states)
        eta = remaining / rate if rate and rate > 0 else None
        finish = None
        if eta is not None and math.isfinite(eta):
            finish = (datetime.now().astimezone() + timedelta(seconds=eta)).strftime(
                "%Y-%m-%d %H:%M:%S %Z"
            )
        return RateSnapshot(rate, elapsed, eta, finish)


class ProgressPayloadCache:
    """Throttle folder scans so browser clients do not multiply disk work."""

    def __init__(
        self,
        *,
        output_dir: Path = DEFAULT_OUTPUT_DIR,
        status_path: Path = DEFAULT_STATUS_PATH,
        error_path: Path = DEFAULT_ERROR_PATH,
        target_states: int = DEFAULT_TARGET_STATES,
        expected_state_size: int = DEFAULT_STATE_SIZE,
        sample_limit: int = DEFAULT_SAMPLE_LIMIT,
        sample_interval_seconds: float = DEFAULT_SAMPLE_INTERVAL_SECONDS,
    ) -> None:
        self.output_dir = output_dir
        self.status_path = status_path
        self.error_path = error_path
        self.target_states = target_states
        self.expected_state_size = expected_state_size
        self.sample_limit = sample_limit
        self.sample_interval_seconds = max(0.1, sample_interval_seconds)
        self._tracker = ProgressRateTracker(target_states)
        self._lock = Lock()
        self._payload: dict[str, Any] | None = None
        self._sampled_at_monotonic = 0.0

    def get(self, *, force: bool = False) -> dict[str, Any]:
        """Return cached progress, rescanning only after sample interval."""

        now = time.monotonic()
        with self._lock:
            cache_age = now - self._sampled_at_monotonic
            if not force and self._payload is not None and cache_age < self.sample_interval_seconds:
                payload = dict(self._payload)
                payload["cache_age_seconds"] = cache_age
                return payload

            payload = build_progress_payload(
                output_dir=self.output_dir,
                status_path=self.status_path,
                error_path=self.error_path,
                target_states=self.target_states,
                expected_state_size=self.expected_state_size,
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


def _read_recent_errors(path: Path, limit: int = DEFAULT_SAMPLE_LIMIT) -> list[dict[str, Any]]:
    """Read recent JSONL error entries."""

    if not path.is_file():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[-limit:]
    except OSError:
        return [{"error": f"could not read {path}"}]
    errors: list[dict[str, Any]] = []
    for line in lines:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            payload = {"error": line}
        errors.append(payload)
    return errors


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
    """Map wildcard or loopback bind hosts to operator-facing address."""

    if bind_host in {"", "0.0.0.0", "::", "127.0.0.1", "localhost"}:
        return _detect_lan_ip()
    return bind_host


def _add_sample(samples: dict[str, list[str]], key: str, value: str, limit: int) -> None:
    """Append one bounded sample value."""

    bucket = samples.setdefault(key, [])
    if len(bucket) < limit:
        bucket.append(value)


def audit_phase2_pickup_states(
    output_dir: Path,
    *,
    target_states: int = DEFAULT_TARGET_STATES,
    expected_state_size: int = DEFAULT_STATE_SIZE,
    settle_seconds: float = 2.0,
    sample_limit: int = DEFAULT_SAMPLE_LIMIT,
) -> Phase2PickupAudit:
    """Audit Phase 2 output folder without modifying it."""

    output_dir = output_dir.expanduser()
    samples: dict[str, list[str]] = {}
    state_files = 0
    complete_targets: set[str] = set()
    bad_names = 0
    bad_sizes = 0
    unsettled_files = 0
    now = time.time()

    if not output_dir.is_dir():
        return Phase2PickupAudit(
            folder=str(output_dir.absolute()),
            state_files=0,
            complete_states=0,
            target_states=target_states,
            missing_states=target_states,
            bad_names=0,
            bad_sizes=0,
            unsettled_files=0,
            samples=samples,
        )

    import re

    state_re = re.compile(STATE_NAME_RE)
    tmp_state_re = re.compile(TMP_STATE_NAME_RE)
    for path in output_dir.iterdir():
        if path.is_dir():
            if path.name == "_scratch":
                continue
            bad_names += 1
            _add_sample(samples, "bad_names", f"{path.name} is a directory", sample_limit)
            continue
        if path.name in IGNORED_OUTPUT_NAMES:
            continue
        if tmp_state_re.match(path.name):
            unsettled_files += 1
            _add_sample(samples, "unsettled_files", path.name, sample_limit)
            continue
        if not state_re.match(path.name):
            bad_names += 1
            _add_sample(samples, "bad_names", path.name, sample_limit)
            continue

        state_files += 1
        stat_result = path.stat()
        if stat_result.st_size != expected_state_size:
            if now - stat_result.st_mtime < settle_seconds:
                unsettled_files += 1
                _add_sample(samples, "unsettled_files", path.name, sample_limit)
            else:
                bad_sizes += 1
                _add_sample(
                    samples,
                    "bad_sizes",
                    f"{path.name} size={stat_result.st_size} expected={expected_state_size}",
                    sample_limit,
                )
            continue
        complete_targets.add(path.stem.upper())

    complete_states = len(complete_targets)
    return Phase2PickupAudit(
        folder=str(output_dir.absolute()),
        state_files=state_files,
        complete_states=complete_states,
        target_states=target_states,
        missing_states=max(0, target_states - complete_states),
        bad_names=bad_names,
        bad_sizes=bad_sizes,
        unsettled_files=unsettled_files,
        samples=samples,
    )


def build_progress_payload(
    *,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    status_path: Path = DEFAULT_STATUS_PATH,
    error_path: Path = DEFAULT_ERROR_PATH,
    target_states: int = DEFAULT_TARGET_STATES,
    expected_state_size: int = DEFAULT_STATE_SIZE,
    sample_limit: int = DEFAULT_SAMPLE_LIMIT,
    tracker: ProgressRateTracker | None = None,
) -> dict[str, Any]:
    """Build one read-only status payload for JSON or SSE output."""

    audit = audit_phase2_pickup_states(
        output_dir,
        target_states=target_states,
        expected_state_size=expected_state_size,
        sample_limit=sample_limit,
    )
    tracker = tracker or ProgressRateTracker(target_states)
    rate = tracker.update(audit.complete_states)
    rate_per_minute = (
        rate.rate_states_per_second * 60.0 if rate.rate_states_per_second is not None else None
    )
    progress = {
        "complete_states": audit.complete_states,
        "target_states": target_states,
        "remaining_states": max(0, target_states - audit.complete_states),
        "percent": audit.progress_percent,
        "rate_states_per_minute": rate_per_minute,
        "elapsed_seconds": rate.elapsed_seconds,
        "eta_seconds": rate.eta_seconds,
        "eta_text": _duration_text(rate.eta_seconds),
        "finish_time_local": rate.finish_time_local,
    }
    health = {
        "state_files": audit.state_files,
        "missing_states": audit.missing_states,
        "bad_names": audit.bad_names,
        "bad_sizes": audit.bad_sizes,
        "unsettled_files": audit.unsettled_files,
        "samples": audit.samples,
    }
    status = _read_json_file(status_path)
    recent_errors = _read_recent_errors(error_path, sample_limit)
    return {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "folder": audit.folder,
        "progress": progress,
        "health": health,
        "status": status,
        "recent_errors": recent_errors,
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
  <title>Spinda Phase 2 Pickup States</title>
  <style>
    :root {
      --bg: #121417;
      --panel: #f4f1e8;
      --ink: #1d211f;
      --muted: #68706b;
      --line: rgba(29, 33, 31, 0.16);
      --good: #247a52;
      --warn: #aa631f;
      --bad: #a52d2d;
      --accent: #397c8f;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      color: var(--ink);
      font-family: "Segoe UI", Arial, sans-serif;
      background: linear-gradient(135deg, #1f2426, var(--bg));
      padding: 24px;
    }
    main { width: min(1120px, 100%); margin: 0 auto; }
    header {
      color: #f4f1e8;
      display: flex;
      justify-content: space-between;
      gap: 16px;
      align-items: end;
      margin-bottom: 18px;
    }
    h1 { margin: 0; font-size: clamp(2rem, 5vw, 4.8rem); line-height: 0.95; }
    .subtitle { color: #d7d0bf; max-width: 44rem; }
    .status-pill {
      border: 1px solid rgba(244, 241, 232, 0.38);
      border-radius: 999px;
      padding: 10px 14px;
      white-space: nowrap;
    }
    .grid { display: grid; grid-template-columns: 1.2fr 0.8fr; gap: 16px; }
    .panel {
      background: var(--panel);
      border: 1px solid rgba(255, 255, 255, 0.28);
      border-radius: 8px;
      box-shadow: 0 22px 70px rgba(0, 0, 0, 0.28);
      padding: 20px;
    }
    .label {
      color: var(--muted);
      font-size: 0.8rem;
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }
    .hero-number {
      font-size: clamp(3rem, 9vw, 7rem);
      line-height: 0.95;
      margin: 8px 0 16px;
      font-variant-numeric: tabular-nums;
      display: flex;
      gap: 0.16em;
      align-items: baseline;
      flex-wrap: wrap;
    }
    .hero-total {
      font-size: clamp(1.8rem, 4vw, 3.2rem);
      color: var(--muted);
      letter-spacing: 0;
    }
    .bar { height: 22px; background: rgba(29, 33, 31, 0.1); border-radius: 999px; overflow: hidden; }
    .fill { height: 100%; width: 0%; background: linear-gradient(90deg, var(--accent), #49a36f); }
    .cards { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; margin-top: 14px; }
    .card { border: 1px solid var(--line); border-radius: 8px; padding: 12px; background: rgba(255, 255, 255, 0.42); }
    .value { font-size: 1.35rem; margin-top: 4px; font-variant-numeric: tabular-nums; }
    table { width: 100%; border-collapse: collapse; margin-top: 8px; }
    td { border-bottom: 1px solid var(--line); padding: 8px 0; font-variant-numeric: tabular-nums; }
    td:last-child { text-align: right; font-weight: 700; }
    .good { color: var(--good); }
    .warn { color: var(--warn); }
    .bad { color: var(--bad); }
    pre {
      white-space: pre-wrap;
      word-break: break-word;
      background: rgba(29, 33, 31, 0.08);
      border-radius: 8px;
      padding: 12px;
      max-height: 220px;
      overflow: auto;
    }
    @media (max-width: 820px) {
      body { padding: 14px; }
      header, .grid { display: block; }
      .status-pill { margin-top: 12px; display: inline-block; }
      .panel { margin-bottom: 14px; }
      .cards { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
<main>
  <header>
    <div>
      <h1>Spinda Phase 2<br>Pickup States</h1>
      <p class="subtitle">Read-only monitor for <code>.\Phase2PickupStates</code>. Shows savestate generation progress after Phase 2 seed replication.</p>
    </div>
    <div class="status-pill" id="connection">connecting...</div>
  </header>
  <section class="grid">
    <div class="panel">
      <div class="label">Complete pickup states</div>
      <div class="hero-number"><span id="complete">0</span><span class="hero-total">/ 65536</span></div>
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
        <tr><td>Status</td><td id="run-status">unknown</td></tr>
        <tr><td>Current</td><td id="current">none</td></tr>
        <tr><td>CSV seed</td><td id="csv-seed">unknown</td></tr>
        <tr><td>Baseline frame</td><td id="baseline">unknown</td></tr>
        <tr><td>Expected RNG</td><td id="expected-rng">unknown</td></tr>
        <tr><td>Generated</td><td id="generated-at">unknown</td></tr>
      </table>
    </div>
  </section>
  <section class="grid" style="margin-top: 16px;">
    <div class="panel">
      <div class="label">Health</div>
      <table>
        <tr><td>.ss0 files</td><td id="state-files">0</td></tr>
        <tr><td>Missing states</td><td id="missing">0</td></tr>
        <tr><td>Bad names</td><td id="bad-names">0</td></tr>
        <tr><td>Bad sizes</td><td id="bad-sizes">0</td></tr>
        <tr><td>Unsettled writes</td><td id="unsettled">0</td></tr>
        <tr><td>Failures</td><td id="failures">0</td></tr>
      </table>
    </div>
    <div class="panel">
      <div class="label">Samples / recent errors</div>
      <pre id="details">{}</pre>
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
    const errors = data.recent_errors || [];
    setText("complete", nf.format(progress.complete_states || 0));
    setText("percent", pct(progress.percent));
    setText("rate", progress.rate_states_per_minute ? `${progress.rate_states_per_minute.toFixed(2)} states/min` : "warming up");
    setText("eta", progress.eta_text || "unknown");
    setText("run-status", status.status || "unknown");
    setText("current", status.current || "none");
    setText("csv-seed", status.csv_initial_seed || "unknown");
    setText("baseline", status.baseline_frame ?? "unknown");
    setText("expected-rng", status.expected_rng_at_baseline || "unknown");
    setText("generated-at", data.generated_at || "unknown");
    setText("state-files", nf.format(health.state_files || 0));
    setText("missing", nf.format(health.missing_states || 0));
    setText("bad-names", nf.format(health.bad_names || 0));
    setText("bad-sizes", nf.format(health.bad_sizes || 0));
    setText("unsettled", nf.format(health.unsettled_files || 0));
    setText("failures", nf.format(status.failed || errors.length || 0));
    ["bad-names", "bad-sizes", "unsettled", "failures"].forEach(id => {
      const count = Number(document.getElementById(id).textContent.replaceAll(",", ""));
      setClassByCount(id, count);
    });
    document.getElementById("fill").style.width = `${Math.min(100, Number(progress.percent || 0))}%`;
    document.getElementById("details").textContent = JSON.stringify({samples: health.samples || {}, recent_errors: errors}, null, 2);
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
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    status_path: Path = DEFAULT_STATUS_PATH,
    error_path: Path = DEFAULT_ERROR_PATH,
    target_states: int = DEFAULT_TARGET_STATES,
    expected_state_size: int = DEFAULT_STATE_SIZE,
    sample_interval_seconds: float = DEFAULT_SAMPLE_INTERVAL_SECONDS,
    display_url: str | None = None,
) -> Flask:
    """Create Flask app. Dependency-injected paths keep tests file-local."""

    app = Flask(__name__)
    payload_cache = ProgressPayloadCache(
        output_dir=output_dir,
        status_path=status_path,
        error_path=error_path,
        target_states=target_states,
        expected_state_size=expected_state_size,
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
        description="Run Flask dashboard for Spinda Phase 2 pickup-state progress.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--folder", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--status", type=Path, default=DEFAULT_STATUS_PATH)
    parser.add_argument("--errors", type=Path, default=DEFAULT_ERROR_PATH)
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--display-host")
    parser.add_argument("--target-states", type=int, default=DEFAULT_TARGET_STATES)
    parser.add_argument("--expected-state-size", type=int, default=DEFAULT_STATE_SIZE)
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
        output_dir=args.folder,
        status_path=args.status,
        error_path=args.errors,
        target_states=args.target_states,
        expected_state_size=args.expected_state_size,
        sample_interval_seconds=args.sample_interval,
        display_url=display_url,
    )
    print(f"Spinda Phase 2 pickup dashboard: {display_url}")
    print(f"Binding Flask server on {args.host}:{args.port}")
    app.run(host=args.host, port=args.port, debug=args.debug, threaded=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
