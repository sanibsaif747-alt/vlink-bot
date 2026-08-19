#!/usr/bin/env python3
import html
import http.cookiejar
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin, urlparse

BOT_TOKEN = os.environ.get("VPLINK_BOT_TOKEN", "")
DEBUG = os.environ.get("VPLINK_DEBUG", "") == "1"
LOG_FILE = "/root/vlink-bot.log"


def log(msg):
    line = time.strftime("%Y-%m-%d %H:%M:%S") + " " + msg
    try:
        if os.path.getsize(LOG_FILE) > 2 * 1024 * 1024:
            os.replace(LOG_FILE, LOG_FILE + ".1")
    except OSError:
        pass
    with open(LOG_FILE, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")
ALLOWED_CHAT_IDS = {
    int(i) for i in os.environ.get("VPLINK_ALLOWED_CHAT_IDS", "").split(",") if i.strip()
}
MAX_HOPS = 15
HTTP_TIMEOUT = 20
RETRY_COUNT = 2
MAX_WORKERS = 8

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
UA_ROTATION = [
    UA,
    "curl/8.5.0",
    "Wget/1.21.4",
    "Python-urllib/3.11",
]

REDIRECT_RE = re.compile(
    r"(?:window\.)?(?:location|document\.location)\.(?:href|replace)\s*=\s*[\"']([^\"']+)[\"']"
)
WINDOW_OPEN_RE = re.compile(r'window\.open\s*\(\s*["\']([^"\']+)["\']')
VAR_LINK_RE = re.compile(
    r"var\s+(?:link|url|finalUrl|destination|target|go|down)\s*[:=]\s*[\"']([^\"']+)[\"']",
    re.IGNORECASE,
)
JSON_URL_RE = re.compile(r'["\']url["\']\s*:\s*["\']([^"\']+)["\']', re.IGNORECASE)
DATA_ATTR_RE = re.compile(r'data-(?:url|href|lnk|link|src)="([^"]+)"', re.IGNORECASE)
SET_ATTR_RE = re.compile(
    r'setAttribute\(\s*["\']href["\']\s*,\s*["\']([^"\']+)["\']'
)
META_REFRESH_RE = re.compile(
    r'<meta[^>]+http-equiv=["\']refresh["\'][^>]*content\s*=\s*["\']\s*\d*\s*;\s*url\s*=\s*([^"\' >]+)',
    re.IGNORECASE,
)
ANCHOR_RE = re.compile(r'<a[^>]+href\s*=\s*["\'](https?://[^"\']+)["\']', re.IGNORECASE)
FILE_HOST_RE = re.compile(
    r"https?://(?:www\.)?(?:mediafire\.com/(?:file|view|download)/[A-Za-z0-9]+"
    r"|devupload\.[a-z]+/[A-Za-z0-9]+"
    r"|drive\.google\.com/file/d/[A-Za-z0-9_-]+"
    r"|mega\.nz/(?:file|folder)/[A-Za-z0-9!_-]+)",
    re.IGNORECASE,
)


_jar = http.cookiejar.CookieJar()
_opener = urllib.request.build_opener(
    urllib.request.HTTPCookieProcessor(_jar),
    urllib.request.HTTPRedirectHandler(),
)
_jar_lock = threading.Lock()
SESSION_FILE = "/root/.vlink-session.json"


def get_opener():
    return _opener


def save_session():
    try:
        cookies = [
            {"domain": c.domain, "path": c.path, "name": c.name, "value": c.value,
             "expires": c.expires, "secure": c.secure}
            for c in _jar
        ]
        cache = {k: list(v) for k, v in _earn_session_cache.items()}
        with open(SESSION_FILE, "w") as fh:
            json.dump({"cookies": cookies, "cache": cache}, fh)
    except OSError:
        pass


def load_session():
    return


def fetch(url, ua=None, referer=None):
    opener = get_opener()
    for try_ua in ([ua] if ua else UA_ROTATION):
        headers = {"User-Agent": try_ua}
        if referer:
            headers["Referer"] = referer
        req = urllib.request.Request(url, headers=headers)
        last_err = None
        for attempt in range(RETRY_COUNT + 1):
            try:
                resp = opener.open(req, timeout=HTTP_TIMEOUT)
                if DEBUG:
                    print("[d] ua={!r} -> {}".format(try_ua[:30], resp.getcode()), flush=True)
                return resp
            except urllib.error.HTTPError as e:
                if e.code == 403:
                    body = e.read(8192).decode("utf-8", errors="replace").lower()
                    if "just a moment" in body or "cf-challenge" in body:
                        break
                raise
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                last_err = e
                time.sleep(2 * (attempt + 1))
    if last_err:
        raise last_err
    raise urllib.error.HTTPError(url, 403, "all UA rotations challenged", None, None)


def hidden_val(body, name):
    m = re.search(
        r'name="' + re.escape(name) + r'"[^>]*value="([^"]*)"', body
    )
    if not m:
        m = re.search(
            r'name="' + re.escape(name) + r'"[^>]*value=&quot;([^&]*)&quot;', body
        )
    return html.unescape(m.group(1)) if m else None


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_partner_cache = {}


def discover_partner(url):
    host = urlparse(url).netloc
    if host in _partner_cache:
        return _partner_cache[host]
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA_ROTATION[1]})
        get_opener()
        opener = urllib.request.build_opener(
            NoRedirect(),
            urllib.request.HTTPCookieProcessor(_jar),
        )
        opener.open(req, timeout=HTTP_TIMEOUT)
    except urllib.error.HTTPError as e:
        loc = e.headers.get("Location", "")
        m = re.match(r"https?://([^/]+)", loc)
        if m:
            partner = "https://" + m.group(1) + "/"
            _partner_cache[host] = partner
            return partner
    except (urllib.error.URLError, TimeoutError, OSError):
        pass
    return None


_earn_session_cache = {}


def solve_earnlinks(url, body, mobile_ua, depth=0):
    code = urlparse(url).path.strip("/").split("/")[-1]
    cached = _earn_session_cache.get(code)
    if cached and time.time() - cached[0] < 1800:
        csrf, adf, fields, unlocked = cached[1:]
        log("earnlinks replay session for {}".format(code))
        target = earnlinks_post(url, csrf, adf, fields, unlocked, depth)
        if target:
            _earn_session_cache[code] = (time.time(), csrf, adf, fields, unlocked or "")
            return target
        _earn_session_cache.pop(code, None)
        log("earnlinks replay failed for {} — fresh session".format(code))
    if 'name="ad_form_data"' not in body and "links/go" not in body:
        return None
    csrf = hidden_val(body, "_csrfToken")
    adf = hidden_val(body, "ad_form_data")
    fields = hidden_val(body, "_Token[fields]")
    unlocked = hidden_val(body, "_Token[unlocked]")
    if not (csrf and adf and fields):
        log("earnlinks solve: missing hidden fields")
        return None
    target = earnlinks_post(url, csrf, adf, fields, unlocked, depth)
    if target:
        _earn_session_cache[code] = (time.time(), csrf, adf, fields, unlocked or "")
    return target


def earnlinks_post(url, csrf, adf, fields, unlocked, depth=0):
    curl_bin = None
    for cand in ("/usr/bin/curl", "/usr/local/bin/curl", "/bin/curl"):
        if os.path.exists(cand):
            curl_bin = cand
            break
    if curl_bin:
        payload = (
            "_method=POST&_csrfToken=" + urllib.parse.quote(csrf, safe="")
            + "&ad_form_data=" + urllib.parse.quote(adf, safe="")
            + "&_Token%5Bfields%5D=" + urllib.parse.quote(fields, safe="")
            + "&_Token%5Bunlocked%5D=" + urllib.parse.quote(unlocked or "", safe="")
        )
        cj = "/tmp/vlink_cookies_{}.txt".format(os.getpid())
        try:
            with open(cj, "w") as fh:
                fh.write("# Netscape HTTP Cookie File\n")
                for c in _jar:
                    fh.write("\t".join([
                        c.domain, "TRUE" if c.domain.startswith(".") else "FALSE",
                        c.path, "TRUE" if c.secure else "FALSE",
                        str(int(c.expires)) if c.expires else "0", c.name, c.value,
                    ]) + "\n")
            proc = subprocess.run(
                [curl_bin, "-s", "-b", cj, "-c", cj,
                 "--max-time", "15", "--connect-timeout", "8",
                 "-A", UA_ROTATION[0],
                 "-H", "Referer: " + url,
                 "-H", "Origin: " + urljoin(url, "/")[:-1],
                 "-H", "Content-Type: application/x-www-form-urlencoded",
                 "-H", "X-Requested-With: XMLHttpRequest",
                 "--data", payload,
                 urljoin(url, "/links/go")],
                capture_output=True,
                text=True,
                timeout=HTTP_TIMEOUT + 5,
                start_new_session=True,
            )
            data = json.loads(proc.stdout)
            try:
                loaded = http.cookiejar.MozillaCookieJar(cj)
                loaded.load(cj, ignore_discard=True, ignore_expires=True)
                for c in loaded:
                    _jar.set_cookie(c)
            except (OSError, http.cookiejar.LoadError):
                pass
        except (OSError, ValueError, subprocess.TimeoutExpired) as e:
            log("earnlinks solve error: {}".format(type(e).__name__))
            return None
        finally:
            try:
                os.remove(cj)
            except OSError:
                pass
        target = data.get("url") if isinstance(data, dict) else None
        if not target:
            return None
        host = urlparse(target).netloc
        if "earnlinks" in host or "linksgo" in host or "vplink" in host:
            if depth >= 5:
                log("earnlinks recursion depth cap hit at {}".format(target))
                return target
            log("earnlinks chain hop: {}".format(target))
            return try_shortener_solve(target, depth + 1) or target
        if FILE_HOST_RE.search(target):
            log("earnlinks solved: {}".format(target))
        return target
    payload = (
        "_method=POST&_csrfToken=" + urllib.parse.quote(csrf, safe="")
        + "&ad_form_data=" + urllib.parse.quote(adf, safe="")
        + "&_Token%5Bfields%5D=" + urllib.parse.quote(fields, safe="")
        + "&_Token%5Bunlocked%5D=" + urllib.parse.quote(unlocked or "", safe="")
    )
    req = urllib.request.Request(
        urljoin(url, "/links/go"),
        data=payload.encode(),
        headers={
            "User-Agent": UA_ROTATION[0],
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": urljoin(url, "/")[:-1],
            "Referer": url,
            "X-Requested-With": "XMLHttpRequest",
        },
    )
    try:
        resp = get_opener().open(req, timeout=HTTP_TIMEOUT)
        data = json.loads(resp.read().decode())
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        log("earnlinks solve error: {}".format(type(e).__name__))
        return None
    target = data.get("url") if isinstance(data, dict) else None
    if not target:
        return None
    host = urlparse(target).netloc
    if "earnlinks" in host or "linksgo" in host or "vplink" in host:
        if depth >= 5:
            log("earnlinks recursion depth cap hit at {}".format(target))
            return target
        log("earnlinks chain hop: {}".format(target))
        return try_shortener_solve(target, depth + 1) or target
    if FILE_HOST_RE.search(target):
        log("earnlinks solved: {}".format(target))
    return target


def extract_target(url, body):
    candidates = []
    for rx in (REDIRECT_RE, WINDOW_OPEN_RE, VAR_LINK_RE, JSON_URL_RE, DATA_ATTR_RE, SET_ATTR_RE):
        m = rx.search(body)
        if m:
            candidates.append(html.unescape(m.group(1)))
    m = META_REFRESH_RE.search(body)
    if m:
        candidates.append(html.unescape(m.group(1)))
    anchors = [
        html.unescape(h)
        for h in ANCHOR_RE.findall(body)
        if not h.startswith("https://vplink.in/cdn-cgi/")
    ]
    candidates.extend(anchors)
    for c in candidates:
        c = c.replace("\\u0026", "&").replace("\\u003d", "=")
        if c.startswith("http"):
            return c
        if c.startswith("//"):
            return "https:" + c
        if c.startswith("/"):
            return urljoin(url, c)
    return None


def normalize_url(u):
    u = (u or "").strip()
    u = u.split("#", 1)[0]
    if u.endswith("/") and not u.endswith("//"):
        u = u[:-1]
    return u


def try_shortener_solve(url, depth=0):
    host = urlparse(url).netloc
    partner = _partner_cache.get(host) or discover_partner(url)
    if not partner:
        return None
    try:
        resp = fetch(url, ua=UA_ROTATION[1], referer=partner)
        if resp.getcode() != 200:
            return None
        body = resp.read(65536).decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError, urllib.error.HTTPError):
        return None
    solved = solve_earnlinks(url, body, UA_ROTATION[1], depth)
    if solved:
        return solved
    with _jar_lock:
        for c in list(_jar):
            if c.domain == host or c.domain.endswith(host):
                try:
                    _jar.clear(c.domain, c.path, c.name)
                except Exception:
                    pass
    try:
        resp = fetch(url, ua=UA_ROTATION[1], referer=partner)
        body = resp.read(65536).decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError, OSError, urllib.error.HTTPError):
        return None
    return solve_earnlinks(url, body, UA_ROTATION[1], depth)


def resolve(entry_url):
    chain = []
    seen = set()
    current = entry_url
    reason = "redirect"
    mediafire_links = set()
    for hop in range(MAX_HOPS + 1):
        key = normalize_url(current)
        if key in seen:
            return chain, "loop detected at hop {}".format(hop), mediafire_links
        seen.add(key)
        if hop == 0 or "earnlinks" in urlparse(current).netloc:
            solved = try_shortener_solve(current)
            if solved:
                mediafire_links.add(solved)
                chain.append((current, "mediafire"))
                return chain, "mediafire", mediafire_links
            if "earnlinks" in urlparse(current).netloc:
                return chain, "earnlinks gate failed", mediafire_links
        try:
            resp = fetch(current)
        except urllib.error.HTTPError as e:
            if e.code in (301, 302, 303, 307, 308) and e.headers.get("Location"):
                target = urljoin(current, e.headers["Location"])
                chain.append((current, "HTTP {}".format(e.code)))
                current = target
                reason = "redirect"
                continue
            return chain, "HTTP {}".format(e.code)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            return chain, "error: {}".format(type(e).__name__)
        if resp.url != current:
            if normalize_url(resp.url) != normalize_url(current):
                chain.append((current, "redirect"))
                current = resp.url
                reason = "redirect"
                continue
        body = resp.read(65536).decode("utf-8", errors="replace")
        ct = resp.headers.get("Content-Type", "")
        for mf in FILE_HOST_RE.findall(body):
            mediafire_links.add(mf)
        if DEBUG:
            print("[d] hop {} ct={} bytes={} url={}".format(hop, ct, len(body), current), flush=True)
        if mediafire_links:
            chain.append((current, "mediafire"))
            return chain, "mediafire", mediafire_links
        solved = solve_earnlinks(current, body, UA_ROTATION[0])
        if solved:
            mediafire_links.add(solved)
            chain.append((current, "mediafire"))
            return chain, "mediafire", mediafire_links
        if "html" not in ct:
            chain.append((current, "final"))
            return chain, "ok", mediafire_links
        target = extract_target(current, body)
        if DEBUG:
            print("[d] extracted target: {}".format(target), flush=True)
        if target and normalize_url(target) != normalize_url(current):
            chain.append((current, "page"))
            current = target
            reason = "page"
            continue
        chain.append((current, "final"))
        return chain, "ok", mediafire_links
    return chain, "hop limit reached", mediafire_links


def resolve_safe(entry_url):
    try:
        chain, status, mf = resolve(entry_url)
        return chain, status, mf
    except Exception as e:
        return [(entry_url, "fatal")], "unhandled: {}".format(type(e).__name__), set()


URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)


def telegram_post(method, payload, timeout=60):
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        "https://api.telegram.org/bot{}/{}".format(BOT_TOKEN, method),
        data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def send_message(chat_id, text):
    for attempt in range(3):
        try:
            telegram_post("sendMessage", {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}, timeout=25)
            log("sent to {}: {!r:.120}".format(chat_id, text))
            return
        except Exception as e:
            log("send attempt {} FAILED to {}: {} ({})".format(attempt + 1, chat_id, type(e).__name__, e))
            time.sleep(3)


def build_reply(entry_url, chain, status, mediafire_links):
    if status != "ok" and status != "mediafire":
        lines = ["Link gate: could not open", "", entry_url, "", status]
        return "\n".join(lines)
    if status == "mediafire":
        lines = ["File link found — chain stopped at it"]
        lines.extend(sorted(mediafire_links))
        lines.append("")
        lines.append("Hops ({}):".format(len(chain)))
        for idx, (hop, why) in enumerate(chain, 1):
            lines.append("  {}. {}  [{}]".format(idx, hop, why))
        return "\n".join(lines)
    if len(chain) == 1:
        return (
            "No link found on that page (JS-gated or unusual structure).\n\n"
            "Original: {}\n\n"
            "Send the exact link here so I can see its page structure and patch the extractor.".format(entry_url)
        )
    lines = []
    if mediafire_links:
        lines.append("MediaFire link found:")
        lines.extend(sorted(mediafire_links))
        lines.append("")
    final = chain[-1][0]
    lines.append("Link gate opened")
    if len(chain) > 1:
        lines.append("Hops ({}):".format(len(chain)))
        for idx, (hop, why) in enumerate(chain[:-1], 1):
            lines.append("  {}. {}  [{}]".format(idx, hop, why))
        lines.append("")
    lines.append("Final:")
    lines.append(final)
    return "\n".join(lines)


def browser_mode(url, wait=15):
    try:
        proc = subprocess.run(
            [sys.executable, "/root/chain_walker.py", url, str(wait)],
            capture_output=True,
            text=True,
            timeout=420,
        )
        out = proc.stdout
        for line in out.splitlines():
            if line.startswith("EARNLINKS_LINK="):
                earn_url = line.split("=", 1)[1].strip()
                log("browser found earnlinks: {}".format(earn_url))
                chain, status, files = resolve_safe(earn_url)
                if files:
                    return files
        for line in out.splitlines():
            if line.startswith("SUMMARY_FILES="):
                raw = line.split("=", 1)[1].strip()
                return [u for u in raw.split(",") if u]
        return []
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        log("browser mode failed: {}".format(type(e).__name__))
        return []


def handle_message(chat_id, text):
    urls = URL_RE.findall(text)
    if not urls:
        send_message(
            chat_id,
            "Send me a shortener link (vplink.in, linksgo.in, bit.ly, t.co, etc.) and I'll open it.\n\n"
            "No link found in your message.",
        )
        return
    log("resolving {} for chat {}".format(urls[0], chat_id))
    send_message(chat_id, "Opening {}…".format(urls[0]))
    chain, status, file_links = resolve_safe(urls[0])
    if not file_links:
        host = urlparse(urls[0]).netloc
        api_shortener = any(h in host for h in ("linksgo.in", "earnlinks.in", "vplink.in"))
        if api_shortener:
            log("api shortener {} failed (spent/blocked): {}".format(host, status))
        else:
            send_message(chat_id, "Fast scan clean — browser mode on, loops ka wait hoga (10-60 sec)")
            file_links = browser_mode(urls[0])
            if file_links:
                chain, status = [(urls[0], "fast")], "mediafire"
    reply = build_reply(urls[0], chain, status, file_links)
    log("resolved: {} | final: {} | files: {}".format(status, (chain[-1][0] if chain else "none"), len(file_links)))
    send_message(chat_id, reply)


def poll_forever():
    offset = 0
    executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)
    log("polling started")
    while True:
        try:
            data = telegram_post(
                "getUpdates", {"offset": offset, "timeout": 30, "allowed_updates": ["message"]}
            )
            for update in data.get("result", []):
                offset = update["update_id"] + 1
                msg = update.get("message") or {}
                chat_id = msg.get("chat", {}).get("id")
                text = msg.get("text") or ""
                log("update {} from {}: {!r}".format(update.get("update_id"), chat_id, text[:80]))
                if not chat_id or not text:
                    continue
                if ALLOWED_CHAT_IDS and chat_id not in ALLOWED_CHAT_IDS:
                    log("ignored: chat {} not in allowlist".format(chat_id))
                    continue
                if text.startswith("/start"):
                    send_message(
                        chat_id,
                        "Send me any shortener link and I'll resolve it to the real destination. "
                        "Chain and hop count included.",
                    )
                else:
                    executor.submit(handle_message, chat_id, text)
        except Exception as e:
            log("poll error: {} ({})".format(type(e).__name__, e))
            time.sleep(5)


def main():
    if len(sys.argv) >= 3 and sys.argv[1] == "--resolve":
        chain, status, mediafire_links = resolve_safe(sys.argv[2])
        print(build_reply(sys.argv[2], chain, status, mediafire_links))
        return
    if not BOT_TOKEN:
        print("VPLINK_BOT_TOKEN env var required (or use: {} --resolve <url>)".format(sys.argv[0]))
        sys.exit(1)
    poll_forever()


if __name__ == "__main__":
    main()
