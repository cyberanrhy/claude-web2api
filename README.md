# Claude Web2API

OpenAI-compatible proxy for [Claude.ai](https://claude.ai) Web API.

Converts standard `/v1/chat/completions` requests to Claude.ai internal API. Works via cookie-based auth — no API key needed.

## Features

- OpenAI-compatible `/v1/chat/completions`
- Streaming (SSE) and non-streaming
- Model: `claude-3-5-sonnet`, `claude-3-5-haiku`, etc. (matches Claude.ai models)
- Automatic TLS retry with exponential backoff
- Rate-limit handling (429, daily reset)
- CORS enabled

## Requirements

- **Python 3.10+**
- `pip install -r requirements.txt` (uses `curl_cffi` with Chrome impersonation)
- **Firefox** with [cookies.txt](https://addons.mozilla.org/en-US/firefox/addon/cookies-txt/) extension
- **VPN/proxy** — Claude.ai blocks non-residential IPs (Cloudflare)

## Setup (step by step)

### 1. Clone & install

```bash
git clone https://github.com/YOUR_USERNAME/claude-web2api.git
cd claude-web2api
pip install -r requirements.txt
```

### 2. Export cookies

1. Open [Claude.ai](https://claude.ai) **in Firefox** and log in
2. Install [cookies.txt](https://addons.mozilla.org/en-US/firefox/addon/cookies-txt/) extension
3. Click the extension icon → **Export** → save as `cookie_claude.txt`
4. Put `cookie_claude.txt` in the project directory

> Must be **Netscape format** (tabs). The cookies.txt extension exports this by default.

### 3. Run

```bash
python3 claude_web2api.py
```

Expected output:
```
* Proxy server running on http://0.0.0.0:8082
```

### 4. Verify

```bash
# Check server is alive
curl -s http://localhost:8082/v1/models | head -c 200

# Send a message
curl -s -X POST http://localhost:8082/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"claude-sonnet-4","messages":[{"role":"user","content":"Say hello in 3 words"}]}'
```

## Configuration

| Field | Default | Description |
|-------|---------|-------------|
| `port` | `8082` | Server port |
| `host` | `"0.0.0.0"` | Bind address |
| `proxy` | `"http://127.0.0.1:12334"` | HTTP proxy (Claude.ai blocks direct connections) |
| `log_requests` | `false` | Log request/response bodies |

## How it works

1. You send a standard OpenAI chat request to `/v1/chat/completions`
2. The proxy creates a new chat session on Claude.ai
3. Sends your message, streams the response, deletes the chat
4. Uses `curl_cffi` with `impersonate="chrome110"` to bypass Cloudflare

## Cross-platform compatibility

Fully compatible with **Linux, macOS, and Windows**. The proxy core and control features have been ported to work across platforms using standard library modules and cross-platform compatible libraries.

## FAQ

### Q: 401 / Connection refused
Claude.ai blocked the request. Make sure your VPN/proxy is active and cookies are fresh.

### Q: 429 Too Many Requests
Daily message limit reached on Claude.ai free tier. Resets at 9:00 AM (local time).

### Q: SSL / TLS errors
`curl_cffi` doesn't work with system libcurl compiled with GnuTLS (common on Debian/Ubuntu). Install with `pip install --break-system-packages` or compile a custom libcurl with OpenSSL.

## Files

```
claude-web2api/
├── claude_web2api.py      # Proxy server
├── panel.py               # Control panel
├── start_claude.sh        # Start script
├── cookie_claude.txt      # Cookies (gitignored)
├── config.json            # Active config (gitignored)
├── README.md
└── LICENSE
```

## License

MIT
