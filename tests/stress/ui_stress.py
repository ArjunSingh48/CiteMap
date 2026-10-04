"""Random UI attack: random routes, garbage hashes, random clicks, typing junk, toggling language,
date inputs, offline mode. Fails on any uncaught JS error or a page that renders nothing."""
import asyncio
import os
import random
import socket
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
JUNK_HASH = ["#/a/NOPE", "#/a/", "#/a/A0001/notadate", "#/a/A0001/2026-02-30", "#/a/%3Cscript%3E", "#/zzz",
             "#/a/A0001/9999-12-31", "#/a/A0001/0001-01-01", "#///", "#/changes/extra", "#/rules?x=1", "#/a/A0002/2027-07-02",
             "#/account", "#/how", "#/", "#/a/A0500", "#/a/a0001", "#/a/" + "A" * 3000]


def _port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


def run_ui(n, seed):
    port, sport = _port(), _port()
    env = {**os.environ, "CITEMAP_DB": os.path.join(tempfile.mkdtemp(), "ui.db")}
    srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "server.app:app", "--port", str(port)], cwd=ROOT, env=env,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    st = subprocess.Popen([sys.executable, "-m", "http.server", str(sport)], cwd=os.path.join(ROOT, "web"),
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(3)
    try:
        return asyncio.run(_ui(n, seed, port, sport))
    finally:
        srv.terminate(); st.terminate()


async def _ui(n, seed, port, sport):
    from playwright.async_api import async_playwright
    rng = random.Random(seed)
    fails = []
    async with async_playwright() as p:
        b = await p.chromium.launch()
        for i in range(n):
            base = f"http://127.0.0.1:{port}/" if rng.random() < 0.8 else f"http://127.0.0.1:{sport}/"
            pg = await b.new_page(viewport=rng.choice([{"width": 1280, "height": 900}, {"width": 375, "height": 740}]))
            errs = []
            pg.on("pageerror", lambda e: errs.append(str(e)[:200]))
            try:
                await pg.goto(base + rng.choice(JUNK_HASH + ["#/"] * 3), wait_until="domcontentloaded")
                await pg.wait_for_timeout(700)
                for _ in range(rng.randint(1, 6)):
                    act = rng.choice(["click", "type", "lang", "hash", "date", "back"])
                    if act == "click":
                        els = await pg.query_selector_all("a, button, .pill, .city, tr.click, summary")
                        if els:
                            el = rng.choice(els)
                            try:
                                await el.click(timeout=800)
                            except Exception:
                                pass
                    elif act == "type" and await pg.query_selector("#q"):
                        await pg.fill("#q", rng.choice(["Mission", "<img src=x onerror=alert(1)>", "'", "😀", "A00", "x" * 500]))
                        await pg.keyboard.press(rng.choice(["Enter", "Escape", "ArrowDown"]))
                    elif act == "lang":
                        if pg.url.startswith("about:"):       # the browser left the page (harness navigation), not an app bug
                            continue
                        if not await pg.query_selector("#langBtn"):
                            fails.append(f"langBtn missing at {pg.url[:90]} title={await pg.title()!r} body={(await pg.inner_text('body'))[:80]!r}")
                            break
                        await pg.click("#langBtn")
                    elif act == "hash":
                        await pg.evaluate(f"location.hash = {rng.choice(JUNK_HASH)!r}")
                    elif act == "date" and await pg.query_selector("#asof:not([disabled])"):
                        await pg.fill("#asof", rng.choice(["2027-07-02", "2025-12-31", "1990-01-01"]))
                        await pg.dispatch_event("#asof", "change")
                    elif act == "back":
                        # go somewhere inside the app first, then Back: never leaves the site (fresh tabs start at about:blank)
                        await pg.evaluate(f"location.hash = {rng.choice(JUNK_HASH)!r}")
                        await pg.wait_for_timeout(200)
                        await pg.evaluate("history.back()")
                    await pg.wait_for_timeout(350)
                if not pg.url.startswith(base):       # harness left the site: not an app failure
                    await pg.goto(base, wait_until="domcontentloaded"); await pg.wait_for_timeout(500)
                body = await pg.inner_text("body")
                if "alert(1)" in await pg.content() and await pg.query_selector("img[src=x]"):
                    fails.append("XSS: injected element rendered")
                if len((await pg.inner_text("main")).strip()) < 3:
                    fails.append(f"blank main after actions on {pg.url[:80]}")
                if errs:
                    fails.append(f"JS error: {errs[0]} @ {pg.url[:80]}")
            except Exception as e:
                fails.append(f"UI driver exception: {str(e)[:150]}")
            finally:
                await pg.close()
        await b.close()
    return fails
