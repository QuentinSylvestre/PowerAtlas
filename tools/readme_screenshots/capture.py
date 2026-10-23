import sys, os, json
from pathlib import Path
REPO = Path(__file__).resolve().parents[2]
SRC = str(REPO / "src")
CHROME = os.environ.get("PA_DEMO_CHROME") or None   # None: Playwright's own Chromium
sys.path.insert(0, SRC)
import power_atlas.web as w, power_atlas.config as c
from playwright.sync_api import sync_playwright
from PIL import Image, ImageDraw
OUT = str(REPO / "docs" / "screenshots")
w.set_local_secret(c.load_local_secret()); cookie = w.make_local_cookie()
sc = json.load(open("/tmp/pa-demo/scenarios.json")); sid_of = {v: "sess_" + k for k, v in sc.items()}
BASE = "http://127.0.0.1:4915"
with sync_playwright() as p:
    b = p.chromium.launch(executable_path=CHROME)
    # desktop dashboard
    ctx = b.new_context(viewport={"width": 1440, "height": 960}, device_scale_factor=2)
    ctx.add_cookies([{"name": "pa_local", "value": cookie, "url": BASE}])
    ctx.add_init_script("localStorage.setItem('pa_profile_toast','1')")
    pg = ctx.new_page(); pg.goto(BASE + "/"); pg.wait_for_timeout(9000)
    pg.screenshot(path=f"{OUT}/dashboard.png"); ctx.close()
    # phone /acp
    ctx = b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True, has_touch=True)
    ctx.add_cookies([{"name": "pa_local", "value": cookie, "url": BASE}])
    pg = ctx.new_page(); pg.goto(BASE + "/acp"); pg.wait_for_timeout(4000)
    pg.screenshot(path=f"{OUT}/acp-phone-sessions.png")
    pg.locator(f'[data-sid="{sid_of["stream"]}"]').first.click(); pg.wait_for_timeout(3500)
    pg.screenshot(path=f"{OUT}/acp-phone-workflow.png")
    pg.get_by_text("retry-per-row").first.click(); pg.wait_for_timeout(2500)
    pg.screenshot(path=f"{OUT}/acp-phone-workflow-step.png")
    pg.goto(BASE + "/acp"); pg.wait_for_timeout(3000)
    pg.locator(f'[data-sid="{sid_of["approval"]}"]').first.click(); pg.wait_for_timeout(3000)
    pg.screenshot(path=f"{OUT}/acp-phone-approval.png")
    b.close()
names = ["sessions", "workflow", "workflow-step", "approval"]
ims = [Image.open(f"{OUT}/acp-phone-{n}.png").convert("RGB") for n in names]
def rounded(im, r=44):
    m = Image.new("L", im.size, 0); ImageDraw.Draw(m).rounded_rectangle((0, 0, *im.size), r, fill=255)
    o = Image.new("RGBA", im.size); o.paste(im, (0, 0), m); return o
gap, pad = 48, 56
W = sum(i.width for i in ims) + gap * (len(ims) - 1) + 2 * pad
H = ims[0].height + 2 * pad
bg = Image.new("RGB", (W, H), (11, 14, 22))
x = pad
for im in ims:
    r = rounded(im); bg.paste(r, (x, pad), r); x += im.width + gap
bg = bg.resize((W // 2, H // 2), Image.LANCZOS)
bg.save(f"{OUT}/acp-phone-overview.png", optimize=True)
for n in os.listdir(OUT):
    if n.endswith(".png"):
        Image.open(f"{OUT}/{n}").save(f"{OUT}/{n}", optimize=True)
print(os.listdir(OUT))
