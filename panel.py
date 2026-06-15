#!/usr/bin/env python3
"""
Proxy Control Panel — Web UI for Gemini & Claude local proxies.
Zero external dependencies (stdlib only).
"""

import http.server
import json
import subprocess
import time
import os
import re
import socket
import signal
import sys
import urllib.request
import urllib.error
import threading
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse, parse_qs

# ── Config ──────────────────────────────────────────────────────────────
PANEL_PORT = 8083
GEMINI_PORT = 8081
CLAUDE_PORT = 8082

HOME = os.path.expanduser("~")
GEMINI_DIR = os.path.join(HOME, "gemini-web2api")
CLAUDE_DIR = os.path.join(HOME, "claude-web2api")

GEMINI_LOG = "/tmp/gemini_proxy.log"
CLAUDE_LOG = "/tmp/claude_proxy.log"

GEMINI_COOKIE_SCRIPT = os.path.join(HOME, ".local", "bin", "gemini-cookie-update")
CLAUDE_COOKIE_SCRIPT = os.path.join(HOME, ".local", "bin", "claude-cookie-update")

GEMINI_COOKIE_FILE = os.path.join(GEMINI_DIR, "cookie.txt")
CLAUDE_COOKIE_FILE = os.path.join(CLAUDE_DIR, "cookie_claude.txt")

VPN_HOST = "127.0.0.1"
VPN_PORT = 12334

# ── Helpers ─────────────────────────────────────────────────────────────

def log(msg):
    print(f"[panel] {msg}", flush=True)


def check_port(port):
    """Check if a TCP port is open."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1)
        result = s.connect_ex(("127.0.0.1", port))
        s.close()
        return result == 0
    except Exception:
        return False


def check_vpn():
    """Check if Hiddify proxy port is open."""
    return check_port(VPN_PORT)


def http_get(url, timeout=5):
    """Make HTTP GET, return (status_code, body_or_error)."""
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return resp.status, body
    except urllib.error.HTTPError as e:
        return e.code, str(e)
    except Exception as e:
        return 0, str(e)


def http_post_json(url, data, timeout=15):
    """Make HTTP POST with JSON body, return (status_code, body_or_error)."""
    try:
        body = json.dumps(data).encode("utf-8")
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp_body = resp.read().decode("utf-8", errors="replace")
            return resp.status, resp_body
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")[:500]
        return e.code, err_body
    except Exception as e:
        return 0, str(e)


def get_log_file(name):
    return GEMINI_LOG if name == "gemini" else CLAUDE_LOG


def read_log_file(filepath, lines=50):
    """Read the last N lines from a log file."""
    try:
        with open(filepath, "r", errors="replace") as f:
            all_lines = f.readlines()
        return "".join(all_lines[-lines:])
    except FileNotFoundError:
        return None
    except Exception as e:
        return f"[error reading log: {e}]"


def count_proxy_process(port):
    """Count processes listening on a port (via fuser mimic)."""
    try:
        result = subprocess.run(
            ["ss", "-tlnp"], capture_output=True, text=True, timeout=3
        )
        for line in result.stdout.split("\n"):
            if f":{port}" in line and "LISTEN" in line:
                return 1
        return 0
    except Exception:
        return -1


def restart_proxy(name):
    """Kill and restart a proxy, redirecting output to log file."""
    port = GEMINI_PORT if name == "gemini" else CLAUDE_PORT
    proxy_dir = GEMINI_DIR if name == "gemini" else CLAUDE_DIR
    log_file = get_log_file(name)
    script_name = "gemini_web2api.py" if name == "gemini" else "claude_web2api.py"
    cookie_flag = "--cookie-file"
    cookie_file = GEMINI_COOKIE_FILE if name == "gemini" else CLAUDE_COOKIE_FILE
    config_file = os.path.join(proxy_dir, "config.json")

    try:
        subprocess.run(
            ["fuser", "-k", f"{port}/tcp"],
            capture_output=True, timeout=5
        )
    except Exception:
        pass

    time.sleep(0.5)

    cmd = [
        sys.executable,
        os.path.join(proxy_dir, script_name),
        "--config", config_file,
        cookie_flag, cookie_file,
    ]

    try:
        with open(log_file, "a") as lf:
            lf.write(f"\n--- restart at {datetime.now().isoformat()} ---\n")
            proc = subprocess.Popen(
                cmd, stdout=lf, stderr=lf,
                cwd=proxy_dir,
                stdin=subprocess.DEVNULL
            )
        return {"success": True, "message": f"PID {proc.pid}"}
    except Exception as e:
        return {"success": False, "message": str(e)}


def run_health_test(name):
    """Send a real chat completion request to the proxy."""
    port = GEMINI_PORT if name == "gemini" else CLAUDE_PORT
    url = f"http://127.0.0.1:{port}/v1/chat/completions"
    payload = {
        "model": "gemini-3.5-flash" if name == "gemini" else "claude-sonnet-4",
        "messages": [{"role": "user", "content": "say hi in 3 words or less"}],
        "max_tokens": 10,
    }
    t0 = time.time()
    status, body = http_post_json(url, payload, timeout=20)
    elapsed = round(time.time() - t0, 3)

    if status == 200:
        try:
            data = json.loads(body)
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            return {"success": True, "response": content.strip(), "time": elapsed}
        except Exception:
            return {"success": False, "response": body[:200], "time": elapsed}
    return {"success": False, "response": body[:200], "time": elapsed}


def parse_cookie_expiry(filepath):
    """Extearct cookie expiry dates from Netscape cookie file. Return days until nearest expiry."""
    if not os.path.exists(filepath):
        return None
    try:
        now = time.time()
        min_days = None
        with open(filepath, "r") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or line.startswith("http"):
                    continue
                parts = line.split("\t")
                if len(parts) >= 5:
                    try:
                        expiry = int(parts[4])
                        if expiry > now:
                            days = (expiry - now) / 86400
                            name = parts[0] + "." + parts[5] if len(parts) > 5 else parts[0]
                            if min_days is None or days < min_days:
                                min_days = days
                    except (ValueError, IndexError):
                        pass
        return round(min_days, 1) if min_days is not None else None
    except Exception:
        return None


# ── API & Web Handler ────────────────────────────────────────────────────

class PanelHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path == "/" or path == "":
            self._serve_html()
        elif path == "/api/status":
            self._json(self._get_status())
        elif path.startswith("/api/logs/"):
            name = path.split("/")[-1]
            if name in ("gemini", "claude"):
                qs = parse_qs(parsed.query)
                lines = int(qs.get("lines", [50])[0])
                text = read_log_file(get_log_file(name), lines)
                self._json({"name": name, "log": text, "exists": text is not None})
            else:
                self._json({"error": "unknown proxy"}, 404)
        elif path.startswith("/api/test/"):
            name = path.split("/")[-1]
            if name in ("gemini", "claude"):
                result = run_health_test(name)
                self._json(result)
            else:
                self._json({"error": "unknown proxy"}, 404)
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path.startswith("/api/action/"):
            parts = path.split("/")
            if len(parts) >= 5:
                name = parts[3]
                action = parts[4]
                if name in ("gemini", "claude"):
                    if action == "restart":
                        result = restart_proxy(name)
                        self._json(result)
                        return
                    elif action == "cookies":
                        result = self._handle_cookies(name)
                        self._json(result)
                        return
            self._json({"error": "invalid action"}, 400)
        else:
            self._json({"error": "not found"}, 404)

    def _handle_cookies(self, name):
        script = GEMINI_COOKIE_SCRIPT if name == "gemini" else CLAUDE_COOKIE_SCRIPT
        if os.path.exists(script):
            try:
                subprocess.Popen(
                    ["gnome-terminal", "--hold", "--", "bash", "-c", script],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
                return {"success": True, "message": f"Launched {script}"}
            except Exception as e:
                return {"success": False, "message": str(e)}
        else:
            return {
                "success": False,
                "message": f"Script not found: {script}\n"
                           f"Export cookies manually and save to "
                           f"{GEMINI_COOKIE_FILE if name == 'gemini' else CLAUDE_COOKIE_FILE}"
            }

    def _get_status(self):
        gemini_alive = check_port(GEMINI_PORT)
        claude_alive = check_port(CLAUDE_PORT)
        vpn_alive = check_vpn()

        gemini_rt = None
        claude_rt = None
        if gemini_alive:
            t0 = time.time()
            code, _ = http_get(f"http://127.0.0.1:{GEMINI_PORT}/v1/models")
            gemini_rt = round(time.time() - t0, 3)
            gemini_alive = code == 200

        if claude_alive:
            t0 = time.time()
            code, _ = http_get(f"http://127.0.0.1:{CLAUDE_PORT}/v1/models")
            claude_rt = round(time.time() - t0, 3)
            claude_alive = code == 200

        gemini_cookie_expiry = parse_cookie_expiry(GEMINI_COOKIE_FILE)
        claude_cookie_expiry = parse_cookie_expiry(CLAUDE_COOKIE_FILE)

        return {
            "gemini": {
                "alive": gemini_alive,
                "port": GEMINI_PORT,
                "response_time": gemini_rt,
                "cookie_expiry_days": gemini_cookie_expiry,
                "log_exists": os.path.exists(GEMINI_LOG),
            },
            "claude": {
                "alive": claude_alive,
                "port": CLAUDE_PORT,
                "response_time": claude_rt,
                "cookie_expiry_days": claude_cookie_expiry,
                "log_exists": os.path.exists(CLAUDE_LOG),
            },
            "vpn": {"alive": vpn_alive, "port": VPN_PORT},
        }

    def _serve_html(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(HTML_PAGE.encode("utf-8"))

    def _json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))

    def log_message(self, format, *args):
        log(f"{self.client_address[0]} - {args[0]}")

    do_PUT = do_POST
    do_DELETE = do_POST


# ── HTML Page ────────────────────────────────────────────────────────────

HTML_PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Proxy Control Panel</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0a0a0a;color:#0f0;font-family:'Courier New',monospace;padding:20px;max-width:1200px;margin:0 auto;min-height:100vh}
h1{font-size:1.3em;margin-bottom:4px;color:#0f0;text-transform:uppercase;letter-spacing:3px}
.sub{color:#080;font-size:0.75em;margin-bottom:20px}
.panel-grid{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:20px}

.card{border:1px solid #0f0;padding:16px;background:#050505;position:relative}
.card.gemini{border-color:#0f0}
.card.claude{border-color:#fa0}
.card-header{display:flex;align-items:center;gap:10px;margin-bottom:12px}
.indicator{width:12px;height:12px;border-radius:50%;display:inline-block}
.indicator.alive{background:#0f0;box-shadow:0 0 8px #0f0;animation:pulse 2s infinite}
.indicator.dead{background:#500;box-shadow:0 0 4px #500}
.indicator.unknown{background:#440}
@keyframes pulse{0%{opacity:1}50%{opacity:0.5}100%{opacity:1}}
.card-title{font-size:1em;font-weight:bold;text-transform:uppercase}
.card-title .gemini{color:#0f0}
.card-title .claude{color:#fa0}
.card-body{font-size:0.8em;color:#0a0;line-height:1.6}
.card-body .row{display:flex;justify-content:space-between;padding:2px 0;border-bottom:1px solid #0a0a0a}
.card-body .row:last-child{border:none}
.card-body .label{color:#060}
.card-body .value{color:#0f0}
.card-body .value.warn{color:#fa0}
.card-body .value.critical{color:#f00}
.actions{display:flex;gap:8px;margin-top:12px;flex-wrap:wrap}
.btn{background:transparent;border:1px solid #0f0;color:#0f0;padding:6px 14px;cursor:pointer;font-family:inherit;font-size:0.78em;transition:all 0.2s}
.btn:hover{background:#0f0;color:#000;box-shadow:0 0 6px #0f0}
.btn.danger{border-color:#f00;color:#f00}
.btn.danger:hover{background:#f00;color:#000}
.btn.warning{border-color:#fa0;color:#fa0}
.btn.warning:hover{background:#fa0;color:#000}
.btn:disabled{opacity:0.3;cursor:not-allowed}
.btn.small{padding:3px 8px;font-size:0.7em}

.section-title{color:#0f0;font-size:0.9em;text-transform:uppercase;letter-spacing:2px;margin:20px 0 10px;border-bottom:1px solid #0f0;padding-bottom:4px}

.log-box{background:#000;border:1px solid #0f0;padding:10px;font-size:0.72em;line-height:1.4;height:200px;overflow-y:auto;white-space:pre-wrap;word-break:break-all;color:#0a0;margin-bottom:20px}
.log-box .highlight{color:#0f0}
.log-box .error{color:#f00}
.log-box .warn{color:#fa0}

.test-result{background:#000;border:1px solid #0f0;padding:10px;font-size:0.75em;margin-top:8px;min-height:20px}
.test-result.success{border-color:#0f0}
.test-result.fail{border-color:#f00}

footer{margin-top:30px;padding-top:10px;border-top:1px solid #0a0a0a;font-size:0.7em;color:#060;text-align:center}
footer a{color:#080;text-decoration:none}
footer a:hover{color:#0f0}

.matrix-rain{position:fixed;top:0;left:0;width:100%;height:100%;z-index:-1;opacity:0.06;pointer-events:none}

.loading{opacity:0.5;pointer-events:none}

@media(max-width:700px){.panel-grid{grid-template-columns:1fr}}

.status-bar{display:flex;gap:16px;font-size:0.72em;color:#060;margin-bottom:16px;flex-wrap:wrap}
.status-bar span{display:flex;align-items:center;gap:4px}

.toast{position:fixed;bottom:20px;right:20px;background:#000;border:1px solid #0f0;padding:10px 16px;font-size:0.75em;z-index:999;max-width:400px;opacity:0;transition:opacity 0.3s}
.toast.show{opacity:1}

.confirm-overlay{display:none;position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.85);z-index:1000;justify-content:center;align-items:center}
.confirm-overlay.show{display:flex}
.confirm-dialog{background:#050505;border:2px solid #0f0;padding:24px;max-width:400px;text-align:center}
.confirm-dialog p{margin-bottom:16px;font-size:0.85em;color:#0a0}
.confirm-dialog .btn-group{display:flex;gap:12px;justify-content:center}
</style>
</head>
<body>

<canvas id="matrix" class="matrix-rain"></canvas>

<div id="confirmOverlay" class="confirm-overlay">
  <div class="confirm-dialog">
    <p id="confirmMsg">Are you sure?</p>
    <div class="btn-group">
      <button class="btn" onclick="confirmAction(true)">> CONFIRM</button>
      <button class="btn danger" onclick="confirmAction(false)">> CANCEL</button>
    </div>
  </div>
</div>

<div id="toast" class="toast"></div>

<h1>// PROXY CONTROL PANEL</h1>
<div class="sub">[ system v1.0 ] — gemini-web2api + claude-web2api</div>

<div class="status-bar">
  <span id="vpnStatus"><span class="indicator unknown" style="width:6px;height:6px"></span> VPN checking...</span>
  <span id="timeDisplay">--:--:--</span>
  <span>|</span>
  <span><a href="https://github.com/cyberanrhy/gemini-web2api" target="_blank" style="color:#080">gemini-web2api</a></span>
  <span><a href="https://github.com/cyberanrhy/claude-web2api" target="_blank" style="color:#080">claude-web2api</a></span>
</div>

<div class="panel-grid">
  <div class="card gemini" id="geminiCard">
    <div class="card-header">
      <span class="indicator unknown" id="geminiIndicator"></span>
      <span class="card-title"><span class="gemini">GEMINI</span></span>
    </div>
    <div class="card-body" id="geminiBody">
      <div class="row"><span class="label">PORT</span><span class="value">8081</span></div>
      <div class="row"><span class="label">STATUS</span><span class="value" id="geminiStatus">scanning...</span></div>
      <div class="row"><span class="label">RESPONSE TIME</span><span class="value" id="geminiRT">--</span></div>
      <div class="row"><span class="label">COOKIE EXPIRY</span><span class="value" id="geminiCookies">--</span></div>
    </div>
    <div class="actions">
      <button class="btn btn-sm" onclick="doAction('gemini','restart')">> RESTART</button>
      <button class="btn btn-sm" onclick="doAction('gemini','cookies')">> COOKIES</button>
      <button class="btn btn-sm" onclick="doTest('gemini')">> TEST</button>
    </div>
    <div id="geminiTest" class="test-result">Press TEST to send a request</div>
  </div>

  <div class="card claude" id="claudeCard">
    <div class="card-header">
      <span class="indicator unknown" id="claudeIndicator"></span>
      <span class="card-title"><span class="claude">CLAUDE</span></span>
    </div>
    <div class="card-body" id="claudeBody">
      <div class="row"><span class="label">PORT</span><span class="value">8082</span></div>
      <div class="row"><span class="label">STATUS</span><span class="value" id="claudeStatus">scanning...</span></div>
      <div class="row"><span class="label">RESPONSE TIME</span><span class="value" id="claudeRT">--</span></div>
      <div class="row"><span class="label">COOKIE EXPIRY</span><span class="value" id="claudeCookies">--</span></div>
    </div>
    <div class="actions">
      <button class="btn btn-sm" onclick="doAction('claude','restart')">> RESTART</button>
      <button class="btn btn-sm" onclick="doAction('claude','cookies')">> COOKIES</button>
      <button class="btn btn-sm" onclick="doTest('claude')">> TEST</button>
    </div>
    <div id="claudeTest" class="test-result">Press TEST to send a request</div>
  </div>
</div>

<div class="section-title">// LOGS</div>
<div class="status-bar" style="margin-bottom:4px">
  <button class="btn small" onclick="switchLog('gemini')">> gemini.log</button>
  <button class="btn small" onclick="switchLog('claude')">> claude.log</button>
  <span id="logLabel" style="color:#060">gemini.log</span>
</div>
<div class="log-box" id="logBox">Loading logs...</div>

<div class="section-title">// QUICK INFO</div>
<p style="font-size:0.75em;color:#060;line-height:1.6">
  <strong style="color:#080">RESTART</strong> — kills the proxy process and starts a fresh one.<br>
  <strong style="color:#080">COOKIES</strong> — opens Firefox to export fresh cookies (save to Downloads, then press Enter).<br>
  <strong style="color:#080">TEST</strong> — sends "say hi in 3 words" to the proxy and shows the response.<br>
  <strong style="color:#080">COOKIE EXPIRY</strong> — days until the earliest cookie in the file expires (based on expiry timestamps).
</p>

<footer>
  <a href="https://github.com/cyberanrhy/gemini-web2api">gemini-web2api</a>
  &middot;
  <a href="https://github.com/cyberanrhy/claude-web2api">claude-web2api</a>
  &middot; Proxy Control Panel v1.0
</footer>

<script>
// ── State ──
let currentLog = 'gemini';
let pendingAction = null;

// ── Matrix Rain (optional, lightweight) ──
(function(){
  const c = document.getElementById('matrix');
  if(!c)return;
  const ctx = c.getContext('2d');
  let W, H;
  function resize(){W=c.width=window.innerWidth;H=c.height=window.innerHeight}
  resize(); window.addEventListener('resize', resize);
  const cols = Math.floor(W/20);
  const drops = Array(cols).fill(1);
  function draw(){
    ctx.fillStyle='rgba(10,10,10,0.05)';
    ctx.fillRect(0,0,W,H);
    ctx.fillStyle='#0f0';
    ctx.font='15px monospace';
    for(let i=0;i<drops.length;i++){
      const ch = String.fromCharCode(0x30A0+Math.random()*96);
      ctx.fillText(ch,i*20,drops[i]*20);
      if(drops[i]*20>H && Math.random()>0.975) drops[i]=0;
      drops[i]++;
    }
  }
  setInterval(draw, 60);
})();

// ── Toast ──
function showToast(msg, isError){
  const t=document.getElementById('toast');
  t.textContent='> '+msg;
  t.style.borderColor=isError?'#f00':'#0f0';
  t.style.color=isError?'#f00':'#0f0';
  t.classList.add('show');
  setTimeout(()=>t.classList.remove('show'),4000);
}

// ── Confirm Dialog ──
function askConfirm(msg, cb){
  document.getElementById('confirmMsg').textContent=msg;
  document.getElementById('confirmOverlay').classList.add('show');
  pendingAction=cb;
}
function confirmAction(ok){
  document.getElementById('confirmOverlay').classList.remove('show');
  if(ok && pendingAction) pendingAction();
  pendingAction=null;
}

// ── API Calls ──
async function api(method, path, body){
  try{
    const opts={method};
    if(body) opts.body=JSON.stringify(body);
    const r=await fetch(path, opts);
    return await r.json();
  }catch(e){
    return {error: e.message};
  }
}

// ── Fetch Status ──
async function fetchStatus(){
  const data = await api('GET', '/api/status');
  if(data.error){ showToast('Status error: '+data.error, true); return; }
  
  for(const name of ['gemini','claude']){
    const s = data[name];
    if(!s) continue;
    const ind = document.getElementById(name+'Indicator');
    const st = document.getElementById(name+'Status');
    const rt = document.getElementById(name+'RT');
    const ck = document.getElementById(name+'Cookies');
    
    if(s.alive){
      ind.className='indicator alive';
      st.textContent='ONLINE';
      st.style.color='#0f0';
      rt.textContent=s.response_time?s.response_time+'s':'checking...';
    }else{
      ind.className='indicator dead';
      st.textContent='OFFLINE';
      st.style.color='#f00';
      rt.textContent='--';
    }
    
    if(s.cookie_expiry_days !== null && s.cookie_expiry_days !== undefined){
      const d = s.cookie_expiry_days;
      let color = '#0f0';
      if(d < 7) color = '#fa0';
      if(d < 3) color = '#f00';
      ck.textContent = d + ' days';
      ck.style.color = color;
    } else {
      ck.textContent = 'unknown';
      ck.style.color = '#060';
    }
  }
  
  const vpn = document.getElementById('vpnStatus');
  if(data.vpn){
    const a = data.vpn.alive;
    vpn.innerHTML = '<span class="indicator '+(a?'alive':'dead')+'" style="width:6px;height:6px"></span> VPN '+(a?'ONLINE':'OFFLINE');
  }
  
  document.getElementById('timeDisplay').textContent = new Date().toLocaleTimeString();
}

// ── Fetch Logs ──
async function fetchLogs(){
  const data = await api('GET', `/api/logs/${currentLog}`);
  const box = document.getElementById('logBox');
  if(data.error){
    box.textContent='[error: '+data.error+']';
    return;
  }
  if(data.log === null){
    box.textContent='[no log file — proxy may not have started or logging is disabled]';
    return;
  }
  box.textContent = data.log || '[empty log]';
  box.scrollTop = box.scrollHeight;
}

function switchLog(name){
  currentLog = name;
  document.getElementById('logLabel').textContent = name+'.log';
  fetchLogs();
}

// ── Actions ──
async function doAction(name, action){
  const labels = {
    restart: 'RESTART',
    cookies: 'REFRESH COOKIES'
  };
  askConfirm(
    `${name.toUpperCase()} > ${labels[action]||action}\nThis will temporarily interrupt the proxy.`,
    async ()=>{
      const btn = event&&event.target||document.querySelector(`.card.${name} button`);
      if(btn) btn.disabled=true;
      const result = await api('POST', `/api/action/${name}/${action}`);
      if(btn) btn.disabled=false;
      if(result.success){
        showToast(`${name} ${action}: OK — ${result.message||''}`, false);
      }else{
        showToast(`${name} ${action}: FAIL — ${result.message||result.error||''}`, true);
      }
      setTimeout(fetchStatus, 2000);
    }
  );
}

// ── Test ──
async function doTest(name){
  const div = document.getElementById(name+'Test');
  div.textContent = '> sending request...';
  div.className = 'test-result';
  const result = await api('GET', `/api/test/${name}`);
  if(result.success){
    div.className = 'test-result success';
    div.textContent = `> OK (${result.time}s): "${result.response}"`;
  }else{
    div.className = 'test-result fail';
    div.textContent = `> FAIL (${result.time}s): ${result.response||'no response'}`;
  }
}

// ── Auto Polling ──
fetchStatus();
fetchLogs();
setInterval(fetchStatus, 3000);
setInterval(fetchLogs, 3000);
</script>
</body>
</html>"""

# ── Main ──────────────────────────────────────────────────────────────────

def serve_forever(host="0.0.0.0", port=8083):
    server = http.server.HTTPServer((host, port), PanelHandler)
    log(f"listening on http://{host}:{port}")
    log(f"open http://127.0.0.1:{port} in your browser")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log("shutting down")
        server.shutdown()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Proxy Control Panel")
    parser.add_argument("--port", type=int, default=PANEL_PORT, help="Panel port (default: 8083)")
    args = parser.parse_args()
    serve_forever(port=args.port)
