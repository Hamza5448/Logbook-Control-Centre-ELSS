# Part 2 Scope

## In scope

- Manual report entry for representative Electronic Logbook and Landing Declaration data.
- GBRRN-based filename generation using `[RSSNumber][YYYYMMDD][999999].xml`.
- Transmission tracking with explicit statuses and UTC timestamps.
- Acknowledgement correlation for `DAT` and `COR` operations.
- Successful and rejected acknowledgement display, including error messages.
- Whole-report correction generation and transmission.
- Daily transmission rule simulation, including the port exception and resumption after leaving port.
- English (UK) interface language.
- UTC storage and display.
- Controlled encrypted-attachment demonstration using a development-only test key.

## Out of scope

- Real UK Fisheries ERS server integration.
- Real regulatory email infrastructure.
- Real production PGP keys.
- Real vessel hardware.
- Real GPS.
- Physical moving-vessel testing.
- Real satellite communication.
- Complete commercial ELSS implementation.
