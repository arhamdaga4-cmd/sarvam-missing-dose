"""Coordinator notifications.

Channels are independent and optional; a broken channel must never break the
call or the scheduler. Every attempt is recorded so the escalation row shows
what was actually delivered.
"""
from __future__ import annotations

import json
import logging
import smtplib
from email.message import EmailMessage
from typing import Any

import requests

from . import config

log = logging.getLogger(__name__)


def _console(escalation, patient, report) -> bool:
    from .escalation import describe

    print("\n" + "=" * 62)
    print(describe(escalation, patient))
    if report:
        print(f"  summary : {report.get('summary', '')}")
        print(f"  missed  : {report.get('missed_doses', 0)} dose(s)")
    coord = (patient or {}).get("coordinator") or {}
    if escalation.needs_human and coord:
        print(f"  notify  : {coord.get('name', 'coordinator')} "
              f"{coord.get('phone', '')} {coord.get('email', '')}".rstrip())
    print("=" * 62 + "\n")
    return True


def _jsonl(escalation, patient, report, checkin_id) -> bool:
    try:
        config.ESCALATION_LOG.parent.mkdir(parents=True, exist_ok=True)
        with config.ESCALATION_LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "patient_id": (patient or {}).get("id"),
                "patient_name": (patient or {}).get("name"),
                "checkin_id": checkin_id,
                "level": escalation.level,
                "reasons": escalation.reasons,
                "report": report,
            }, ensure_ascii=False) + "\n")
        return True
    except OSError as exc:
        log.warning("Could not write escalation log: %s", exc)
        return False


def _webhook(escalation, patient, report) -> bool:
    if not config.COORDINATOR_WEBHOOK_URL:
        return False
    try:
        resp = requests.post(
            config.COORDINATOR_WEBHOOK_URL,
            json={
                "level": escalation.level,
                "reasons": escalation.reasons,
                "patient": {
                    "id": (patient or {}).get("id"),
                    "name": (patient or {}).get("name"),
                    "language_code": (patient or {}).get("language_code"),
                },
                "coordinator": (patient or {}).get("coordinator"),
                "summary": (report or {}).get("summary"),
                "report": report,
            },
            timeout=10,
        )
        return resp.status_code < 300
    except requests.RequestException as exc:
        log.warning("Webhook notification failed: %s", exc)
        return False


def _email(escalation, patient, report) -> bool:
    if not (config.SMTP_HOST and config.ALERT_EMAIL_TO):
        return False
    recipients = list(config.ALERT_EMAIL_TO)
    coord_email = ((patient or {}).get("coordinator") or {}).get("email")
    if coord_email and coord_email not in recipients:
        recipients.append(coord_email)
    msg = EmailMessage()
    msg["Subject"] = f"[{escalation.level.upper()}] Check-in: {(patient or {}).get('name', 'patient')}"
    msg["From"] = config.ALERT_EMAIL_FROM
    msg["To"] = ", ".join(recipients)
    body = [
        f"Patient: {(patient or {}).get('name')} ({(patient or {}).get('id')})",
        f"Level: {escalation.level}",
        "",
        "Reasons:",
    ]
    body += [f"- {r}" for r in escalation.reasons]
    body += ["", f"Summary: {(report or {}).get('summary', '')}", ""]
    for turn in (report or {}).get("_transcript") or []:
        body.append(f"{turn['role']}: {turn['content']}")
    msg.set_content("\n".join(body))
    try:
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=15) as smtp:
            smtp.starttls()
            if config.SMTP_USER:
                smtp.login(config.SMTP_USER, config.SMTP_PASSWORD)
            smtp.send_message(msg)
        return True
    except (smtplib.SMTPException, OSError) as exc:
        log.warning("Email notification failed: %s", exc)
        return False


def notify(escalation, patient: dict | None = None, report: dict | None = None,
           checkin_id: int | None = None) -> dict[str, Any]:
    """Deliver to every configured channel. Returns a per-channel result map."""
    delivered: dict[str, Any] = {}
    if escalation.level == "none":
        return delivered
    delivered["console"] = _console(escalation, patient, report)
    delivered["jsonl"] = _jsonl(escalation, patient, report, checkin_id)
    if config.COORDINATOR_WEBHOOK_URL and escalation.needs_human:
        delivered["webhook"] = _webhook(escalation, patient, report)
    if config.SMTP_HOST and config.ALERT_EMAIL_TO and escalation.needs_human:
        delivered["email"] = _email(escalation, patient, report)
    return delivered


def dispatch(escalation, patient, report, checkin_id=None, db_path=None) -> int | None:
    """Notify, then persist the escalation row. Returns the escalation id."""
    from . import patient_db

    delivered = notify(escalation, patient, report, checkin_id)
    if escalation.level == "none":
        return None
    return patient_db.record_escalation(
        patient_id=(patient or {}).get("id", "unknown"),
        level=escalation.level,
        reasons=escalation.reasons,
        checkin_id=checkin_id,
        delivered=delivered,
        db_path=db_path,
    )
