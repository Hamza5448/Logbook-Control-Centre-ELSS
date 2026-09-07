# ELSS Reporting & Transmission Management System

This is an academic, controlled prototype for **Part 2** of the Software Quality Engineering assignment. It implements the locked ten-requirement scope from the selected UK Fishing Vessel Electronic Logbook SRS.

The application provides a runnable GUI for manual report entry, GBRRN-based filename generation, transmission tracking, acknowledgement correlation, whole-report corrections, and daily transmission-rule simulation. External UK Fisheries ERS communication is simulated and no production regulatory infrastructure is contacted.

## Technology

- Python 3.11 or later
- Flask
- SQLite
- HTML5, CSS3, and vanilla JavaScript-free server-rendered templates
- `cryptography` for a development-only encrypted attachment demonstration

## Prerequisites

- Python 3.11 or later
- `pip`

## Installation

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Database initialisation

The SQLite database and demo data are initialised automatically when the application starts. The default database path is:

```text
instance/elss.sqlite3
```

To start with a clean database, delete `instance/elss.sqlite3` and restart the application.

## How to run

```bash
python app.py
```

Open:

```text
http://127.0.0.1:5000/
```

### Deploy publicly with Render

GitHub stores the source code but does not run the Flask application. To create
a public link that works without the same Wi-Fi network, open Render, choose
**New +** -> **Blueprint**, connect this repository, and select `render.yaml`.
Render will install the dependencies and start the web service using Gunicorn.
The generated `onrender.com` URL can then be shared with the team.

The included SQLite database is suitable for this academic demonstration. A
production deployment should use persistent storage or a managed database.

### Daily report email

The application checks every 24 hours and emails all reports generated during
the current UTC day to `chhamzaahmad6@gmail.com`. Configure an SMTP server
before starting the application:

```text
ELSS_SMTP_HOST=smtp.example.com
ELSS_SMTP_PORT=587
ELSS_SMTP_USERNAME=your-user
ELSS_SMTP_PASSWORD=your-password
ELSS_SMTP_FROM=your-sender@example.com
ELSS_REPORT_EMAIL=chhamzaahmad6@gmail.com
```

The **Send daily report** button in Reports triggers the same delivery path for
testing. Do not commit SMTP credentials.

### Open on a mobile device

Connect the phone and computer to the same Wi-Fi network, start the app with
`python app.py`, and allow Python through the Windows firewall for private
networks if prompted. Find the computer's IPv4 address with `ipconfig`, then
open this address on the phone:

```text
http://<computer-ipv4-address>:5000/
```

For example, if the computer address is `192.168.100.72`, open
`http://192.168.100.72:5000/`. Do not use `127.0.0.1` or `localhost` on the
phone; those refer to the phone itself.

## Demo credentials

- Username: `fisherman`
- Password: `elss-demo`

## Demo flow for the ten selected requirements

| Requirement | Demo flow |
|---|---|
| `Capture-002` | Sign in, open **Create Report**, complete the representative fields, and submit. Required-field validation is shown if fields are missing. |
| `Transmission-002` | Create a report. The generated filename is displayed on the report detail screen using `[RSSNumber][YYYYMMDD][999999].xml`. |
| `Transmission-005` | Queue and send a report, then open **Transmission Monitor**. Transmission status, UTC time, acknowledgement status, and encryption status are displayed. |
| `Acknowledgement-001` | Open a sent transmission and simulate a successful, rejected, or unmatched acknowledgement. Correlation is displayed as `MATCHED` or `UNMATCHED`. |
| `Acknowledgement-002` | Simulate a successful ACK to see acceptance details, or a rejected ACK to see the rejection message. |
| `Correction-001` | Acknowledge a report, open **Corrections**, select the report, edit one or more fields, generate the COR report, and inspect the complete XML payload before transmission. |
| `Frequency-001` | Open **Trip Controls**. Use the port, catch, landing-declaration, and simulation-time controls to observe the daily deadline, port exception, and resumption after leaving port. |
| `Capture-005` | The interface uses English (UK) spelling and terminology throughout. |
| `Capture-006` | All date and time displays use UTC and are labelled accordingly. |
| `Transmission-003` | Send a transmission. The transmission detail screen shows the encrypted attachment demonstration and `Encryption: SUCCESS — development-only key`. |

## Known limitations

- This is a controlled academic prototype, not a production ELSS.
- The ERS authority is simulated within the application.
- The report fields are a representative subset, not the complete UK ERS data model.
- The XML is a controlled prototype representation and is not claimed to be UK XSD-conformant.
- The encryption demonstration uses a development-only `cryptography` envelope and a generated test key. It does not use a real UK Fisheries production PGP public key and does not perform production key management.
- The daily transmission rule is demonstrated through a controlled simulation clock rather than a real vessel schedule or satellite connection.

## Requirement traceability

See `docs/REQUIREMENT_TRACEABILITY.md`.

## AI development record

See `docs/AI_DEVELOPMENT_LOG.md`.
