# v0.2 application

The FastAPI application in `app/` is a small orchestration layer around the proven CLI engines. SQLite persists jobs and events. The worker polls incoming content, ignores incomplete suffixes, waits for a configurable unchanged interval, and creates one durable job per source fingerprint.

PS3 folders call `ps3_ingest.py`; that engine remains responsible for IRD validation, immutable-source reconstruction, ISO creation, and fail-closed publication. Other supported formats are passed to `ps3-import-incoming.py --source` so existing PS1/PS2/PSP/PKG routing remains available.

The setup wizard writes non-secret configuration and creates required directories. Doctor checks permissions, storage, tools, parser availability, and optional endpoints. A failed integration is shown as a human-readable warning/failure, never as a raw traceback.

Home presents Search first, active PS3 downloads/processing second, and the ready Library third. SAB monitoring is read-only and shows progress, speed, and ETA for the configured PS3 category. Durable ingest jobs continue the same visible title through validation/build states; completed titles appear in Ready Library.

Search calls the existing `ps3-search` helper and retains category 1080 versus category-less fallback. A result action opens a scoped search in Prowlarr; the application does not call its grab endpoint or alter SAB routing. Mount actions require an explicitly configured safe URL template; automatic game launching is never implemented.

Refresh remains relay-based when configured. The relay owns PS3 idle detection and safe XMB reload; HTTP 202 is treated as pending and never interrupts gameplay.
