# Sourced into the dev container's shell by postCreate.sh. Run `api` in one
# terminal and `web` in another; ports 8001 and 5173 are forwarded to the host,
# which is where web/src/api.js expects the API to be (http://localhost:8001).

api() {
  echo "Starting Runway API on :8001…"
  cd "$WORKSPACE_FOLDER/server" || return 1
  python -m uvicorn app.main:app --reload --port 8001 --host 0.0.0.0
}

web() {
  echo "Starting Runway web on :5173…"
  cd "$WORKSPACE_FOLDER/web" || return 1
  npm run dev -- --host 0.0.0.0
}

tests() {
  cd "$WORKSPACE_FOLDER/server" || return 1
  python -m pytest "$@"
}
