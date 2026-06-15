#!/bin/bash
fuser -k 8082/tcp 2>/dev/null
sleep 0.3
exec python3 /home/skitchen/claude-web2api/claude_web2api.py
