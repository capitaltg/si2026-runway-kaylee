#!/usr/bin/env bash
set -e

# The node_modules volume is created root-owned on first mount.
sudo mkdir -p "$WORKSPACE_FOLDER/web/node_modules"
sudo chown -R "$(id -u):$(id -g)" "$WORKSPACE_FOLDER/web/node_modules"

echo "Installing Python dependencies…"
python -m pip install --upgrade pip
python -m pip install -r "$WORKSPACE_FOLDER/server/requirements.txt"

echo "Installing web dependencies…"
npm install --prefix "$WORKSPACE_FOLDER/web"

if ! grep -qF 'services.sh' ~/.bashrc; then
  cat <<EOF >>~/.bashrc

source "$WORKSPACE_FOLDER/.devcontainer/services.sh"

EOF
fi

echo
echo "Done. Open two terminals and run 'api' in one, 'web' in the other."
