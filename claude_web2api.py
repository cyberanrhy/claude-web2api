#!/usr/bin/env python3
"""Claude.ai -> OpenAI-compatible proxy (port 8082)"""

import json, os, sys, time, uuid, re
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

from curl_cffi.requests import Session

CONFIG = {}
COOKIE_STRING = ""
ORGANIZATION_ID = None
CLAUDE_BASE = "https://claude.ai"
_SESSION = None

def log(msg):
    print(f"[claude-proxy] {msg}", file=sys.stderr, flush=True)

def load_config():
    global CONFIG
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
    if not os.path.exists(path):
        example = path + ".example"
        if os.path.exists(example):
            with open(example) as src, open(path, "w") as dst:
                dst.write(src.read())
            log(f"Created config.json from config.json.example")
        else:
            print("ERROR: config.json not found.")
            print(f"  Run: cp config.json.example config.json")
            print("  Then run again.")
            sys.exit(1)
    with open(path) as f:
        CONFIG = json.load(f)
    log(f"config loaded: port={CONFIG.get('port', 8082)}")

def load_cookies():
    global COOKIE_STRING
    path = CONFIG.get("cookie_file") or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "cookie_claude.txt")
    if not os.path.exists(path):
        print(f"ERROR: cookie file not found at '{path}'.")
        print("  1. Open https://claude.ai/chats in Firefox and log in")
        print("  2. Install 'cookies.txt' extension (https://addons.mozilla.org/firefox/addon/cookies-txt/)")
        print("  3. Click the extension → Export → save as the file above")
        print("  4. Then run again")
        sys.exit(1)
    cookies = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) >= 7:
                cookies.append(f"{parts[5]}={parts[6]}")
    COOKIE_STRING = "; ".join(cookies)
    log(f"loaded {len(cookies)} cookies from {path}")
    return bool(cookies)

def _session():
    global _SESSION
    if _SESSION is None:
        s = Session(impersonate="chrome110")
        s.headers.update({
            "User-Agent": CONFIG.get("user_agent",
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0"),
            "Accept-Language": "en-US,en;q=0.5",
            "DNT": "1",
        })
        if CONFIG.get("proxy"):
            s.proxies = {"http": CONFIG["proxy"], "https": CONFIG["proxy"]}
        s.trust_env = False
        _SESSION = s
    return _SESSION

def _reset_session():
    global _SESSION
    if _SESSION is not None:
        try:
            _SESSION.close()
        except:
            pass
        _SESSION = None

def claude_req(method, path, **kwargs):
    headers = kwargs.pop("headers", {})
    headers.setdefault("Cookie", COOKIE_STRING)
    s = _session()
    return s.request(method, f"{CLAUDE_BASE}{path}", headers=headers, **kwargs)

def get_organization_id():
    resp = claude_req("GET", "/api/organizations", timeout=30)
    if resp.status_code != 200:
        log(f"failed to get org id: {resp.status_code}")
        return None
    data = resp.json()
    if data and "uuid" in data[0]:
        return data[0]["uuid"]
    return None

def create_chat(org_id):
    resp = claude_req("POST", f"/api/organizations/{org_id}/chat_conversations",
                      json={"name": ""}, timeout=30)
    return resp.json().get("uuid") if resp.status_code in (200, 201) else None

def delete_chat(org_id, chat_id):
    try:
        claude_req("DELETE", f"/api/organizations/{org_id}/chat_conversations/{chat_id}", timeout=10)
    except:
        pass

def format_prompt(messages, tools=None):
    parts = []
    if tools:
        tool_defs = []
        for t in tools:
            fn = t.get("function", t) if t.get("type") == "function" else t
            tool_defs.append({
                "name": fn.get("name", t.get("name", "")),
                "description": fn.get("description", t.get("description", "")),
                "parameters": fn.get("parameters", t.get("parameters", {})),
            })
        if tool_defs:
            parts.append(
                "Human: [Tools] I've configured the following tools for this "
                "conversation. When you need to call one, respond with a JSON "
                "code block using this exact format:\n"
                '```tool_call\n{"name": "function_name", "arguments": {...}}\n```\n'
                "Then wait for the tool result before continuing.\n\n"
                f"Available tool definitions:\n{json.dumps(tool_defs, indent=2)}"
            )
    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")
        if isinstance(content, list):
            content = " ".join(
                c.get("text", "") for c in content
                if c.get("type") in ("text", "input_text")
            )
        if role == "system":
            parts.append(f"System: {content}")
        elif role == "user":
            parts.append(f"Human: {content}")
        elif role == "assistant":
            if msg.get("tool_calls"):
                tc_strs = []
                for tc in msg["tool_calls"]:
                    fn = tc.get("function", {})
                    tc_strs.append(
                        f'```tool_call\n{{"name": "{fn.get("name")}", '
                        f'"arguments": {fn.get("arguments", "{}")}}}\n```'
                    )
                parts.append(f"Assistant: {content or ''}\n" + "\n".join(tc_strs))
            else:
                parts.append(f"Assistant: {content}")
        elif role == "tool":
            parts.append(f"Tool result for {msg.get('name', '')}: {content}")
        else:
            parts.append(content if content else "")
    parts.append("Assistant:")
    return "\n\n".join(parts)

def parse_tool_calls(text):
    """Extract ```tool_call blocks from response. Returns (clean_text, tool_calls_list)."""
    tool_calls = []
    pattern = r'```tool_call\s*\n(.*?)\n```'
    for match in re.findall(pattern, text, re.DOTALL):
        try:
            data = json.loads(match.strip())
            tool_calls.append({
                "id": f"call_{uuid.uuid4().hex[:12]}",
                "type": "function",
                "function": {
                    "name": data["name"],
                    "arguments": json.dumps(data.get("arguments", {}), ensure_ascii=False),
                },
            })
        except (json.JSONDecodeError, KeyError):
            pass
    clean = re.sub(pattern, '', text, flags=re.DOTALL).strip()
    return clean, tool_calls

def get_timezone():
    import subprocess
    try:
        r = subprocess.run(["timedatectl", "show", "--property=Timezone", "--value"],
                           capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            return r.stdout.strip()
    except:
        pass
    return "Europe/Moscow"

def iter_sse_lines(resp):
    """Buffered SSE line iterator for curl_cffi streaming responses"""
    buf = ""
    for chunk in resp.iter_content():
        if chunk:
            buf += chunk.decode(errors="replace")
            while "\n" in buf:
                line, buf = buf.split("\n", 1)
                yield line.strip()
    # Flush remaining
    if buf.strip():
        yield buf.strip()

class ClaudeProxyHandler(BaseHTTPRequestHandler):

    def _sendall(self, data: bytes):
        try:
            self.request.sendall(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json_response(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode()
        reason = {200: "OK", 400: "Bad Request", 401: "Unauthorized",
                  404: "Not Found", 502: "Bad Gateway", 503: "Service Unavailable"}.get(code, "")
        self._sendall(
            f"HTTP/1.1 {code} {reason}\r\n"
            f"Content-Type: application/json; charset=utf-8\r\n"
            f"Content-Length: {len(body)}\r\n"
            f"Connection: close\r\n"
            "Access-Control-Allow-Origin: *\r\n"
            "\r\n".encode() + body
        )

    def _sse_headers(self):
        self._sendall(
            "HTTP/1.1 200 OK\r\n"
            "Content-Type: text/event-stream; charset=utf-8\r\n"
            "Cache-Control: no-cache\r\n"
            "Connection: keep-alive\r\n"
            "Access-Control-Allow-Origin: *\r\n"
            "\r\n".encode()
        )

    def _send_sse(self, data: str):
        self._sendall(f"data: {data}\n\n".encode())

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path == "/v1/models":
            models = [
                {"id": "claude-3-5-sonnet-20241022", "object": "model", "created": 1728000000, "owned_by": "anthropic"},
                {"id": "claude-3-opus-20240229", "object": "model", "created": 1709164800, "owned_by": "anthropic"},
                {"id": "claude-3-sonnet-20240229", "object": "model", "created": 1709164800, "owned_by": "anthropic"},
                {"id": "claude-3-haiku-20240307", "object": "model", "created": 1709769600, "owned_by": "anthropic"},
                {"id": "claude-2.1", "object": "model", "created": 1701302400, "owned_by": "anthropic"},
            ]
            self._json_response(200, {"object": "list", "data": models})
        elif path == "/health" or path == "/":
            self._json_response(200, {"status": "ok", "org_id": ORGANIZATION_ID,
                                      "cookies": bool(COOKIE_STRING)})
        else:
            self._json_response(404, {"error": "not found"})

    def do_POST(self):
        global ORGANIZATION_ID

        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path != "/v1/chat/completions":
            self._json_response(404, {"error": "not found"})
            return

        if not COOKIE_STRING:
            self._json_response(401, {"error": "no cookies loaded"})
            return

        if not ORGANIZATION_ID:
            self._json_response(503, {"error": "no organization id"})
            return

        try:
            content_len = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_len)
            req = json.loads(body)
        except:
            self._json_response(400, {"error": "invalid json"})
            return

        messages = req.get("messages", [])
        if not messages:
            self._json_response(400, {"error": "no messages"})
            return

        stream = req.get("stream", False)
        model = req.get("model") or CONFIG.get("model") or "claude"
        tools = req.get("tools")
        prompt = format_prompt(messages, tools)
        log(f"chat request: model={model}, stream={stream}, messages={len(messages)}, tools={bool(tools)}")

        # Send streaming headers immediately so client doesn't wait
        if stream:
            self._sse_headers()

        chat_id = create_chat(ORGANIZATION_ID)
        if not chat_id:
            if not stream:
                self._json_response(502, {"error": "failed to create chat"})
            else:
                # Already sent SSE headers, send error inside stream
                err_chunk = {"choices": [{"delta": {}, "finish_reason": "stop", "index": 0}]}
                self._send_sse(json.dumps(err_chunk))
                self._send_sse("[DONE]")
            return

        try:
            headers = {
                "Accept": "text/event-stream, text/event-stream",
                "Content-Type": "application/json",
                "Origin": "https://claude.ai",
                "Referer": f"https://claude.ai/chat/{chat_id}",
                "TE": "trailers",
            }
            payload = {
                "attachments": [],
                "files": [],
                "prompt": prompt,
                "timezone": get_timezone(),
            }

            upstream = claude_req("POST",
                f"/api/organizations/{ORGANIZATION_ID}/chat_conversations/{chat_id}/completion",
                json=payload, headers=headers, stream=True, timeout=240)

            if upstream.status_code != 200:
                err_text = upstream.text[:500]
                log(f"upstream error: {upstream.status_code} {err_text}")
                if stream:
                    self._send_sse(json.dumps({"choices": [{"delta": {}, "finish_reason": "stop", "index": 0}]}))
                    self._send_sse("[DONE]")
                else:
                    self._json_response(502, {"error": f"upstream error {upstream.status_code}", "detail": err_text})
                return

            if stream:
                self._stream_response(upstream, model, tools)
            else:
                self._blocking_response(upstream, model, tools)

        except Exception as e:
            log(f"request error: {e}")
            try:
                if not stream:
                    self._json_response(502, {"error": str(e)})
            except:
                pass
        finally:
            try:
                delete_chat(ORGANIZATION_ID, chat_id)
            except:
                pass

    def _stream_response(self, upstream, model, tools=None):
        completion_id = f"chatcmpl-{uuid.uuid4().hex}"
        created = int(time.time())

        if tools:
            # Buffer full response when tools present (need complete text to parse tool calls)
            full_text = ""
            for line in iter_sse_lines(upstream):
                if not line or not line.startswith("data: "):
                    continue
                try:
                    data = json.loads(line[6:])
                except:
                    continue
                if data.get("type") == "completion":
                    full_text += data.get("completion", "")
                elif data.get("type") == "error":
                    log(f"upstream SSE error: {data}")
                    break

            clean_text, tool_calls = parse_tool_calls(full_text)
            msg = {"role": "assistant", "content": clean_text or None}
            if tool_calls:
                msg["tool_calls"] = tool_calls
            finish = "tool_calls" if tool_calls else "stop"
            chunk = {
                "id": completion_id, "object": "chat.completion.chunk",
                "created": created, "model": model,
                "choices": [{"index": 0, "delta": msg, "finish_reason": finish}]
            }
            self._send_sse(json.dumps(chunk, ensure_ascii=False))
        else:
            # True streaming: forward chunks as they arrive
            for line in iter_sse_lines(upstream):
                if not line or not line.startswith("data: "):
                    continue
                try:
                    data = json.loads(line[6:])
                except:
                    continue
                if data.get("type") == "completion":
                    text = data.get("completion", "")
                    if text:
                        chunk = {
                            "id": completion_id, "object": "chat.completion.chunk",
                            "created": created, "model": model,
                            "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}]
                        }
                        self._send_sse(json.dumps(chunk, ensure_ascii=False))
                elif data.get("type") == "error":
                    log(f"upstream SSE error: {data}")
                    break

            final = {
                "id": completion_id, "object": "chat.completion.chunk",
                "created": created, "model": model,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]
            }
            self._send_sse(json.dumps(final, ensure_ascii=False))

        self._send_sse("[DONE]")

    def _blocking_response(self, upstream, model, tools=None):
        completion_id = f"chatcmpl-{uuid.uuid4().hex}"
        created = int(time.time())
        content_parts = []

        for line in iter_sse_lines(upstream):
            if not line or not line.startswith("data: "):
                continue
            try:
                data = json.loads(line[6:])
            except:
                continue
            if data.get("type") == "completion":
                content_parts.append(data.get("completion", ""))
            elif data.get("type") == "error":
                log(f"upstream error: {data}")
                self._json_response(502, {"error": data.get("message", str(data))})
                return

        full_text = "".join(content_parts)
        if tools:
            clean_text, tool_calls = parse_tool_calls(full_text)
        else:
            clean_text = full_text.strip()
            tool_calls = None

        msg = {"role": "assistant", "content": clean_text or None}
        if tool_calls:
            msg["tool_calls"] = tool_calls
        finish = "tool_calls" if tool_calls else "stop"

        self._json_response(200, {
            "id": completion_id, "object": "chat.completion",
            "created": created, "model": model,
            "choices": [{"index": 0, "message": msg, "finish_reason": finish}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        })


def main():
    load_config()
    load_cookies()

    global ORGANIZATION_ID
    if COOKIE_STRING:
        ORGANIZATION_ID = get_organization_id()
        log(f"organization_id: {ORGANIZATION_ID}")
    else:
        log("no cookies loaded, server will return 401")

    port = CONFIG.get("port", 8082)
    server = HTTPServer(("0.0.0.0", port), ClaudeProxyHandler)
    log(f"listening on http://0.0.0.0:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log("shutting down")
        server.server_close()


if __name__ == "__main__":
    main()
