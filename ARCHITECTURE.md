# Claude Web2API — Architecture & Flow

## System Architecture

```mermaid
graph TB
    Client["👤 Client Application<br/>(OpenAI SDK/curl)"] 
    Proxy["🔄 Claude Web2API Proxy<br/>(Python HTTP Server)"]
    Claude["🤖 Claude.ai Web API<br/>(Internal Endpoints)"]
    Cloudflare["🛡️ Cloudflare Protection"]
    
    Client -->|POST /v1/chat/completions| Proxy
    Proxy -->|TLS + Chrome110<br/>Impersonation| Cloudflare
    Cloudflare -->|Cookie Auth| Claude
    Claude -->|SSE Stream| Proxy
    Proxy -->|OpenAI Format| Client
    
    style Client fill:#e1f5ff
    style Proxy fill:#fff3e0
    style Claude fill:#f3e5f5
    style Cloudflare fill:#ffebee
```

## Request/Response Flow (Streaming)

```mermaid
sequenceDiagram
    actor User
    participant Client as OpenAI Client<br/>(Your App)
    participant Proxy as Claude Web2API
    participant CookieAuth as Cookie Manager
    participant ClaudeAPI as Claude.ai API
    
    User->>Client: send message
    Client->>Proxy: POST /v1/chat/completions<br/>{messages, model, stream}
    
    Proxy->>CookieAuth: validate cookies loaded?
    CookieAuth-->>Proxy: ✓ cookies ready
    
    Proxy->>Proxy: format_prompt(messages)<br/>→ Claude text format
    
    Proxy->>ClaudeAPI: POST /api/organizations/{id}/chat/completions<br/>rate_limit() + retry logic
    
    ClaudeAPI-->>Proxy: SSE stream (data: ...)
    
    loop Parse SSE chunks
        Proxy->>Proxy: iter_sse_lines()<br/>parse_tool_calls()<br/>detect Claude invocations
        Proxy-->>Client: data: {...chunk...}
    end
    
    Proxy->>Proxy: [DONE]
    Client-->>User: display response
    
    Proxy->>ClaudeAPI: DELETE /api/organizations/{id}/chat/{chat_id}<br/>cleanup
```

## Tool Call Detection Flow

```mermaid
graph LR
    Response["Claude Response<br/>(SSE Stream)"] 
    Buffer["Accumulate in Buffer<br/>(buf += text)"]
    Parse["parse_tool_calls()<br/>Regex Match"]
    
    Pattern1["Pattern 1:<br/>&lt;invoke tool=...&gt;<br/>...&lt;/invoke&gt;"]
    Pattern2["Pattern 2:<br/>&lt;atml:invoke...&gt;<br/>&lt;atml:parameter&gt;"]
    
    Match{"Tool calls<br/>found?"}
    ToolFormat["Format as OpenAI<br/>tool_calls[]"]
    TextFormat["Format as<br/>content text"]
    
    Response --> Buffer
    Buffer --> Parse
    Parse --> Pattern1
    Parse --> Pattern2
    Pattern1 --> Match
    Pattern2 --> Match
    Match -->|Yes| ToolFormat
    Match -->|No| TextFormat
    ToolFormat --> Client["Send to Client<br/>(finish_reason: tool_calls)"]
    TextFormat --> Client
    
    style Response fill:#e8f5e9
    style Match fill:#fff9c4
    style ToolFormat fill:#c8e6c9
    style TextFormat fill:#bbdefb
```

## Component Breakdown

| Component | Purpose | Key Features |
|-----------|---------|---------------|
| **HTTPServer** | Listen for OpenAI-format requests | ThreadingMixIn for concurrent requests |
| **Cookie Manager** | Load & validate Netscape format cookies | Parses `cookie_claude.txt` on startup |
| **Rate Limiter** | Enforce 1.5s minimum between requests | Prevents 429 Too Many Requests |
| **Retry Logic** | Exponential backoff for TLS/SSL errors | Up to 5 retries with 2^n delay |
| **SSE Parser** | Stream parsing from Claude API | Handles `completion`, `content_block_delta`, `message_stop` |
| **Tool Detector** | Regex-based tool call extraction | Supports both `<invoke>` and `<atml:invoke>` formats |
| **Response Formatter** | Convert Claude → OpenAI format | Handles streaming & blocking modes |

## Security & Authentication

```
┌─────────────────────────────────────┐
│  No API Key Required                │
│  ✓ Cookie-based auth (Claude.ai)   │
│  ✓ Chrome110 User-Agent spoofing    │
│  ✓ curl_cffi TLS fingerprinting     │
│  ✓ Cloudflare bypass support        │
└─────────────────────────────────────┘
```

## Error Handling Strategy

```mermaid
graph TD
    Req["Incoming Request"]
    ValidCookies{"Cookies<br/>loaded?"}
    ValidOrg{"Org ID<br/>available?"}
    CreateChat{"Chat<br/>created?"}
    UpstreamOK{"Upstream<br/>200?"}
    ParseOK{"Parse<br/>success?"}
    
    Req --> ValidCookies
    ValidCookies -->|No| Err401["401: No Cookies"]
    ValidCookies -->|Yes| ValidOrg
    ValidOrg -->|No| FetchOrg["Fetch org_id<br/>(on-demand)"]
    FetchOrg --> CreateChat
    ValidOrg -->|Yes| CreateChat
    CreateChat -->|No| Err502a["502: Chat Creation Failed"]
    CreateChat -->|Yes| UpstreamOK
    UpstreamOK -->|No| Err502b["502: Upstream Error"]
    UpstreamOK -->|Yes| ParseOK
    ParseOK -->|Error| Err502c["502: Parse Error"]
    ParseOK -->|Success| Success["200: Tool or Content"]
    
    style Err401 fill:#ffcdd2
    style Err502a fill:#ffcdd2
    style Err502b fill:#ffcdd2
    style Err502c fill:#ffcdd2
    style Success fill:#c8e6c9
```

## Configuration & Startup

```mermaid
graph LR
    Start["python3 claude_web2api.py"]
    LoadCfg["load_config()<br/>config.json"]
    LoadCookie["load_cookies()<br/>cookie_claude.txt"]
    FetchOrg["get_organization_id()<br/>(if cookies OK)"]
    Server["HTTPServer(0.0.0.0:8082)<br/>listening..."]
    Ready["✓ Ready for requests"]
    
    Start --> LoadCfg
    LoadCfg --> LoadCookie
    LoadCookie -->|Success| FetchOrg
    LoadCookie -->|Fail| Server
    FetchOrg --> Server
    Server --> Ready
    
    style Ready fill:#a5d6a7
```

## Model Support

```
Supported Claude Models:
├── claude-3-5-sonnet-20241022 (Latest, recommended)
├── claude-3-5-haiku-20241022
├── claude-3-opus-20240229
├── claude-3-sonnet-20240229
├── claude-3-haiku-20240307
├── claude-2.1
└── claude-haiku-4-5-20251001
```

## Performance Notes

- **Concurrency**: ThreadingMixIn allows unlimited concurrent requests
- **Streaming**: Full SSE support, sent to client in real-time
- **Rate Limiting**: 1.5s enforced between requests (Claude.ai requirement)
- **Retry**: Exponential backoff handles Cloudflare TLS issues
- **Chat Cleanup**: Automatic deletion after response (no chat history leak)

---

*Generated for claude-web2api project — Architecture Documentation*
