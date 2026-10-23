"""Build a fake HOME full of synthetic sessions for the README screenshots.

Run through ``run.sh``, never directly against a real home: it deletes ``~/.kiro``, ``~/.claude``,
``~/.codex``, ``~/.config`` and ``~/code``, and refuses to run unless HOME is exactly /home/demo.
Every name, prompt and reply below is invented.
"""
import base64, json, os, shutil, subprocess, sys, time, uuid
from datetime import datetime, timezone
from pathlib import Path

HOME = Path(os.environ["HOME"])
WORK = Path("/tmp/pa-demo")
assert str(HOME) == "/home/demo", "refusing to seed a real home"
NOW = time.time()
CODE = HOME / "code"


def iso(ago_min):
    return datetime.fromtimestamp(NOW - ago_min * 60, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def uid(seed):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "poweratlas-demo/" + seed))


def touch(path, ago_min):
    t = NOW - ago_min * 60
    os.utime(path, (t, t))


for sub in (".kiro", ".claude", ".codex", ".config", "code"):
    shutil.rmtree(HOME / sub, ignore_errors=True)

# (name, [(provider, title, prompt, reply, age_min)...])
W = {
    "atlas-api": [
        ("claude-code", "Add cursor pagination to /v2/events", "Add cursor-based pagination to the /v2/events endpoint and keep the old offset params working for one release.", "Done. Cursor pagination is in `events.py`; offset params emit a Deprecation header. 14 tests added, all green.", 6),
        ("kiro-cli-v3", "Rate limiter returns 500 on burst", "Our rate limiter returns a 500 instead of a 429 when a burst hits the Redis script. Find out why.", "The Lua script raised on a nil TTL. I now default it to the window size and return 429 with Retry-After.", 190),
        ("codex", "Migrate settings to pydantic v2", "Migrate config/settings.py to pydantic-settings v2 without changing env var names.", "Migrated. `Settings.model_config` replaces the inner Config class; env names unchanged.", 2600),
    ],
    "mobile-app": [
        ("kiro-cli-v3", "Fix keyboard overlap on checkout form", "On small iPhones the keyboard covers the Pay button on the checkout form. Fix it.", "Wrapped the form in KeyboardAvoidingView and scroll the focused field into view.", 35),
        ("claude-code", "Offline queue for analytics events", "Design an offline queue for analytics events that survives app restarts.", "Proposed a SQLite-backed queue with exponential backoff; implementation plan below.", 410),
        ("kiro-ide", "Dark mode colour audit", "Audit the dark mode palette for WCAG AA contrast.", "11 token pairs fail AA. I listed fixes in `docs/contrast.md`.", 4300),
    ],
    "infra-terraform": [
        ("codex", "Split VPC module per environment", "Split the monolithic VPC module into per-environment stacks without recreating resources.", "Used `moved` blocks so state migrates in place. `terraform plan` shows no changes for prod.", 70),
        ("claude-code", "Cost drift in staging cluster", "Why did staging EKS cost double last week?", "Two forgotten GPU node groups from the load test. Scaled both to zero and added a TTL tag.", 880),
        ("kiro-cli-v3", "Rotate the CI deploy role", "Rotate the CI deploy role and tighten its trust policy.", "New role created with a repo-scoped OIDC condition; the old one is detached.", 5200),
    ],
    "docs-site": [
        ("kiro-ide", "Rewrite the quickstart", "Rewrite the quickstart so a new user reaches a working example in under five minutes.", "New quickstart is 6 steps; I cut the Docker section into its own page.", 120),
        ("kiro-cli-v3", "Broken links after sidebar move", "The sidebar move broke 31 internal links. Fix them.", "Added redirects for 31 moved pages and a link checker to CI.", 1500),
        ("claude-code", "Search ranking tweaks", "Boost API reference pages in search results.", "Added a 1.5x weight for /reference and tested against the top 50 queries.", 6100),
    ],
    "data-pipeline": [
        ("claude-code", "Backfill 2025 events into the warehouse", "Write a resumable backfill for the 2025 events into the warehouse, 100k rows per batch.", "Backfill job is checkpointed per day; a crash resumes from the last committed batch.", 1),
        ("codex", "Dedupe late-arriving rows", "Late-arriving rows create duplicates in `fct_orders`. Dedupe them.", "Added a merge on (order_id, updated_at) and a uniqueness test.", 300),
        ("kiro-cli-v3", "Airflow DAG timeouts", "The nightly DAG times out on the join step. Investigate.", "The join skews on a null key; salted it and runtime dropped from 52 to 9 minutes.", 2900),
    ],
    "design-system": [
        ("kiro-ide", "Button focus ring variants", "Add focus ring variants to the Button component.", "Added `outline`, `ring` and `none` variants with tokens.", 240),
        ("claude-code", "Publish v3 to the private registry", "Prepare the v3.0 release and publish to the private registry.", "Changelog written, tags pushed, package published as 3.0.0-rc.1.", 2000),
        ("codex", "Storybook a11y checks in CI", "Run Storybook accessibility checks in CI and fail on violations.", "Added test-runner with axe; two existing stories were fixed.", 7000),
    ],
    "billing-service": [
        ("kiro-cli-v3", "Proration for mid-cycle plan changes", "Implement proration for mid-cycle plan upgrades and downgrades.", "Proration is computed per day with banker's rounding; see `proration.py`.", 15),
        ("claude-code", "Idempotency keys on /charges", "Make POST /charges idempotent with client-supplied keys.", "Keys are stored 24h with the response hash; replays return the first response.", 700),
        ("codex", "Tax rounding mismatch with Stripe", "Our tax rounding differs from Stripe by 1 cent on some invoices.", "We round per line; Stripe rounds the total. Switched to total rounding.", 3800),
    ],
    "ml-experiments": [
        ("codex", "Tune LoRA rank for the support model", "Sweep LoRA rank 8 to 64 on the support dataset and report eval loss.", "Rank 16 matches rank 64 within noise at a quarter of the memory.", 95),
        ("kiro-cli-v3", "Eval harness flaky on GPU", "The eval harness gives different scores per run on GPU.", "Non-deterministic scatter ops. Pinned seeds and enabled deterministic algorithms.", 1100),
        ("claude-code", "Plot loss curves for the report", "Plot training and eval loss curves for all five runs with shared axes.", "Saved `figures/loss.png` and a script to regenerate it.", 5000),
    ],
    "cli-tools": [
        ("claude-code", "Add shell completions", "Generate bash, zsh and fish completions from the argument parser.", "Completions generated at build time and installed by `make install`.", 500),
        ("kiro-cli-v3", "Windows path handling bug", "`pa-sync` fails on paths with spaces on Windows.", "Switched to `subprocess` argument lists and added a regression test.", 2300),
        ("codex", "Cut cold start time", "Cold start is 1.4s. Get it under 400ms.", "Lazy-imported two heavy modules; cold start is now 310ms.", 8000),
    ],
    "marketing-site": [
        ("kiro-ide", "Pricing page A/B variant", "Create a B variant of the pricing page with annual billing selected by default.", "Variant added behind the `pricing_b` flag; events wired to analytics.", 360),
        ("claude-code", "Fix CLS on the hero", "Layout shifts on the hero image hurt our Core Web Vitals.", "Reserved the aspect ratio and preloaded the font. CLS 0.21 to 0.01.", 1800),
        ("kiro-cli-v3", "Sitemap missing localised pages", "The sitemap omits localised pages.", "Generated hreflang entries for all 6 locales.", 9000),
    ],
}


EXTRA = {
    "atlas-api": ("Per-row retries for the bulk importer", "The bulk importer replays a whole batch when one row fails. Make retries per row.", "Understood, I'll start from `src/importer/batch.py`.", 3),
    "billing-service": ("Partial index on invoices.status", "Invoice list queries scan the whole table when filtering by status. Add a partial index.", "The migration is drafted. It needs to run against the dev database.", 8),
    "mobile-app": ("Splash screen flicker on Android", "The splash screen flickers for a frame before the first screen on Android 14.", "The theme switches before the activity draws. I moved the splash to the SplashScreen API.", 52),
    "infra-terraform": ("Alarm for NAT gateway egress", "Add a CloudWatch alarm when NAT gateway egress exceeds 2 TB per day.", "Alarm added with an SNS topic and a runbook link in the description.", 260),
    "docs-site": ("Draft the 2.0 migration guide", "Draft a migration guide from 1.x to 2.0 from the changelog.", "Draft is in `docs/migrate-2-0.md`: 9 breaking changes, each with before and after snippets.", 640),
    "data-pipeline": ("Freshness checks on dbt sources", "Add dbt source freshness checks and fail the nightly run if a source is over 6 hours late.", "Freshness checks added for all 14 sources; `orders` warns at 3h and errors at 6h.", 90),
    "design-system": ("Spacing scale audit", "Audit the components for off-scale spacing values.", "47 hard-coded values map to tokens; the 6 that do not are listed in the PR.", 1300),
    "ml-experiments": ("Cache tokenised dataset shards", "Tokenising the dataset takes 40 minutes at the start of every run. Cache the shards.", "Shards are cached by content hash; a warm start now takes 90 seconds.", 420),
    "cli-tools": ("Self-update command", "Add a `pa self-update` command that checks releases and verifies the checksum.", "Implemented with a dry-run flag and a checksum check before replacing the binary.", 1700),
    "marketing-site": ("Compress the hero video", "The hero video is 18 MB. Get it under 2 MB without visible banding.", "Re-encoded to AV1 with a poster frame: 1.7 MB.", 2500),
}
for _ws, (_t, _p, _r, _a) in EXTRA.items():
    W[_ws].append(("kiro-cli-v3", _t, _p, _r, _a))

# Which workspaces get which cwd
for name in W:
    (CODE / name).mkdir(parents=True)
    (CODE / name / "README.md").write_text(f"# {name}\n")

# ---- providers ---------------------------------------------------------------------
ids = {}   # (ws, idx) -> session id
claude_hist = []


def tool_pair(i, name, args, ok=True):
    return [
        {"id": f"t{i}", "timestamp": "", "payload": {"type": "tool_call", "toolCallId": f"call{i}", "toolName": name, "args": args}},
        {"id": f"r{i}", "timestamp": "", "payload": {"type": "tool_result", "toolCallId": f"call{i}", "success": ok}},
    ]


def write_v3(ws, title, prompt, reply, ago, sid):
    h = base64.b16encode(ws.encode()).decode()[:16].lower()
    d = HOME / ".kiro" / "sessions" / h / f"sess_{sid}"
    d.mkdir(parents=True)
    (d / "session.json").write_text(json.dumps({
        "id": f"sess_{sid}", "title": title, "workspacePaths": [str(CODE / ws)],
        "createdAt": iso(ago + 25), "lastModifiedAt": iso(ago), "agentMode": "kiro_default"}))
    msgs = [{"id": "m1", "timestamp": iso(ago + 25), "payload": {"type": "user", "content": [{"type": "text", "text": prompt}]}}]
    pkg = "src/" + ws.replace("-", "_") + "/core.py"
    msgs += tool_pair(1, "fs_read", {"path": pkg})
    msgs += tool_pair(2, "execute_bash", {"command": "pytest -q"})
    msgs.append({"id": "m2", "timestamp": iso(ago), "payload": {"type": "assistant", "content": [{"type": "text", "text": reply}]}})
    (d / "messages.jsonl").write_text("\n".join(json.dumps(m) for m in msgs) + "\n")
    touch(d / "session.json", ago); touch(d / "messages.jsonl", ago)


def claude_lines(ws, title, prompt, reply, ago, sid, hit_reset=None):
    cwd = str(CODE / ws)
    L = [
        {"type": "ai-title", "aiTitle": title, "sessionId": sid},
        {"parentUuid": None, "type": "user", "cwd": cwd, "sessionId": sid, "timestamp": iso(ago + 25), "uuid": "u1",
         "message": {"role": "user", "content": prompt}},
        {"parentUuid": "u1", "type": "assistant", "cwd": cwd, "sessionId": sid, "timestamp": iso(ago + 20), "uuid": "a1",
         "message": {"role": "assistant", "model": "claude-opus-5-5", "content": [{"type": "text", "text": "On it. Reading the relevant files first."},
                                                                                {"type": "tool_use", "id": "tu1", "name": "Read", "input": {"file_path": cwd + "/src/main.py"}}],
                     "usage": {"input_tokens": 3200, "output_tokens": 410, "cache_read_input_tokens": 18000, "cache_creation_input_tokens": 2100}}},
        {"parentUuid": "a1", "type": "user", "cwd": cwd, "sessionId": sid, "timestamp": iso(ago + 19), "uuid": "u2",
         "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tu1", "content": "ok"}]}},
    ]
    if hit_reset is None:
        L.append({"parentUuid": "u2", "type": "assistant", "cwd": cwd, "sessionId": sid, "timestamp": iso(ago), "uuid": "a2",
                  "message": {"role": "assistant", "model": "claude-opus-5-5", "content": [{"type": "text", "text": reply}],
                              "usage": {"input_tokens": 4100, "output_tokens": 900, "cache_read_input_tokens": 22000, "cache_creation_input_tokens": 1500}}})
    else:
        L.append({"parentUuid": "u2", "type": "assistant", "cwd": cwd, "sessionId": sid, "timestamp": iso(ago), "uuid": "a2",
                  "apiErrorStatus": 429, "isApiErrorMessage": True,
                  "quotaLimits": {"rateLimitType": "five_hour", "resetsAt": hit_reset},
                  "message": {"role": "assistant", "content": [{"type": "text", "text": "You've hit your session limit"}]}})
    return L


def write_claude(ws, title, prompt, reply, ago, sid, hit_reset=None, hit_provider=None):
    cwd = str(CODE / ws)
    folder = HOME / ".claude" / "projects" / "".join(c if c.isalnum() else "-" for c in cwd)
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / f"{sid}.jsonl"
    p.write_text("\n".join(json.dumps(x) for x in claude_lines(ws, title, prompt, reply, ago, sid, hit_reset)) + "\n")
    touch(p, ago)
    claude_hist.append({"display": prompt[:60], "timestamp": int((NOW - ago * 60) * 1000), "project": cwd, "sessionId": sid})


def codex_records(ws, prompt, reply, ago, sid, limit=None):
    cwd = str(CODE / ws)

    def ev(t, item):
        return {"timestamp": t, "type": "event_msg", "payload": {"type": "item_completed", "item": item}}
    R = [{"timestamp": iso(ago + 25), "type": "session_meta", "payload": {"id": sid, "cwd": cwd, "source": "cli", "timestamp": iso(ago + 25), "model_provider": "openai"}},
         ev(iso(ago + 24), {"type": "UserMessage", "content": [{"type": "text", "text": prompt}]}),
         {"timestamp": iso(ago + 23), "type": "event_msg", "payload": {"type": "task_started"}}]
    if limit is None:
        R.append(ev(iso(ago + 2), {"type": "AgentMessage", "content": [{"type": "text", "text": reply}]}))
        R.append({"timestamp": iso(ago), "type": "event_msg", "payload": {"type": "task_complete"}})
    else:
        R.append({"timestamp": iso(ago + 1), "type": "event_msg", "payload": {"type": "token_count", "rate_limits": {
            "primary": {"used_percent": 100.0, "window_minutes": 300, "resets_at": limit},
            "secondary": {"used_percent": 61.0, "window_minutes": 10080, "resets_at": NOW + 4 * 86400}}}})
        R.append({"timestamp": iso(ago), "type": "event_msg", "payload": {"type": "task_complete", "error": {
            "codex_error_info": "usage_limit_exceeded", "message": "You've hit your usage limit."}}})
    return R


def write_codex(ws, title, prompt, reply, ago, sid, limit=None):
    d = HOME / ".codex" / "sessions" / "2026" / "10" / "23"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"rollout-2026-10-23T00-00-00-{sid}.jsonl"
    p.write_text("\n".join(json.dumps(x) for x in codex_records(ws, prompt, reply, ago, sid, limit)) + "\n")
    touch(p, ago)
    with (HOME / ".codex" / "session_index.jsonl").open("a") as f:
        f.write(json.dumps({"id": sid, "thread_name": title, "updated_at": iso(ago)}) + "\n")


def write_ide(ws, title, prompt, reply, ago, sid):
    base = HOME / ".config" / "Kiro" / "User" / "globalStorage" / "kiro.kiroagent" / "workspace-sessions"
    enc = base64.b64encode(str(CODE / ws).encode()).decode().replace("+", "-").replace("/", "_").replace("=", "?")
    d = base / enc
    d.mkdir(parents=True, exist_ok=True)
    sj = d / "sessions.json"
    cur = json.loads(sj.read_text()) if sj.exists() else []
    cur.append({"sessionId": sid, "title": title, "dateCreated": str(int((NOW - ago * 60) * 1000)), "workspaceDirectory": str(CODE / ws)})
    sj.write_text(json.dumps(cur))
    (d / f"{sid}.json").write_text(json.dumps({"history": [
        {"message": {"role": "user", "content": [{"type": "text", "text": prompt}]}},
        {"message": {"role": "assistant", "content": reply}}]}))


WRITERS = {"kiro-cli-v3": write_v3, "claude-code": write_claude, "codex": write_codex, "kiro-ide": write_ide}
# special sessions by (ws, index): quota stops
quota_claude = (NOW + 95 * 60)      # resets in 1h35
quota_codex = (NOW + 140 * 60)
SPECIAL = {}
for ws, sess in W.items():
    for i, (prov, title, prompt, reply, ago) in enumerate(sess):
        sid = uid(f"{ws}/{i}")
        if prov == "kiro-ide":
            sid = "ide-" + sid[:16]
        ids[(ws, i)] = sid

# quota-stopped: ml-experiments codex (idx0), billing-service claude (idx1)
hit_claude_ws = ("billing-service", 1)
hit_codex_ws = ("ml-experiments", 0)

for ws, sess in W.items():
    for i, (prov, title, prompt, reply, ago) in enumerate(sess):
        sid = ids[(ws, i)]
        if (ws, i) == hit_claude_ws:
            ago = 12
            write_claude(ws, title, prompt, reply, ago, sid, hit_reset=quota_claude)
        elif (ws, i) == hit_codex_ws:
            ago = 9
            write_codex(ws, title, prompt, reply, ago, sid, limit=quota_codex)
        else:
            WRITERS[prov](ws, title, prompt, reply, ago, sid)

(HOME / ".claude" / "history.jsonl").write_text("\n".join(json.dumps(x) for x in claude_hist) + "\n")

# ---- quota snapshot (Claude statusline) --------------------------------------------
cfgdir = HOME / ".config" / "power-atlas"
cfgdir.mkdir(parents=True, exist_ok=True)
(cfgdir / "claude-statusline.json").write_text(json.dumps({"rate_limits": {
    "five_hour": {"used_percentage": 100, "resets_at": quota_claude},
    "seven_day": {"used_percentage": 48, "resets_at": NOW + 3.2 * 86400}}}))

# ---- git: one dirty workspace ---------------------------------------------------------
dirty = CODE / "data-pipeline"
env = dict(os.environ, GIT_AUTHOR_NAME="Demo", GIT_AUTHOR_EMAIL="demo@example.com", GIT_COMMITTER_NAME="Demo", GIT_COMMITTER_EMAIL="demo@example.com")
def git(*a, cwd=dirty):
    subprocess.run(["git", *a], cwd=cwd, env=env, check=True, capture_output=True)
git("init", "-q", "-b", "main")
git("add", "-A"); git("commit", "-qm", "Initial commit")
(dirty / "backfill.py").write_text("# resumable 2025 backfill\n")
(dirty / "README.md").write_text("# data-pipeline\n\nNightly warehouse loads.\n")

# ---- config -------------------------------------------------------------------------
launchers = [
    {"id": uid("l1"), "name": "Lazygit", "command": "lazygit", "custom_args": "", "cwd": "", "env": {}, "color": "#f97316", "terminal": True, "use_selected_workspaces": True, "show_in_workspace_hover": True},
    {"id": uid("l2"), "name": "VS Code", "command": "code", "custom_args": ".", "cwd": "", "env": {}, "color": "#3b82f6", "terminal": False, "use_selected_workspaces": True, "show_in_workspace_hover": True},
    {"id": uid("l3"), "name": "htop", "command": "htop", "custom_args": "", "cwd": "", "env": {}, "color": "#22c55e", "terminal": True, "use_selected_workspaces": False, "show_in_workspace_hover": False},
]
pinned_sessions = [ids[("atlas-api", 0)], ids[("data-pipeline", 0)]]
pinned_folders = [str(CODE / "atlas-api")]
tags = {"backend": "#06b6d4", "frontend": "#a855f7", "ops": "#f59e0b", "hidden": ""}
wsset = {
    "atlas-api": (["backend"], "#06b6d4"), "billing-service": (["backend"], ""), "data-pipeline": (["backend"], ""),
    "mobile-app": (["frontend"], "#a855f7"), "design-system": (["frontend"], ""), "marketing-site": (["frontend"], ""), "docs-site": (["frontend"], ""),
    "infra-terraform": (["ops"], "#f59e0b"), "cli-tools": ([], ""), "ml-experiments": ([], "#ec4899"),
}
import tomli_w
cfg = {
    "port": 4915, "default_directory": str(CODE), "pinned_folders": pinned_folders, "pinned_sessions": pinned_sessions,
    "active_launch_profile": "default", "auto_schedule": True,
    "custom_launchers": launchers,
    "launch_profiles": [{"id": "default", "name": "Default", "terminal_command": "x-terminal-emulator -e {pscmd}", "wt_profile": "PowerShell"}],
    "provider_settings": {"claude-code": {"enabled": True}, "codex": {"enabled": True}, "kiro-ide": {"enabled": True}, "kiro-cli-v3": {"enabled": True}},
    "workspace_settings": {str(CODE / k): {"tags": v[0], **({"color": v[1]} if v[1] else {})} for k, v in wsset.items()},
    "tag_settings": {k: {"color": v} for k, v in tags.items()},
    "notifications": {"enabled": False},
}
(cfgdir / "config.toml").write_text(tomli_w.dumps(cfg))

WORK.mkdir(exist_ok=True)
json.dump({ids[("atlas-api", 3)]: "stream", ids[("billing-service", 3)]: "approval"}, open(WORK / "scenarios.json", "w"))
json.dump({f"{k[0]}/{k[1]}": v for k, v in ids.items()}, open(WORK / "ids.json", "w"), indent=1)
print("seeded", len(ids), "sessions")
