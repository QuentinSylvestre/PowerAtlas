import sys, os, json
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
SRC = str(REPO / "src")
CHROME = os.environ.get("PA_DEMO_CHROME") or None   # None: Playwright's own Chromium
sys.path.insert(0, SRC)
import power_atlas.web as w, power_atlas.config as c
from playwright.sync_api import sync_playwright
w.set_local_secret(c.load_local_secret()); cookie = w.make_local_cookie()
sc = json.load(open("/tmp/pa-demo/scenarios.json"))
sid_of = {v: k for k, v in sc.items()}
PROMPTS = {
  "stream": "Retry only the failed rows instead of replaying the whole batch, and add a dead-letter table for rows that keep failing.",
  "approval": "Apply the invoices index migration to the dev database and confirm the query plan uses it.",
}
with sync_playwright() as p:
    b = p.chromium.launch(executable_path=CHROME)
    ctx = b.new_context(viewport={"width": 1440, "height": 900})
    ctx.add_cookies([{"name": "pa_local", "value": cookie, "url": "http://127.0.0.1:4915"}])
    ctx.add_init_script("localStorage.setItem('pa_profile_toast','1')")
    pg = ctx.new_page()
    pg.on("console", lambda m: print("console:", m.text[:200]) if m.type == "error" else None)
    pg.goto("http://127.0.0.1:4915/acp")
    pg.wait_for_timeout(3000)
    for scen, prompt in PROMPTS.items():
        sid = "sess_" + sid_of[scen]
        pg.locator(f'[data-sid="{sid}"]').first.click()
        pg.wait_for_timeout(4000)
        pg.fill("#acpPrompt", prompt)
        pg.keyboard.press("Enter")
        pg.wait_for_timeout(6000)
    b.close()
