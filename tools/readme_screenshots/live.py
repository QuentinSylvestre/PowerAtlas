import json, subprocess, time, os
ids = json.load(open("/tmp/pa-demo/ids.json"))
H = "/home/demo"
def spawn(exe, args, cwd):
    return subprocess.Popen([f"{H}/bin/{exe}", "-c", "import time;time.sleep(99999)", *args], cwd=cwd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
c = spawn("claude", ["--resume", ids["data-pipeline/0"]], f"{H}/code/data-pipeline")
x = spawn("codex", ["resume", ids["infra-terraform/0"]], f"{H}/code/infra-terraform")
time.sleep(0.5)
os.makedirs(f"{H}/.claude/sessions", exist_ok=True)
json.dump({"pid": c.pid, "sessionId": ids["data-pipeline/0"], "startedAt": int(time.time()*1000), "cwd": f"{H}/code/data-pipeline",
           "status": "busy", "kind": "interactive", "entrypoint": "cli"}, open(f"{H}/.claude/sessions/{c.pid}.json", "w"))
print(c.pid, x.pid)
import glob, datetime
now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
for f in glob.glob(f"{H}/.codex/sessions/*/*/*/*{ids['infra-terraform/0']}.jsonl"):
    with open(f, "a") as fh:
        fh.write(json.dumps({"timestamp": now, "type": "event_msg", "payload": {"type": "task_started"}}) + "\n")
        fh.write(json.dumps({"timestamp": now, "type": "event_msg", "payload": {"type": "item_completed", "item": {"type": "AgentMessage", "content": [{"type": "text", "text": "Running `terraform plan` on the split stacks to confirm there is no drift."}]}}}) + "\n")
for f in glob.glob(f"{H}/.claude/projects/*/{ids['data-pipeline/0']}.jsonl"):
    os.utime(f, None)
