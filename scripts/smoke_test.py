from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


def run_smoke_test() -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    expected_date = datetime.now(timezone.utc).strftime("%Y%m%d")
    temporary_directory = tempfile.TemporaryDirectory()
    database_path = Path(temporary_directory.name) / "elss-smoke.sqlite3"
    os.environ["ELSS_DB"] = str(database_path)

    from app import app, init_db

    app.config["TESTING"] = True
    with app.app_context():
        init_db()

    client = app.test_client()
    response = client.post(
        "/login",
        data={"username": "fisherman", "password": "elss-demo"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Dashboard" in response.data

    response = client.get("/reports/new")
    assert response.status_code == 200
    assert b"Create report" in response.data

    response = client.post(
        "/reports/new",
        data={
            "report_type": "ELECTRONIC_LOGBOOK",
            "report_date": "2026-09-07",
            "report_time": "10:30",
            "location": "Representative fishing area 1",
            "species": "Cod",
            "catch_weight_kg": "120.50",
            "port": "",
            "notes": "Initial controlled report",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"VALID" in response.data
    assert f"RSS123456{expected_date}000001.xml".encode() in response.data

    response = client.post("/reports/1/queue", follow_redirects=True)
    assert response.status_code == 200
    assert b"PENDING" in response.data

    response = client.post("/transmissions/1/send", follow_redirects=True)
    assert response.status_code == 200
    assert b"TRANSMITTED" in response.data
    assert b"Encryption: SUCCESS" in response.data

    response = client.post(
        "/transmissions/1/ack",
        data={"ack_type": "success"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"ACCEPTED" in response.data
    assert b"MATCHED" in response.data

    response = client.get("/corrections")
    assert response.status_code == 200
    assert f"RPT-{expected_date}-000001".encode() in response.data

    response = client.post(
        "/corrections/1",
        data={
            "report_date": "2026-09-07",
            "report_time": "11:15",
            "location": "Representative fishing area 2",
            "species": "Haddock",
            "catch_weight_kg": "135.75",
            "port": "",
            "notes": "Corrected complete report",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"COR" in response.data
    assert b"CompleteReportPayload" in response.data
    assert f"RSS123456{expected_date}000002.xml".encode() in response.data

    response = client.get("/reports/1")
    assert response.status_code == 200
    assert b"Updated version available" in response.data
    assert f"RPT-{expected_date}-000002".encode() in response.data

    response = client.post("/reports/2/delete", follow_redirects=True)
    assert response.status_code == 200
    assert b"deleted" in response.data

    response = client.get("/transmissions")
    assert response.status_code == 200
    assert b"Transmission Monitor" in response.data

    response = client.get("/trip")
    assert response.status_code == 200
    assert b"Trip Controls" in response.data
    assert b"DUE_BEFORE_DEADLINE" in response.data or b"SATISFIED_TODAY" in response.data

    temporary_directory.cleanup()
    print("Smoke test passed: login, report, transmission, ACK, correction, and trip controls.")


if __name__ == "__main__":
    run_smoke_test()
