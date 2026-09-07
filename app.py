from __future__ import annotations

import os
import json
import smtplib
import sqlite3
import threading
import time as clock
from datetime import date, datetime, time, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from flask import (
    Flask,
    flash,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

from elss.database import close_db, get_db, init_db
from elss.services import (
    build_report_xml,
    encrypt_payload,
    format_utc,
    load_payload_json,
    generate_filename,
    next_sequence,
    parse_report_datetime,
    validate_report_payload,
)


ROOT = Path(__file__).resolve().parent
app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("ELSS_SECRET_KEY", "elss-development-secret")
app.teardown_appcontext(close_db)
app.jinja_env.globals["format_utc"] = format_utc


REPORT_TYPES = {
    "ELECTRONIC_LOGBOOK": "Electronic Logbook",
    "LANDING_DECLARATION": "Landing Declaration",
}


def db() -> sqlite3.Connection:
    return get_db()


def now() -> datetime:
    trip = current_trip()
    if trip and trip["simulated_now_utc"]:
        return datetime.fromisoformat(trip["simulated_now_utc"])
    return datetime.now(timezone.utc)


def current_trip() -> sqlite3.Row | None:
    if "trip" not in g:
        g.trip = db().execute(
            "SELECT * FROM trips WHERE status = 'CURRENT' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    return g.trip


def current_user() -> sqlite3.Row | None:
    user_id = session.get("user_id")
    if not user_id:
        return None
    return db().execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


@app.before_request
def require_login() -> Any:
    public_routes = {"login", "static"}
    if request.endpoint in public_routes:
        if request.endpoint == "login":
            init_db()
        return None
    if not session.get("user_id"):
        return redirect(url_for("login"))
    init_db()
    if current_trip() is None:
        g.pop("trip", None)
    return None


@app.context_processor
def inject_globals() -> dict[str, Any]:
    trip = current_trip()
    return {
        "current_user": current_user(),
        "trip": trip,
        "utc_now": format_utc(now()),
        "frequency_state": frequency_state() if trip else {},
    }


def frequency_state() -> dict[str, Any]:
    trip = current_trip()
    if trip is None:
        return {}
    current = now()
    deadline = datetime.combine(current.date() + timedelta(days=1), time.min, timezone.utc)
    port_exception = (
        trip["vessel_status"] == "IN_PORT"
        and trip["catch_on_board"] == 0
        and trip["landing_declaration_submitted"] == 1
    )
    latest = db().execute(
        """
        SELECT t.* FROM transmissions t
        JOIN reports r ON r.id = t.report_id
        WHERE t.transmission_status IN ('TRANSMITTED', 'ACKNOWLEDGED')
        ORDER BY t.sent_at_utc DESC LIMIT 1
        """
    ).fetchone()
    satisfied_today = bool(
        latest
        and latest["sent_at_utc"]
        and datetime.fromisoformat(latest["sent_at_utc"]).date() == current.date()
    )
    if port_exception:
        state = "PORT_EXCEPTION_ACTIVE"
        message = "Port exception active: in port, no catch on board, and landing declaration submitted."
    elif satisfied_today:
        state = "SATISFIED_TODAY"
        message = "Daily transmission requirement satisfied for the current UTC date."
    elif current >= deadline:
        state = "OVERDUE"
        message = "Daily transmission deadline has passed."
    else:
        state = "DUE_BEFORE_DEADLINE"
        message = "Transmission is due before 24:00 UTC."
    return {
        "state": state,
        "message": message,
        "deadline": f"{current.date().isoformat()} 24:00 UTC",
        "latest": latest,
    }


def report_payload_from_form(form: dict[str, str]) -> dict[str, Any]:
    return {
        "report_type": form.get("report_type", ""),
        "report_date": form.get("report_date", ""),
        "report_time": form.get("report_time", ""),
        "location": form.get("location", "").strip(),
        "species": form.get("species", "").strip(),
        "catch_weight_kg": form.get("catch_weight_kg", "").strip(),
        "port": form.get("port", "").strip(),
        "notes": form.get("notes", "").strip(),
    }


def create_report(payload: dict[str, Any], operation: str = "DAT") -> sqlite3.Row | None:
    errors, normalised = validate_report_payload(payload)
    if errors:
        for error in errors:
            flash(error, "error")
        return None
    current = now()
    rss_number = current_trip()["rss_number"]
    try:
        sequence = next_sequence(db(), current.date())
        filename = generate_filename(rss_number, current.date(), sequence)
    except ValueError as error:
        flash(f"Filename generation failed: {error}", "error")
        return None
    message_id = f"MSG-{current.strftime('%Y%m%d')}-{sequence:06d}"
    report_number = f"RPT-{current.strftime('%Y%m%d')}-{sequence:06d}"
    xml_content = build_report_xml(
        report_number,
        message_id,
        operation,
        normalised,
        rss_number,
        filename,
        current,
    )
    cursor = db().execute(
        """
        INSERT INTO reports (
            report_number, report_type, trip_id, operation, message_id,
            sequence_number, status, filename, xml_content, payload_json,
            created_at_utc, updated_at_utc
        ) VALUES (?, ?, ?, ?, ?, ?, 'VALIDATED', ?, ?, ?, ?, ?)
        """,
        (
            report_number,
            normalised["report_type"],
            current_trip()["id"],
            operation,
            message_id,
            sequence,
            filename,
            xml_content,
            json.dumps(normalised, sort_keys=True),
            current.isoformat(),
            current.isoformat(),
        ),
    )
    db().commit()
    return db().execute("SELECT * FROM reports WHERE id = ?", (cursor.lastrowid,)).fetchone()


def send_daily_report_email(period_start: datetime | None = None) -> tuple[bool, str]:
    """Send generated reports for a UTC day or rolling period when SMTP is configured."""
    current = now()
    start = period_start or datetime.combine(current.date(), time.min, timezone.utc)
    reports = db().execute(
        """
        SELECT * FROM reports
        WHERE created_at_utc >= ? AND created_at_utc < ?
        ORDER BY id
        """,
        (
            start.isoformat(),
            current.isoformat() if period_start else datetime.combine(current.date() + timedelta(days=1), time.min, timezone.utc).isoformat(),
        ),
    ).fetchall()
    if not reports:
        return False, "No reports were generated during the current UTC day."
    smtp_host = os.environ.get("ELSS_SMTP_HOST", "")
    recipient = os.environ.get("ELSS_REPORT_EMAIL", "chhamzaahmad6@gmail.com")
    if not smtp_host:
        return False, "Daily email is waiting for ELSS_SMTP_HOST configuration."
    message = EmailMessage()
    message["Subject"] = f"ELSS daily report - {current.date().isoformat()} UTC"
    message["From"] = os.environ.get("ELSS_SMTP_FROM", recipient)
    message["To"] = recipient
    message.set_content(
        "Generated ELSS reports for the current UTC day:\n\n"
        + "\n".join(f"- {report['report_number']} ({report['operation']})" for report in reports)
    )
    for report in reports:
        message.add_attachment(
            report["xml_content"].encode("utf-8"),
            maintype="application",
            subtype="xml",
            filename=report["filename"],
        )
    smtp_port = int(os.environ.get("ELSS_SMTP_PORT", "587"))
    with smtplib.SMTP(smtp_host, smtp_port, timeout=20) as smtp:
        smtp.starttls()
        username = os.environ.get("ELSS_SMTP_USERNAME", "")
        password = os.environ.get("ELSS_SMTP_PASSWORD", "")
        if username:
            smtp.login(username, password)
        smtp.send_message(message)
    return True, f"Daily report email sent to {recipient}."


def start_daily_email_scheduler() -> None:
    interval = int(os.environ.get("ELSS_EMAIL_INTERVAL_SECONDS", "86400"))

    def run() -> None:
        while True:
            clock.sleep(interval)
            with app.app_context():
                try:
                    send_daily_report_email(now() - timedelta(seconds=interval))
                except (OSError, smtplib.SMTPException, ValueError):
                    pass

    threading.Thread(target=run, name="elss-daily-email", daemon=True).start()


@app.route("/login", methods=["GET", "POST"])
def login() -> Any:
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = db().execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        if user is None or not check_password_hash(user["password_hash"], password):
            flash("Invalid username or password.", "error")
        else:
            session.clear()
            session["user_id"] = user["id"]
            flash(f"Signed in as {user['username']} ({user['role']}).", "success")
            return redirect(url_for("dashboard"))
    return render_template("login.html")


@app.route("/logout", methods=["POST"])
def logout() -> Any:
    session.clear()
    flash("Signed out.", "success")
    return redirect(url_for("login"))


@app.route("/")
def dashboard() -> Any:
    trip = current_trip()
    latest_transmission = db().execute(
        """
        SELECT t.*, r.report_number
        FROM transmissions t JOIN reports r ON r.id = t.report_id
        ORDER BY t.id DESC LIMIT 1
        """
    ).fetchone()
    pending_acknowledgement = db().execute(
        "SELECT COUNT(*) AS count FROM transmissions WHERE ack_status = 'PENDING'"
    ).fetchone()["count"]
    reports = db().execute("SELECT * FROM reports ORDER BY id DESC LIMIT 5").fetchall()
    return render_template(
        "dashboard.html",
        latest_transmission=latest_transmission,
        pending_acknowledgement=pending_acknowledgement,
        reports=reports,
    )


@app.route("/reports")
def reports() -> Any:
    all_reports = db().execute("SELECT * FROM reports ORDER BY id DESC").fetchall()
    return render_template("reports.html", reports=all_reports)


@app.route("/reports/new", methods=["GET", "POST"])
def new_report() -> Any:
    if request.method == "POST":
        report = create_report(report_payload_from_form(request.form.to_dict()))
        if report:
            flash("Report created and validated successfully.", "success")
            return redirect(url_for("report_detail", report_id=report["id"]))
    return render_template("report_form.html", report_types=REPORT_TYPES)


@app.route("/reports/<int:report_id>")
def report_detail(report_id: int) -> Any:
    report = db().execute("SELECT * FROM reports WHERE id = ?", (report_id,)).fetchone()
    if report is None:
        flash("Report not found.", "error")
        return redirect(url_for("reports"))
    transmissions = db().execute(
        "SELECT * FROM transmissions WHERE report_id = ? ORDER BY id DESC", (report_id,)
    ).fetchall()
    corrected_report = None
    if report["corrected_report_id"]:
        corrected_report = db().execute(
            "SELECT * FROM reports WHERE id = ?", (report["corrected_report_id"],)
        ).fetchone()
    return render_template(
        "report_detail.html",
        report=report,
        transmissions=transmissions,
        corrected_report=corrected_report,
    )


@app.route("/reports/<int:report_id>/delete", methods=["POST"])
def delete_report(report_id: int) -> Any:
    report = db().execute("SELECT * FROM reports WHERE id = ?", (report_id,)).fetchone()
    if report is None:
        flash("Report not found.", "error")
        return redirect(url_for("reports"))
    db().execute("DELETE FROM transmissions WHERE report_id = ?", (report_id,))
    db().execute(
        "UPDATE reports SET corrected_report_id = NULL WHERE corrected_report_id = ?",
        (report_id,),
    )
    db().execute("DELETE FROM reports WHERE id = ?", (report_id,))
    db().commit()
    flash(f"Report {report['report_number']} and its transmissions were deleted.", "success")
    return redirect(url_for("reports"))


@app.route("/reports/email-daily", methods=["POST"])
def email_daily_reports() -> Any:
    try:
        sent, message = send_daily_report_email()
    except (OSError, smtplib.SMTPException, ValueError) as error:
        sent, message = False, f"Daily email failed: {error}"
    flash(message, "success" if sent else "error")
    return redirect(url_for("reports"))


@app.route("/reports/<int:report_id>/queue", methods=["POST"])
def queue_report(report_id: int) -> Any:
    report = db().execute("SELECT * FROM reports WHERE id = ?", (report_id,)).fetchone()
    if report is None:
        flash("Report not found.", "error")
        return redirect(url_for("reports"))
    if report["status"] != "VALIDATED":
        flash("Only validated reports can be queued for transmission.", "error")
        return redirect(url_for("report_detail", report_id=report_id))
    cursor = db().execute(
        """
        INSERT INTO transmissions (
            report_id, operation, transmission_status, sent_at_utc,
            ack_status, correlation_status, encrypted
        ) VALUES (?, ?, 'PENDING', NULL, 'PENDING', 'PENDING', 0)
        """,
        (report_id, report["operation"]),
    )
    db().execute(
        "UPDATE reports SET status = 'TRANSMISSION_PENDING', updated_at_utc = ? WHERE id = ?",
        (now().isoformat(), report_id),
    )
    db().commit()
    flash("Transmission queued as PENDING.", "success")
    return redirect(url_for("transmission_detail", transmission_id=cursor.lastrowid))


@app.route("/transmissions")
def transmissions() -> Any:
    rows = db().execute(
        """
        SELECT t.*, r.report_number, r.filename AS report_filename
        FROM transmissions t JOIN reports r ON r.id = t.report_id
        ORDER BY t.id DESC
        """
    ).fetchall()
    return render_template("transmissions.html", transmissions=rows)


@app.route("/transmissions/<int:transmission_id>")
def transmission_detail(transmission_id: int) -> Any:
    transmission = db().execute(
        """
        SELECT t.*, r.report_number, r.filename AS report_filename, r.xml_content,
               r.payload_json, r.message_id
        FROM transmissions t JOIN reports r ON r.id = t.report_id
        WHERE t.id = ?
        """,
        (transmission_id,),
    ).fetchone()
    if transmission is None:
        flash("Transmission not found.", "error")
        return redirect(url_for("transmissions"))
    return render_template("transmission_detail.html", transmission=transmission)


@app.route("/transmissions/<int:transmission_id>/send", methods=["POST"])
def send_transmission(transmission_id: int) -> Any:
    transmission = db().execute(
        "SELECT t.*, r.xml_content FROM transmissions t JOIN reports r ON r.id = t.report_id WHERE t.id = ?",
        (transmission_id,),
    ).fetchone()
    if transmission is None:
        flash("Transmission not found.", "error")
        return redirect(url_for("transmissions"))
    if transmission["transmission_status"] != "PENDING":
        flash("Only PENDING transmissions can be sent.", "error")
        return redirect(url_for("transmission_detail", transmission_id=transmission_id))
    try:
        encrypted_payload = encrypt_payload(db(), transmission["xml_content"])
    except Exception:
        flash("Encryption failed. Transmission was not sent.", "error")
        db().execute(
            "UPDATE transmissions SET transmission_status = 'FAILED', ack_status = 'NOT_APPLICABLE' WHERE id = ?",
            (transmission_id,),
        )
        db().execute(
            "UPDATE reports SET status = 'TRANSMISSION_FAILED', updated_at_utc = ? WHERE id = ?",
            (now().isoformat(), transmission["report_id"]),
        )
        db().commit()
        return redirect(url_for("transmission_detail", transmission_id=transmission_id))
    current = now()
    db().execute(
        """
        UPDATE transmissions
        SET transmission_status = 'TRANSMITTED', sent_at_utc = ?, encrypted = 1,
            encrypted_payload = ?, ack_status = 'PENDING'
        WHERE id = ?
        """,
        (current.isoformat(), encrypted_payload, transmission_id),
    )
    db().execute(
        "UPDATE reports SET status = 'TRANSMITTED', updated_at_utc = ? WHERE id = ?",
        (current.isoformat(), transmission["report_id"]),
    )
    db().commit()
    flash("Transmission sent to the simulated ERS authority. Encryption: SUCCESS.", "success")
    return redirect(url_for("transmission_detail", transmission_id=transmission_id))


@app.route("/transmissions/<int:transmission_id>/ack", methods=["POST"])
def simulate_acknowledgement(transmission_id: int) -> Any:
    ack_type = request.form.get("ack_type", "")
    transmission = db().execute(
        "SELECT t.*, r.message_id FROM transmissions t JOIN reports r ON r.id = t.report_id WHERE t.id = ?",
        (transmission_id,),
    ).fetchone()
    if transmission is None:
        flash("Transmission not found.", "error")
        return redirect(url_for("transmissions"))
    if transmission["transmission_status"] not in ("TRANSMITTED", "ACKNOWLEDGED"):
        flash("Acknowledgement can only be simulated after a successful transmission.", "error")
        return redirect(url_for("transmission_detail", transmission_id=transmission_id))
    current = now()
    ack_id = f"ACK-{current.strftime('%Y%m%d%H%M%S')}-{transmission_id:04d}"
    if ack_type == "success":
        correlation = "MATCHED"
        ack_status = "ACCEPTED"
        ack_message = "Report accepted by simulated ERS authority."
        transmission_status = "ACKNOWLEDGED"
        report_status = "ACKNOWLEDGED"
    elif ack_type == "rejected":
        correlation = "MATCHED"
        ack_status = "REJECTED"
        ack_message = "Rejected by simulated ERS authority: representative validation error in submitted report data."
        transmission_status = "TRANSMITTED"
        report_status = "ACKNOWLEDGEMENT_REJECTED"
    elif ack_type == "unmatched":
        correlation = "UNMATCHED"
        ack_status = "UNMATCHED"
        ack_message = "Acknowledgement message ID did not match an outgoing report and operation."
        transmission_status = "TRANSMITTED"
        report_status = "TRANSMITTED"
    else:
        flash("Invalid acknowledgement type.", "error")
        return redirect(url_for("transmission_detail", transmission_id=transmission_id))
    db().execute(
        """
        UPDATE transmissions
        SET transmission_status = ?, ack_status = ?, ack_message = ?,
            correlation_status = ?, ack_id = ?, ack_at_utc = ?,
            ack_operation = ?
        WHERE id = ?
        """,
        (
            transmission_status,
            ack_status,
            ack_message,
            correlation,
            ack_id,
            current.isoformat(),
            transmission["operation"],
            transmission_id,
        ),
    )
    db().execute(
        "UPDATE reports SET status = ?, updated_at_utc = ? WHERE id = ?",
        (report_status, current.isoformat(), transmission["report_id"]),
    )
    db().commit()
    flash(f"Acknowledgement recorded: {ack_status}, correlation {correlation}.", "success")
    return redirect(url_for("transmission_detail", transmission_id=transmission_id))


@app.route("/corrections")
def corrections() -> Any:
    eligible = db().execute(
        "SELECT * FROM reports WHERE status = 'ACKNOWLEDGED' ORDER BY id DESC"
    ).fetchall()
    corrections = db().execute(
        "SELECT * FROM reports WHERE operation = 'COR' ORDER BY id DESC"
    ).fetchall()
    return render_template("corrections.html", eligible=eligible, corrections=corrections)


@app.route("/corrections/<int:report_id>", methods=["GET", "POST"])
def create_correction(report_id: int) -> Any:
    original = db().execute("SELECT * FROM reports WHERE id = ?", (report_id,)).fetchone()
    if original is None:
        flash("Original report not found.", "error")
        return redirect(url_for("corrections"))
    if original["status"] != "ACKNOWLEDGED":
        flash("Only acknowledged current reports can be corrected.", "error")
        return redirect(url_for("corrections"))
    original_payload = load_payload_json(original["payload_json"])
    if request.method == "POST":
        payload = report_payload_from_form(request.form.to_dict())
        payload["report_type"] = original["report_type"]
        correction = create_report(payload, operation="COR")
        if correction:
            db().execute(
                "UPDATE reports SET corrected_report_id = ? WHERE id = ?",
                (correction["id"], original["id"]),
            )
            db().execute(
                "UPDATE reports SET status = 'CORRECTED', updated_at_utc = ? WHERE id = ?",
                (now().isoformat(), original["id"]),
            )
            db().commit()
            flash("Correction generated. The complete corrected report is ready for transmission.", "success")
            return redirect(url_for("report_detail", report_id=correction["id"]))
    return render_template(
        "correction_form.html", original=original, original_payload=original_payload
    )


@app.route("/trip", methods=["GET", "POST"])
def trip_controls() -> Any:
    trip = current_trip()
    if request.method == "POST":
        action = request.form.get("action", "")
        current = now()
        if action == "enter_port":
            db().execute(
                "UPDATE trips SET vessel_status = 'IN_PORT', simulated_now_utc = ? WHERE id = ?",
                (current.isoformat(), trip["id"]),
            )
            flash("Vessel status changed to IN PORT.", "success")
        elif action == "leave_port":
            db().execute(
                "UPDATE trips SET vessel_status = 'AT_SEA', simulated_now_utc = ? WHERE id = ?",
                (current.isoformat(), trip["id"]),
            )
            flash("Vessel left port. Daily transmission checking has resumed.", "success")
        elif action == "submit_landing_declaration":
            db().execute(
                "UPDATE trips SET landing_declaration_submitted = 1, simulated_now_utc = ? WHERE id = ?",
                (current.isoformat(), trip["id"]),
            )
            flash("Landing declaration marked as submitted.", "success")
        elif action == "last_fishing_operation":
            db().execute(
                "UPDATE trips SET catch_on_board = 1, last_fishing_operation_at_utc = ?, simulated_now_utc = ? WHERE id = ?",
                (current.isoformat(), current.isoformat(), trip["id"]),
            )
            flash("Last fishing operation recorded and catch marked on board.", "success")
        elif action == "clear_catch":
            db().execute(
                "UPDATE trips SET catch_on_board = 0, simulated_now_utc = ? WHERE id = ?",
                (current.isoformat(), trip["id"]),
            )
            flash("Catch on board cleared for controlled port-exception testing.", "success")
        elif action == "advance_time":
            hours = request.form.get("hours", "0")
            try:
                hours_value = float(hours)
                if hours_value <= 0 or hours_value > 24:
                    raise ValueError
            except ValueError:
                flash("Advance time must be a number greater than 0 and no more than 24 hours.", "error")
                return redirect(url_for("trip_controls"))
            new_time = current + timedelta(hours=hours_value)
            db().execute(
                "UPDATE trips SET simulated_now_utc = ? WHERE id = ?",
                (new_time.isoformat(), trip["id"]),
            )
            flash(f"Simulation time advanced by {hours_value:g} hour(s).", "success")
        elif action == "set_time":
            new_date = request.form.get("simulated_date", "")
            new_time_value = request.form.get("simulated_time", "")
            try:
                parsed = datetime.strptime(f"{new_date} {new_time_value}", "%Y-%m-%d %H:%M").replace(
                    tzinfo=timezone.utc
                )
            except ValueError:
                flash("Set time must use YYYY-MM-DD and HH:MM in UTC.", "error")
                return redirect(url_for("trip_controls"))
            db().execute(
                "UPDATE trips SET simulated_now_utc = ? WHERE id = ?",
                (parsed.isoformat(), trip["id"]),
            )
            flash("Simulation time updated.", "success")
        else:
            flash("Invalid trip action.", "error")
        db().commit()
        g.pop("trip", None)
        return redirect(url_for("trip_controls"))
    latest_transmission = db().execute(
        "SELECT * FROM transmissions ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return render_template("trip.html", latest_transmission=latest_transmission)


if __name__ == "__main__":
    with app.app_context():
        init_db()
    start_daily_email_scheduler()
    app.run(host=os.environ.get("ELSS_HOST", "0.0.0.0"), port=5000, debug=True, use_reloader=False)
