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

TARGET_LOW = float(os.environ.get("TARGET_LOW", "19.0"))
TARGET_HIGH = float(os.environ.get("TARGET_HIGH", "23.0"))
LOOKAHEAD = int(os.environ.get("LOOKAHEAD", "30"))
LOOP_SECONDS = int(os.environ.get("LOOP_SECONDS", "60"))
HEATER_ID = os.environ.get("ACTUATOR_HEATER_ID", "heater01")
COOLER_ID = os.environ.get("ACTUATOR_COOLER_ID", "cooler01")

ROLE_TO_ACTUATOR = {"heater": HEATER_ID, "cooler": COOLER_ID}


def log(msg):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def iteration(model, mean, std, client, last_sent, dry_run):
    window = latest_window()
    fc_scaled = run_forecast(model, scale(window, mean, std), LOOKAHEAD)
    fc = unscale(fc_scaled, mean, std)
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
        if dry_run:
            log(f"DRY-RUN would POST: actuator_id={actuator_id} command={command} issued_by=machine")
        else:
            client.post_actuator_command(actuator_id, command, issued_by="machine")
            log(f"sent: actuator_id={actuator_id} command={command} issued_by=machine")
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
    log(f"starting loop  interval={LOOP_SECONDS}s  dry_run={args.dry_run}")

    try:
        while True:
            try:
                last_sent = iteration(model, mean, std, client, last_sent, args.dry_run)
            except Exception as e:
                log(f"iteration failed: {e!r}")
            if args.once:
                return
            time.sleep(LOOP_SECONDS)
    except KeyboardInterrupt:
        log("interrupted, exiting")
        sys.exit(0)


if __name__ == "__main__":
    main()
