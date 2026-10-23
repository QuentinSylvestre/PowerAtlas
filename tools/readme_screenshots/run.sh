#!/bin/bash
# Regenerate docs/screenshots/*.png from synthetic data. Linux, throwaway machine or container only:
# it takes over /home/demo and port 4915, and kills processes whose command line contains /home/demo/bin/.
# Needs a venv with the app's web dependencies plus `playwright` and Pillow:  PA_DEMO_PY=<venv python>
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
PY="${PA_DEMO_PY:-python3}"
export HOME=/home/demo PA_PORT=4915 PYTHONPATH="$REPO/src"
mkdir -p "$HOME/bin" /tmp/pa-demo
pkill -f 'readme_screenshots/serve.py' || true
pkill -f '/home/demo/bin/' || true
sleep 1
rm -rf "$HOME/.config/power-atlas" /tmp/pa-demo/fake_kiro.log
"$PY" "$HERE/seed.py"
# a process named `claude`/`codex` is what presence detection looks for; the stand-in agent is `kiro-cli`
rm -f "$HOME/bin/claude" "$HOME/bin/codex"
ln -s "$(readlink -f "$(command -v python3)")" "$HOME/bin/claude"
ln -s "$(readlink -f "$(command -v python3)")" "$HOME/bin/codex"
install -m 755 "$HERE/fake_kiro.py" "$HOME/bin/kiro-cli"
"$PY" "$HERE/live.py"
PATH="$HOME/bin:$PATH" nohup "$PY" "$HERE/serve.py" > /tmp/pa-demo/serve.log 2>&1 &
echo $! > /tmp/pa-demo/serve.pid
sleep 8
"$PY" "$HERE/drive.py"      # loads the two ACP sessions and sends their prompts
"$PY" "$HERE/touch.py"      # keeps the two terminal sessions fresh
sleep 3
"$PY" "$HERE/capture.py"
kill "$(cat /tmp/pa-demo/serve.pid)" || true
pkill -f '/home/demo/bin/' || true
