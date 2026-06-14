#!/bin/bash
set -e

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

if [ ! -f config.json ]; then
  cp config.json.example config.json
  echo "Created config.json from config.json.example — review it before running."
fi

if [ ! -f cookie_claude.txt ]; then
  echo "ERROR: cookie_claude.txt not found."
  echo "1. Log in to https://claude.ai/chats in Firefox"
  echo "2. Install 'cookies.txt' extension and export cookies"
  echo "3. Save the file as: $DIR/cookie_claude.txt"
  echo "Then run: bash start.sh"
  exit 1
fi

echo "Starting Claude proxy on http://0.0.0.0:8082 ..."
fuser -k 8082/tcp 2>/dev/null
sleep 0.3
exec python3 claude_web2api.py
