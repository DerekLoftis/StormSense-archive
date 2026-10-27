"""Find how the GreenStream dashboard reads GS01/GS02 water levels, and whether the VIMS API key
(env GREENSTREAM_API_KEY, a repository secret) opens the API. Prints request URLs, statuses and the
first bytes of each answer; never prints the key.

    python tools/probe_greenstream.py
"""
import json
import os
import urllib.error
import urllib.request

KEY = os.environ.get("GREENSTREAM_API_KEY", "")
DASH = "https://dashboard.greenstream.cloud/Detail?id=SITE#d3906d8a-46a1-423b-8e12-dc37022b8cef"
SITE = "d3906d8a-46a1-423b-8e12-dc37022b8cef"


def show(s, n=600):
    s = s.replace(KEY, "***") if KEY else s
    return s[:n].replace("\n", " ")


def render():
    from playwright.sync_api import sync_playwright
    seen = []
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(viewport={"width": 1600, "height": 1200})

        def resp(r):
            if r.request.resource_type in ("xhr", "fetch", "document", "websocket", "eventsource"):
                try:
                    body = r.text()
                except Exception:
                    body = ""
                seen.append(r.url)
                print(f"RESP {r.status} {r.request.method} {r.url}\n    {show(body, 400 if 'graphql' not in r.url else 6000)}")
                if "graphql" in r.url:
                    print("    REQHDR", {k: v for k, v in r.request.headers.items() if k.lower() not in ("user-agent",)})
                    print("    REQBODY", show(r.request.post_data or "", 3000))

        page.on("response", resp)
        page.on("websocket", lambda ws: print(f"WS {ws.url}") or ws.on(
            "framereceived", lambda f: print(f"    WSFRAME {show(f if isinstance(f, str) else '<binary>', 300)}")))
        page.goto(DASH, wait_until="domcontentloaded", timeout=90000)
        page.wait_for_timeout(25000)
        print("TITLE", page.title(), "URL", page.url)
        print("TEXT", show(page.inner_text("body"), 1500))
        scripts = page.evaluate("[...document.scripts].map(s => s.src).filter(Boolean)")
        print("SCRIPTS", scripts)
        for src in scripts:
            try:
                js = page.evaluate("u => fetch(u).then(r => r.text())", src)
            except Exception:
                continue
            import re
            for m in sorted(set(re.findall(r"https?://[a-zA-Z0-9.-]*greenstream[a-zA-Z0-9./_-]*", js))):
                print("JSURL", m)
            for m in sorted(set(re.findall(r"[\"'`](/api/[a-zA-Z0-9/_{}$.-]+)", js)))[:80]:
                print("JSPATH", m)
            for m in sorted(set(re.findall(r"[\"'`]((?:x-)?api[-_]?key|Authorization|apikey)[\"'`]", js, re.I))):
                print("JSHDR", m)
            for m in sorted(set(re.findall(r"(?:query|mutation)\s+(\w+)\s*[({]", js))):
                print("JSOP", m)
            for m in re.finditer(r"(?:query|mutation)\s+\w+\s*\([^`]{0,600}", js):
                print("JSQ", show(m.group(0), 600))
            for m in sorted(set(re.findall(r"https?://[a-zA-Z0-9.-]+\.(?:amazonaws\.com|execute-api[^\"'`]*)[a-zA-Z0-9./_-]*", js))):
                print("JSAWS", m)
        b.close()
    return seen


def try_api():
    if not KEY:
        print("no GREENSTREAM_API_KEY; skipping API calls")
        return
    bases = ["https://api.greenstream.cloud", "https://api.greenstream.cloud/api", "https://api.greenstream.cloud/v1",
             "https://dashboard.greenstream.cloud/api"]
    paths = ["", "/sites", "/Sites", f"/sites/{SITE}", f"/site/{SITE}", "/locations", "/devices",
             f"/sites/{SITE}/data", f"/sites/{SITE}/history", f"/sites/{SITE}/readings"]
    auths = [("x-api-key", KEY), ("Authorization", f"Bearer {KEY}"), ("Authorization", KEY), ("apikey", KEY),
             ("Ocp-Apim-Subscription-Key", KEY)]
    for base in bases:
        for path in paths:
            url = base + path
            got = set()
            for h, v in auths + [("?key", None)]:
                req = urllib.request.Request(url + (f"?key={KEY}" if v is None else ""),
                                             headers={} if v is None else {h: v})
                try:
                    with urllib.request.urlopen(req, timeout=30) as r:
                        body = r.read(2000).decode("utf-8", "replace")
                        code = r.status
                except urllib.error.HTTPError as e:
                    code, body = e.code, e.read(300).decode("utf-8", "replace")
                except Exception as e:
                    code, body = "ERR", str(e)
                if (code, body[:80]) in got:
                    continue
                got.add((code, body[:80]))
                print(f"API {code} {h} {url}\n    {show(body, 500)}")


if __name__ == "__main__":
    try:
        render()
    except Exception as e:
        print("render failed:", e)
    try_api()
    print(json.dumps({"done": True}))
