#!/usr/bin/env python3
"""A stand-in for `kiro-cli acp --agent-engine v3`, for README screenshots only.

Speaks just enough ACP for PowerAtlas: initialize, session/new, session/load
(replaying the stored messages.jsonl), session/prompt with scripted scenarios.
"""
import json, os, sys, threading, time, uuid
from datetime import datetime, timezone
from pathlib import Path

HOME = Path(os.environ.get("HOME", "/home/demo"))
ROOT = HOME / ".kiro" / "sessions"
LOG = open("/tmp/pa-demo/fake_kiro.log", "a", buffering=1)
out_lock = threading.Lock()
pending = {}          # request id -> (event, [response])
next_id = [1000]


def log(*a):
    LOG.write(" ".join(str(x) for x in a) + "\n")


def send(obj):
    with out_lock:
        sys.stdout.write(json.dumps(obj) + "\n")
        sys.stdout.flush()


def notify(method, params):
    send({"jsonrpc": "2.0", "method": method, "params": params})


def update(sid, upd):
    notify("session/update", {"sessionId": sid, "update": upd})


def agent_request(method, params, timeout=None):
    next_id[0] += 1
    rid = next_id[0]
    ev = threading.Event()
    box = []
    pending[rid] = (ev, box)
    send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
    ev.wait(timeout)
    return box[0] if box else None


def find_dir(sid):
    for h in ROOT.glob("*/"):
        d = h / sid
        if d.is_dir():
            return d
    return None


def text_chunks(sid, text, delay=0.04, kind="agent_message_chunk"):
    words = text.split(" ")
    for i in range(0, len(words), 3):
        piece = " ".join(words[i:i + 3]) + (" " if i + 3 < len(words) else "")
        update(sid, {"sessionUpdate": kind, "content": {"type": "text", "text": piece}})
        time.sleep(delay)


def replay(sid):
    d = find_dir(sid)
    if not d or not (d / "messages.jsonl").exists():
        return
    for line in (d / "messages.jsonl").read_text().splitlines():
        try:
            p = json.loads(line)["payload"]
        except Exception:
            continue
        t = p.get("type")
        if t in ("user", "assistant"):
            txt = " ".join(c.get("text", "") for c in p.get("content", []) if isinstance(c, dict))
            update(sid, {"sessionUpdate": "user_message_chunk" if t == "user" else "agent_message_chunk",
                         "content": {"type": "text", "text": txt}, "_meta": {"kiro": {"replay": True}}})
        elif t == "tool_call":
            a = p.get("args") or {}
            name = p.get("toolName", "tool")
            kind = {"fs_read": "read", "fs_write": "edit"}.get(name, "execute")
            title = f"Reading {a.get('path', '')}" if kind == "read" else f"Running {a.get('command', '')}"
            update(sid, {"sessionUpdate": "tool_call", "toolCallId": p.get("toolCallId"),
                         "title": title,
                         "kind": kind, "status": "in_progress", "rawInput": a,
                         "locations": [{"path": a.get("path", "")}] if a.get("path") else []})
        elif t == "tool_result":
            update(sid, {"sessionUpdate": "tool_call_update", "toolCallId": p.get("toolCallId"),
                         "status": "completed" if p.get("success") else "failed"})


def metadata(sid, pct):
    notify("_kiro.dev/metadata", {"sessionId": sid, "contextUsagePercentage": pct})


def mcp_status(sid):
    notify("_kiro/mcp/status", {"sessionId": sid, "servers": [
        {"name": n, "status": "connected", "tools": [{"name": f"t{i}"} for i in range(c)]}
        for n, c in (("github", 41), ("postgres", 9), ("viz", 1))]})


def wf(method, **p):
    notify("_kiro/workflow/" + method, p)


def child_stream(child, lines, tools=(), delay=0.05, hold=False):
    for title, kind in tools:
        tid = "tc-" + uuid.uuid4().hex[:6]
        update(child, {"sessionUpdate": "tool_call", "toolCallId": tid, "title": title, "kind": kind, "status": "in_progress", "rawInput": {}})
        time.sleep(0.4)
        update(child, {"sessionUpdate": "tool_call_update", "toolCallId": tid, "status": "completed"})
    for ln in lines:
        text_chunks(child, ln, delay=delay)
    if hold:
        time.sleep(3600)


def run_workflow_scenario(sid):
    W = "wf-" + uuid.uuid4().hex[:8]
    text_chunks(sid, "Per-row retries touch the batcher, the failure handling and the schema, so I'll run this as a workflow: analyse the failures first, then build the retry path and the dead-letter table in parallel, then run the importer tests.")
    update(sid, {"sessionUpdate": "tool_call", "toolCallId": "tc-wf", "title": "Run workflow: harden-importer", "kind": "other", "status": "in_progress",
                 "rawInput": {"name": "harden-importer"}})
    time.sleep(1.0)
    update(sid, {"sessionUpdate": "tool_call_update", "toolCallId": "tc-wf", "status": "completed"})
    tree = [{"nodeId": "analyse-failures", "type": "step", "agentName": "wf-analyst"},
            {"nodeId": "build", "type": "parallel", "branches": [
                {"nodeId": "retry-per-row", "type": "step", "agentName": "wf-coder"},
                {"nodeId": "dead-letter-table", "type": "step", "agentName": "wf-coder"}]},
            {"nodeId": "run-tests", "type": "step", "agentName": "wf-tester"}]
    wf("run_start", workflowId=W, workflowName="harden-importer", inputs={}, nodeTree=tree, parentSessionId=sid)
    # step 1
    c1 = "sess_" + str(uuid.uuid4())
    wf("node_start", workflowId=W, nodeId="analyse-failures", nodePath=[W, "analyse-failures"], type="step", agentName="wf-analyst", parentSessionId=sid)
    time.sleep(0.2)
    wf("node_start", workflowId=W, nodeId="analyse-failures", nodePath=[W, "analyse-failures"], type="step", agentName="wf-analyst", sessionId=c1, parentSessionId=sid)
    child_stream(c1, ["Scanning the last 30 days of importer failures. 412 failed batches, 96% caused by a single malformed row. Retrying the whole batch replays 4,900 good rows each time."],
                 tools=[("Searching for `retry` in src/importer", "search"), ("Reading src/importer/batch.py", "read")])
    notify("_kiro/session/notify", {"sessionId": sid, "callerSessionId": c1, "message": "Root cause confirmed: whole-batch retry. Recommend per-row retry plus a dead-letter table.",
                                    "severity": "success", "sender": "step", "workflowId": W, "nodeId": "analyse-failures", "agentName": "wf-analyst"})
    wf("node_complete", workflowId=W, nodeId="analyse-failures", nodePath=[W, "analyse-failures"], status="completed",
       capturedOutput="Whole-batch retry replays good rows; recommend per-row retry + dead-letter table.", parentSessionId=sid)
    # parallel container + two branches
    wf("node_start", workflowId=W, nodeId="build", nodePath=[W, "build"], type="parallel", parentSessionId=sid)
    c2, c3 = "sess_" + str(uuid.uuid4()), "sess_" + str(uuid.uuid4())
    for nid, ag, cs in (("retry-per-row", "wf-coder", c2), ("dead-letter-table", "wf-coder", c3)):
        wf("node_start", workflowId=W, nodeId=nid, nodePath=[W, "build", nid], type="step", agentName=ag, branchId=nid, parentSessionId=sid)
        time.sleep(0.2)
        wf("node_start", workflowId=W, nodeId=nid, nodePath=[W, "build", nid], type="step", agentName=ag, branchId=nid, sessionId=cs, parentSessionId=sid)
    # the dead-letter branch finishes; the retry branch is still working
    child_stream(c3, ["Added the `import_dead_letter` table with an Alembic migration and a repository class."],
                 tools=[("Editing migrations/0043_import_dead_letter.py", "edit")])
    notify("_kiro/session/notify", {"sessionId": sid, "callerSessionId": c3, "message": "Dead-letter table and repository are in place.",
                                    "severity": "success", "sender": "step", "workflowId": W, "nodeId": "dead-letter-table", "agentName": "wf-coder"})
    wf("node_complete", workflowId=W, nodeId="dead-letter-table", nodePath=[W, "build", "dead-letter-table"], status="completed",
       capturedOutput="Table + repository added.", parentSessionId=sid)
    send({"jsonrpc": "2.0", "id": PROMPT_IDS.get(sid), "result": {"stopReason": "end_turn"}})
    PROMPT_IDS[sid] = None
    child_stream(c2, ["Splitting the batch loop: each row now retries up to three times with backoff, and rows that still fail go to the dead-letter table. Updating the batcher and its tests"],
                 tools=[("Reading src/importer/batch.py", "read"), ("Editing src/importer/batch.py", "edit")], hold=True)


PROMPT_IDS = {}


def run_prompt(rid, sid, text):
    PROMPT_IDS[sid] = rid
    """Scripted scenarios keyed by a marker in the prompt."""
    log("prompt", sid, text[:80])
    try:
        scenario = json.load(open("/tmp/pa-demo/scenarios.json")).get(sid.replace("sess_", ""), "")
    except Exception:
        scenario = ""
    time.sleep(0.3)
    if scenario == "approval":
        text_chunks(sid, "I'll check the migration state first, then apply the pending schema change.")
        update(sid, {"sessionUpdate": "tool_call", "toolCallId": "tc-a1", "title": "Reading db/migrations/0042_add_invoice_index.sql",
                     "kind": "read", "status": "in_progress", "rawInput": {"path": "db/migrations/0042_add_invoice_index.sql"}})
        time.sleep(0.4)
        update(sid, {"sessionUpdate": "tool_call_update", "toolCallId": "tc-a1", "status": "completed"})
        text_chunks(sid, "The migration adds a partial index on `invoices(status)`. Applying it needs a database write, so I need your approval.")
        metadata(sid, 37.4)
        resp = agent_request("session/request_permission", {
            "sessionId": sid,
            "toolCall": {"toolCallId": "tc-a2", "title": "Run: alembic upgrade head", "kind": "execute", "status": "pending",
                         "rawInput": {"command": "alembic upgrade head"}},
            "options": [{"optionId": "allow_once", "name": "Yes", "kind": "allow_once"},
                        {"optionId": "allow_always", "name": "Always", "kind": "allow_always"},
                        {"optionId": "reject_once", "name": "No", "kind": "reject_once"}]})
        log("permission resp", resp)
        text_chunks(sid, "Migration applied.")
    elif scenario == "stream":
        run_workflow_scenario(sid)
    else:
        text_chunks(sid, "Done.")
    if PROMPT_IDS.get(sid) is not None:
        send({"jsonrpc": "2.0", "id": rid, "result": {"stopReason": "end_turn"}})


def handle(msg):
    method, rid, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
    if method is None:   # a response to one of our requests
        ent = pending.pop(rid, None)
        if ent:
            ent[1].append(msg.get("result", msg.get("error")))
            ent[0].set()
        return
    log("<-", method, json.dumps(params)[:200])
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": 1,
            "agentCapabilities": {"loadSession": True, "promptCapabilities": {"image": True}, "sessionCapabilities": {"delete": {}}},
            "agentInfo": {"name": "kiro-cli", "version": "2.31.1"}, "authMethods": []}})
    elif method == "session/new":
        sid = "sess_" + str(uuid.uuid4())
        cwd = params.get("cwd", str(HOME))
        h = "".join(f"{b:02x}" for b in cwd.encode())[:16]
        d = ROOT / h / sid
        d.mkdir(parents=True)
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        (d / "session.json").write_text(json.dumps({"id": sid, "title": "New session", "workspacePaths": [cwd], "createdAt": now, "lastModifiedAt": now, "agentMode": "kiro_default"}))
        (d / "messages.jsonl").write_text("")
        send({"jsonrpc": "2.0", "id": rid, "result": {"_meta": {"id": sid}, "configOptions": []}})
        mcp_status(sid)
        metadata(sid, 2.0)
    elif method == "session/load":
        sid = params["sessionId"]
        replay(sid)
        send({"jsonrpc": "2.0", "id": rid, "result": {"configOptions": []}})
        mcp_status(sid)
        metadata(sid, 18.6)
    elif method == "session/prompt":
        sid = params["sessionId"]
        text = " ".join(b.get("text", "") for b in params.get("prompt", []) if isinstance(b, dict))
        threading.Thread(target=run_prompt, args=(rid, sid, text), daemon=True).start()
    elif rid is not None:
        send({"jsonrpc": "2.0", "id": rid, "result": {}})


for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        msg = json.loads(line)
    except ValueError:
        continue
    try:
        handle(msg)
    except Exception as e:
        log("ERR", repr(e))
