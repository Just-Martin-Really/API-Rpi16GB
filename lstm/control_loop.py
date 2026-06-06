"""LSTM control loop.

Long-running daemon that pulls the latest temperature window from the API,
predicts LOOKAHEAD minutes ahead with the trained model, and posts heater
or cooler commands to keep the forecast inside the target band.

All commands are tagged issued_by='machine'. Commands are only emitted when
the desired state changes (dedupe), so the queue isn't spammed with
repeated identical commands.

Flags:
  --once      run one iteration and exit. Useful for cron or for verifying
              the pipeline end-to-end.
  --dry-run   don't POST. Print the would-be commands instead.
"""
import argparse
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

from api_client import ApiClient
from decide import desired_state, diff_state
from forecast import forecast as run_forecast, latest_window, load_artifacts, scale, unscale
from prometheus_client import Counter, Gauge, Histogram, start_http_server

TARGET_LOW = float(os.environ.get("TARGET_LOW", "19.0"))
TARGET_HIGH = float(os.environ.get("TARGET_HIGH", "21.0"))
LOOKAHEAD = int(os.environ.get("LOOKAHEAD", "30"))
LOOP_SECONDS = int(os.environ.get("LOOP_SECONDS", "60"))
METRICS_PORT = int(os.environ.get("METRICS_PORT", "8000"))
HEATER_ID = os.environ.get("ACTUATOR_HEATER_ID", "heater01")
COOLER_ID = os.environ.get("ACTUATOR_COOLER_ID", "cooler01")

ROLE_TO_ACTUATOR = {"heater": HEATER_ID, "cooler": COOLER_ID}

# Translation from the loop's internal {role, state} representation to the
# command names the firmware understands. The Pico subscribes to a single
# actuator topic and switches on the command string, so off/on become
# explicit HEAT/FAN verbs at the wire.
ROLE_COMMAND_TO_WIRE = {
    ("heater", "on"):  "HEAT_ON",
    ("heater", "off"): "HEAT_OFF",
    ("cooler", "on"):  "FAN_ON",
    ("cooler", "off"): "FAN_OFF",
}

# Prometheus metrics. start_http_server() spins up a daemon thread that serves
# /metrics on METRICS_PORT against the default registry, so just defining the
# instruments here is enough — observing or incrementing them anywhere in the
# loop will show up on the scrape.
iterations_total = Counter(
    "lstm_iterations_total",
    "Number of control-loop iterations executed.",
    ["outcome"],
)
inference_duration = Histogram(
    "lstm_inference_duration_seconds",
    "Wall-clock time spent running forecast() per iteration.",
)
predictions_total = Counter(
    "lstm_predictions_total",
    "Number of forecast points emitted (LOOKAHEAD per iteration).",
)
last_prediction_value = Gauge(
    "lstm_last_prediction_celsius",
    "Most recent peak of the forecast window (max over LOOKAHEAD).",
)
commands_sent_total = Counter(
    "lstm_commands_sent_total",
    "Actuator commands dispatched by role and command.",
    ["role", "command"],
)


def log(msg):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def iteration(model, mean, std, client, last_sent, dry_run):
    window = latest_window()
    with inference_duration.time():
        fc_scaled = run_forecast(model, scale(window, mean, std), LOOKAHEAD)
    fc = unscale(fc_scaled, mean, std)
    predictions_total.inc(LOOKAHEAD)
    last_prediction_value.set(float(fc.max()))
    log(f"forecast {LOOKAHEAD}min  min={fc.min():.2f}  max={fc.max():.2f}  "
        f"target=[{TARGET_LOW},{TARGET_HIGH}]")
    desired = desired_state(fc, TARGET_LOW, TARGET_HIGH, LOOKAHEAD)
    log(f"desired: heater={desired['heater']} cooler={desired['cooler']}")
    to_send = diff_state(desired, last_sent)
    if not to_send:
        log("no change, nothing to send")
        return last_sent
    for role, command in to_send.items():
        actuator_id = ROLE_TO_ACTUATOR[role]
        wire_command = ROLE_COMMAND_TO_WIRE[(role, command)]
        if dry_run:
            log(f"DRY-RUN would POST: actuator_id={actuator_id} command={wire_command} issued_by=machine")
        else:
            client.post_actuator_command(actuator_id, wire_command, issued_by="machine")
            log(f"sent: actuator_id={actuator_id} command={wire_command} issued_by=machine")
        commands_sent_total.labels(role=role, command=wire_command).inc()
    new_state = dict(last_sent)
    new_state.update(to_send)
    return new_state


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--once", action="store_true",
                    help="run one iteration and exit")
    ap.add_argument("--dry-run", action="store_true",
                    help="don't POST commands, just print what would be sent")
    args = ap.parse_args()

    log("loading model")
    model, mean, std = load_artifacts()
    client = None if args.dry_run else ApiClient()
    last_sent = {}

    # --once is for cron / smoke tests; no point opening a metrics port there.
    if not args.once:
        start_http_server(METRICS_PORT)
        log(f"metrics endpoint listening on :{METRICS_PORT}/metrics")
    log(f"starting loop  interval={LOOP_SECONDS}s  dry_run={args.dry_run}")

    try:
        while True:
            try:
                last_sent = iteration(model, mean, std, client, last_sent, args.dry_run)
                iterations_total.labels(outcome="ok").inc()
            except Exception as e:
                iterations_total.labels(outcome="error").inc()
                log(f"iteration failed: {e!r}")
            if args.once:
                return
            time.sleep(LOOP_SECONDS)
    except KeyboardInterrupt:
        log("interrupted, exiting")
        sys.exit(0)


if __name__ == "__main__":
    main()
