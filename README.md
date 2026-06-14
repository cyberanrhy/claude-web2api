# Claude Web2API

OpenAI-compatible proxy for [Claude.ai](https://claude.ai) Web API.

Translate standard `/v1/chat/completions` requests to Claude.ai internal API. Bypasses Cloudflare via TLS fingerprint impersonation.

## Features

- OpenAI-compatible `/v1/chat/completions` endpoint
- Streaming (SSE) and non-streaming modes
- Supports all Claude models (model is selected automatically by Claude)
- Conversation history via prompt formatting (`Human: ... / Assistant: ...`)
- Cloudflare bypass via `curl_cffi` with `impersonate="chrome110"`
- CORS enabled for browser access

## Prerequisites

- Python 3.10+
- `curl_cffi` library
- Active Claude.ai session (cookies)
- Proxy/VPN for Cloudflare bypass (required for most regions)

## Installation

```bash
git clone https://github.com/YOUR_USERNAME/claude-web2api.git
cd claude-web2api
pip install curl_cffi
```

## Quick Start

### 1. Export cookies

1. Install browser extension [cookies.txt](https://addons.mozilla.org/en-US/firefox/addon/cookies-txt/) (Firefox) or equivalent
2. Log in to [Claude.ai](https://claude.ai/chats)
3. Export cookies in **Netscape format**
4. Save as `cookie_claude.txt` in the project directory

### 2. Configure

Copy the example config and edit if needed:

```bash
cp config.json.example config.json
```

Default config works out of the box. If you need a proxy for upstream requests:

```json
{
  "proxy": "http://proxy:port"
}
```

### 3. Run

```bash
bash start.sh
```

Or directly:

```bash
python3 claude_web2api.py
```

Server starts on `http://0.0.0.0:8082`.

## API

### `GET /v1/models`

Returns available Claude models.

### `POST /v1/chat/completions`

OpenAI-compatible chat completions endpoint.

**Request:**

```json
{
  "model": "claude-3-5-sonnet-20241022",
  "messages": [
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": "Hello!"}
  ],
  "stream": false
}
```

**Response (non-streaming):**

```json
{
  "id": "chatcmpl-xxx",
  "object": "chat.completion",
  "choices": [{
    "message": {
      "role": "assistant",
      "content": "Hi! How can I help you?"
    }
  }]
}
```

**Response (streaming):** Server-Sent Events with `[DONE]` termination.

## Usage with OpenCode / AI SDK

Configure provider in your `opencode.json`:

```json
{
  "provider": {
    "claude": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "Claude Local",
      "options": {
        "baseURL": "http://localhost:8082/v1",
        "apiKey": "sk-proxy",
        "timeout": 240000,
        "headerTimeout": 60000,
        "chunkTimeout": 120000
      },
      "models": {
        "claude-3-5-sonnet-20241022": {
          "name": "Claude 3.5 Sonnet"
        }
      }
    }
  }
}
```

## Health Check

```
GET /health
```

Returns server status, organization ID, and cookie state.

## Troubleshooting / FAQ

### Q: 403 Forbidden
Cookies expired. Export fresh cookies from Claude.ai and restart.

### Q: TLS / connection errors
Claude.ai is protected by Cloudflare. You must route requests through a proxy/VPN that can handle Cloudflare challenges. Set `"proxy"` in `config.json`.

### Q: Bad Gateway: upstream error 403
The model name sent to Claude.ai might be unrecognized. The proxy no longer forwards model names to upstream by default.

### Q: Requests hang / timeout
- Check that your proxy is running and accessible
- Increase timeout values in your client configuration
- Export fresh cookies

### Q: How to refresh cookies
1. Open Claude.ai in Firefox
2. Export cookies via cookies.txt extension
3. Replace `cookie_claude.txt`
4. Restart the proxy

### Q: Which model is used?
Claude.ai selects the model automatically based on your account and subscription.

### Q: Can I use this with curl?
```bash
curl -X POST http://localhost:8082/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"Hello"}],"stream":true}'
```

## Files

```
claude-web2api/
├── claude_web2api.py      # Proxy server
├── config.json.example    # Configuration template
├── start.sh               # Start script
├── cookie_claude.txt      # Netscape cookies (gitignored)
├── config.json            # Active config (gitignored)
├── README.md
└── LICENSE
```

## License

MIT
