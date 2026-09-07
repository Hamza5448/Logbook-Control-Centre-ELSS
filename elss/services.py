from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime, time, timezone
from typing import Any

from cryptography.fernet import Fernet, InvalidToken


def format_utc(value: datetime | str | None) -> str:
    if value is None:
        return "Not recorded"
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def parse_report_datetime(report_date: str, report_time: str) -> tuple[list[str], datetime | None]:
    errors: list[str] = []
    try:
        parsed = datetime.strptime(f"{report_date} {report_time}", "%Y-%m-%d %H:%M").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        errors.append("UTC date must use YYYY-MM-DD and UTC time must use HH:MM.")
        return errors, None
    return errors, parsed


def validate_report_payload(payload: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    errors: list[str] = []
    normalised = dict(payload)
    required_text = {
        "report_type": "Report type",
        "report_date": "UTC date",
        "report_time": "UTC time",
        "location": "Location",
    }
    for field, label in required_text.items():
        if not str(normalised.get(field, "")).strip():
            errors.append(f"{label} is required.")
    if normalised.get("report_type") not in ("ELECTRONIC_LOGBOOK", "LANDING_DECLARATION"):
        errors.append("Report type must be Electronic Logbook or Landing Declaration.")
    if normalised.get("report_type") == "ELECTRONIC_LOGBOOK":
        if not str(normalised.get("species", "")).strip():
            errors.append("Species is required for an Electronic Logbook.")
        weight = str(normalised.get("catch_weight_kg", "")).strip()
        if not weight:
            errors.append("Catch weight (kg) is required for an Electronic Logbook.")
        else:
            try:
                weight_value = float(weight)
                if weight_value <= 0:
                    errors.append("Catch weight (kg) must be greater than zero.")
            except ValueError:
                errors.append("Catch weight (kg) must be a number.")
    if normalised.get("report_type") == "LANDING_DECLARATION":
        if not str(normalised.get("port", "")).strip():
            errors.append("Port is required for a Landing Declaration.")
    date_errors, parsed_datetime = parse_report_datetime(
        str(normalised.get("report_date", "")), str(normalised.get("report_time", ""))
    )
    errors.extend(date_errors)
    if parsed_datetime is not None:
        normalised["report_timestamp_utc"] = parsed_datetime.isoformat()
    return errors, normalised


def generate_filename(rss_number: str, report_date: date, sequence: int) -> str:
    if not rss_number or not re.fullmatch(r"[A-Za-z0-9]+", rss_number):
        raise ValueError("RSS Number must contain only letters and numbers.")
    if not 1 <= sequence <= 999999:
        raise ValueError("Sequence must be between 000001 and 999999.")
    return f"{rss_number}{report_date.strftime('%Y%m%d')}{sequence:06d}.xml"


def next_sequence(connection: Any, report_date: date) -> int:
    row = connection.execute(
        """
        SELECT MAX(sequence_number) AS max_sequence FROM reports
        WHERE substr(created_at_utc, 1, 10) = ?
        """,
        (report_date.isoformat(),),
    ).fetchone()
    current = row["max_sequence"] or 0
    if current >= 999999:
        raise ValueError("Daily sequence limit of 999999 reached.")
    return current + 1


def build_report_xml(
    report_number: str,
    message_id: str,
    operation: str,
    payload: dict[str, Any],
    rss_number: str,
    filename: str,
    generated_at: datetime,
) -> str:
    root = ET.Element(
        "PrototypeELSSReport",
        {
            "operation": operation,
            "messageId": message_id,
            "controlledPrototype": "true",
        },
    )
    ET.SubElement(root, "ReportNumber").text = report_number
    ET.SubElement(root, "RSSNumber").text = rss_number
    ET.SubElement(root, "GeneratedFilename").text = filename
    ET.SubElement(root, "GeneratedAtUTC").text = generated_at.isoformat()
    payload_element = ET.SubElement(root, "CompleteReportPayload")
    for key in sorted(payload):
        if key != "report_timestamp_utc":
            ET.SubElement(payload_element, key).text = str(payload[key])
    ET.indent(root)
    return ET.tostring(root, encoding="unicode", xml_declaration=True)


def _development_key(connection: Any) -> bytes:
    row = connection.execute(
        "SELECT value FROM settings WHERE key = 'development_encryption_key'"
    ).fetchone()
    if row is None:
        key = Fernet.generate_key()
        connection.execute(
            "INSERT INTO settings (key, value) VALUES ('development_encryption_key', ?)",
            (key.decode("ascii"),),
        )
        connection.commit()
        return key
    return row["value"].encode("ascii")


def encrypt_payload(connection: Any, plaintext: str) -> str:
    key = _development_key(connection)
    return Fernet(key).encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_payload(connection: Any, token: str) -> str:
    key = _development_key(connection)
    try:
        return Fernet(key).decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("Development encryption token is invalid.") from exc


def load_payload_json(payload_json: str) -> dict[str, Any]:
    return json.loads(payload_json)
