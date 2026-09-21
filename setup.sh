#!/usr/bin/env bash
# One-command setup, run after you've created accounts/keys and filled in
# .env.local (copy .env.example -> .env.local first). Safe to re-run.
#
#   ./setup.sh
#
# When it finishes, start the bot with:
#   python agent.py dev
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -f .env.local ]; then
  cp .env.example .env.local
  echo "Created .env.local from .env.example — fill in your keys, then re-run ./setup.sh"
  exit 1
fi

if [ ! -d venv ]; then
  echo "==> Creating virtualenv"
  python3 -m venv venv
fi
# shellcheck disable=SC1091
source venv/bin/activate

echo "==> Installing dependencies"
pip install -q --upgrade pip
pip install -q -r requirements.txt

echo "==> Downloading model files (Silero VAD)"
python -m livekit.agents download-files

echo "==> Configuring SIP trunk (skips cleanly if not ready yet)"
python setup_sip_trunk.py

echo
echo "Setup complete. Run the bot with:"
echo "    source venv/bin/activate && python agent.py dev"
echo "Then, in another terminal:"
echo "    source venv/bin/activate && python dispatch_call.py --scenario simple_scheduling"
