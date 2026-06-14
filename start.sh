#!/bin/bash
# Claude.ai -> OpenAI-compatible proxy
# Usage: ./start.sh

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

fuser -k 8082/tcp 2>/dev/null
sleep 0.3
exec python3 claude_web2api.py
