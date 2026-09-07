# Installation

## Docker (recommended)

Requirements: Docker Engine with Compose v2 and a Linux host with storage for a reconstruction work tree plus final ISOs.

```sh
git clone https://github.com/YOUR-ACCOUNT/ps3-media-automation.git
cd ps3-media-automation
cp examples/.env.example .env
# Edit .env with your own paths and optional PS3/Prowlarr values.
docker compose up -d --build
```

Open `http://SERVER-IP:8787` and complete the wizard. The image runs the application as an unprivileged user. Its small root entrypoint only prepares ownership for incoming, state, work, IRD, audit, and PS3ISO directories; it does not recursively chown or alter published library contents. Mount `/opt/ps3-tools` read-only and place the separately installed, checksum-verified pinned `makeps3iso` binary there.

```sh
docker compose ps
curl http://127.0.0.1:8787/healthz
```

The default Compose paths are `./runtime-library` and `./runtime-tools`; replace them with real paths. Do not put secrets in the repository.

## CLI installation

The proven CLI runtime uses Python 3.10+ and the standard library. Install the web dependencies with `python3 -m pip install -r requirements.txt`, then install the scripts from `scripts/` into `/usr/local/sbin`. The CLI and web worker use the same ingest engine and environment variables.

For production, run the container under Compose with persistent state and library volumes. Do not run the worker against a download directory until ownership and permissions are verified.
