# Configuration reference

Copy `examples/.env.example` to `.env`. The application reads environment defaults and stores wizard settings under the protected application state directory. The Prowlarr key is never rendered in the UI or written to logs; prefer an environment variable or Docker secret.

- `APP_STATE_ROOT`: SQLite database and protected application settings.
- `PS3_HOST_LIBRARY_ROOT`: host-side directory mounted as the container library.
- `PS3_INCOMING_DIR`, `PS3_WORK_ROOT`, `PS3_IRD_ROOT`, `PS3_ISO_ROOT`: container paths for staging, isolated reconstruction, trusted IRDs, and final images.
- `PS3_MAKEPS3ISO`: pinned builder path; absent or unverified tools fail Doctor/ingest.
- `PS3_IP`: PS3/webMAN host; `PS3NETSRV_HOST` and `PS3NETSRV_PORT`: read-only server endpoint.
- `PROWLARR_URL` and `PROWLARR_API_KEY`: optional search integration.
- `PROWLARR_SEARCH_URL`: Prowlarr search API URL; `PROWLARR_CONTAINER` may be empty for direct HTTP. `PROWLARR_PUBLIC_URL` is the browser-facing address for safe result handoff.
- `SAB_URL`, `SAB_API_KEY`, and `SAB_CATEGORY`: optional read-only Home queue monitoring. `SAB_HOST_HEADER` is an optional host-allow-list override for trusted internal container networks.
- `PS3_CAPACITY_PATHS`: comma-separated `Label=/path` values used for true storage capacity; paths on one filesystem are shown once.
- `PS3_METADATA_ROOT`: location of `known-isos.json` used to label verified library entries.
- `PS3_MOUNT_URL_TEMPLATE`: optional operator-supplied safe mount URL containing `{path}`. When absent, Home clearly disables Mount.
- `WORKER_INTERVAL_SECONDS` and `INGEST_STABLE_SECONDS`: polling and completion-stability controls.

The published library should be mounted read-only in ps3netsrv. Only incoming, state/work, and the automation's PS3ISO publication path need write access.
