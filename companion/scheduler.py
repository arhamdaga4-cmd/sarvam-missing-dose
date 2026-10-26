"""Daily check-in scheduler.

Runs forever, waking every SCHEDULER_POLL_SECONDS, and calls any active patient
who has not completed a check-in today at SCHEDULE_TIME (Asia/Kolkata by
default). Retries a failed call after SCHEDULER_RETRY_HOURS.

    python -m companion.scheduler                # run forever, real calls
    python -m companion.scheduler --dry-run      # simulate, no telephony
    python -m companion.scheduler --once         # one pass, then exit
    python -m companion.scheduler --once --patient P001
"""
from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from . import config, patient_db
from .call_simulator import simulate_call

log = logging.getLogger("scheduler")


def _tz() -> ZoneInfo:
    try:
        return ZoneInfo(config.SCHEDULE_TIMEZONE)
    except Exception:  # noqa: BLE001
        return ZoneInfo("Asia/Kolkata")


def _schedule_time() -> tuple[int, int]:
    try:
        hh, mm = config.SCHEDULE_TIME.split(":")
        return int(hh), int(mm)
    except (ValueError, AttributeError):
        return 9, 30


def is_due(patient: dict, now: datetime | None = None, db_path=None) -> bool:
    now = now or datetime.now(_tz())
    hour, minute = _schedule_time()
    today = now.date().isoformat()
    last = patient_db.last_checkin(patient["id"], db_path=db_path)

    if last is None:
        # Never called: due once the daily window opens.
        return (now.hour, now.minute) >= (hour, minute)

    if last.get("started_at", "")[:10] == today and last.get("completed"):
        return False

    # Today's attempt failed or never happened: retry after the retry window.
    last_dt = None
    if last.get("started_at"):
        try:
            last_dt = datetime.fromisoformat(last["started_at"]).astimezone(_tz())
        except ValueError:
            last_dt = None
    if last_dt and last_dt.date() == now.date():
        return now - last_dt >= timedelta(hours=config.SCHEDULER_RETRY_HOURS)
    return (now.hour, now.minute) >= (hour, minute)


def due_patients(now: datetime | None = None, db_path=None) -> list[dict]:
    return [p for p in patient_db.list_patients(db_path=db_path) if is_due(p, now, db_path)]


async def run_pass(dry_run: bool = False, only: str | None = None, db_path=None) -> list[dict]:
    patients = ([patient_db.get_patient(only, db_path=db_path)] if only
                else due_patients(db_path=db_path))
    patients = [p for p in patients if p]
    if not patients:
        log.info("No patients due.")
        return []

    results = []
    for patient in patients:
        try:
            if dry_run:
                result = simulate_call(patient["id"], offline=True, verbose=True, db_path=db_path)
                results.append(result.as_dict())
                log.info("[dry-run] %s simulated", patient["id"])
            else:
                from .telephony.exotel import call_patient

                payload = await asyncio.to_thread(call_patient, patient)
                results.append({"patient_id": patient["id"], "exotel": payload})
                log.info("Called %s: %s", patient["id"], payload)
        except Exception as exc:  # noqa: BLE001 - one bad call must not stop the run
            log.exception("Check-in failed for %s: %s", patient["id"], exc)
            results.append({"patient_id": patient["id"], "error": str(exc)})
    return results


async def run_forever(dry_run: bool = False) -> None:
    log.info("Scheduler started (tz=%s, at=%s, dry_run=%s)",
             config.SCHEDULE_TIMEZONE, config.SCHEDULE_TIME, dry_run)
    while True:
        try:
            await run_pass(dry_run=dry_run)
        except Exception as exc:  # noqa: BLE001
            log.exception("Scheduler pass failed: %s", exc)
        await asyncio.sleep(config.SCHEDULER_POLL_SECONDS)


def main() -> None:
    parser = argparse.ArgumentParser(description="Daily check-in scheduler")
    parser.add_argument("--once", action="store_true", help="run a single pass and exit")
    parser.add_argument("--dry-run", action="store_true", help="simulate instead of dialling")
    parser.add_argument("--patient", help="only this patient id")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    patient_db.load_patients()

    if args.once:
        results = asyncio.run(run_pass(dry_run=args.dry_run, only=args.patient))
        print(f"{len(results)} check-in(s) attempted on {date.today().isoformat()}")
    else:
        asyncio.run(run_forever(dry_run=args.dry_run))


if __name__ == "__main__":
    main()
