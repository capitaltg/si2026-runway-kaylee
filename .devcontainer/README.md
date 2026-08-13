# Running Runway in a dev container

Works the same on macOS, Windows (Docker Desktop + WSL2) and Linux. You need
Docker Desktop running, VS Code, and the **Dev Containers** extension.

## Start it

1. Open this folder in VS Code.
2. `Ctrl+Shift+P` → **Dev Containers: Reopen in Container**. The first build
   takes 10–20 minutes (Python 3.12, Node 20, then the dependency installs in
   `postCreate.sh`). Later starts take seconds.
3. Open two terminals (`` Ctrl+` ``, then `+` for the second) and run:
   - `api` — the FastAPI server on :8001
   - `web` — the Vite dev server on :5173
4. Browse to <http://localhost:5173>.

`tests` runs pytest from `server/`.

## Two things Docker can't bring with it

Both are gitignored on purpose, so a fresh clone won't have them.

**`server/runway.db`** — the SQLite database. If it's missing, the app still
starts: `db.init_db()` runs on the FastAPI startup event and creates an empty
schema, so you get a working app with no contracts in it. To bring your data
from another machine, copy that one file into `server/` and restart `api`.

**`server/.env.local`** — AI credentials. Copy it from your other machine, or
build one from `server/.env.example`. Without it the ingest and Ask Runway
routes fail; everything else works. `app/extract.py` loads it by absolute path,
so it does not matter which directory you launch from.

## Talking to Fixtura

`FIXTURA_URL` is set to `http://host.docker.internal:8000` in
`devcontainer.json`, which is the Fixtura dev container's published port seen
from inside this one. Start Fixtura's container first, then sync from Runway as
usual. Nothing in the app changed for this — `server/app/sources.py` already
read `FIXTURA_URL` from the environment.

If `host.docker.internal` fails to resolve on your setup, the fallback is a
shared Docker network:

```bash
docker network create si2026
```

then add `"--network=si2026"` to `runArgs` in both repos' `devcontainer.json`
(plus `"--network-alias=fixtura"` on Fixtura's) and set `FIXTURA_URL` to
`http://fixtura:8000`.

## Notes

- `web/node_modules` lives in a named Docker volume, so a copy of this repo made
  on a different OS won't shadow the container's binaries. Delete the volume
  (`docker volume rm runway-node-modules`) if you ever need a clean reinstall.
- Python packages install into the container's own interpreter, not a `.venv`.
  A rebuild reinstalls them from `server/requirements.txt`.
- The database is a plain file in the workspace, so each machine has its own
  independent copy. Data made on one does not appear on the other.
