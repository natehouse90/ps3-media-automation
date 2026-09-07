# Configuration reference

Copy `examples/.env.example` to `.env`. The application reads environment defaults and stores wizard settings under the protected application state directory. The Prowlarr key is never rendered in the UI or written to logs; prefer an environment variable or Docker secret.

- `APP_STATE_ROOT`: SQLite database and protected application settings.
- `PS3_HOST_LIBRARY_ROOT`: host-side directory mounted as the container library.
- `PS3_INCOMING_DIR`, `PS3_WORK_ROOT`, `PS3_IRD_ROOT`, `PS3_ISO_ROOT`: container paths for staging, isolated reconstruction, trusted IRDs, and final images.
- `PS3_MAKEPS3ISO`: pinned builder path; absent or unverified tools fail Doctor/ingest.
- `PS3_IP`: PS3/webMAN host; `PS3NETSRV_HOST` and `PS3NETSRV_PORT`: read-only server endpoint.
- `PROWLARR_URL` and `PROWLARR_API_KEY`: optional search integration.
- `WORKER_INTERVAL_SECONDS` and `INGEST_STABLE_SECONDS`: polling and completion-stability controls.

The published library should be mounted read-only in ps3netsrv. Only incoming, state/work, and the automation's PS3ISO publication path need write access.
