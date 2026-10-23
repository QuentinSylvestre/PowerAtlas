import json, glob, os, datetime
ids = json.load(open("/tmp/pa-demo/ids.json")); H = "/home/demo"
now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
for f in glob.glob(f"{H}/.codex/sessions/*/*/*/*{ids['infra-terraform/0']}.jsonl"):
    open(f, "a").write(json.dumps({"timestamp": now, "type": "event_msg", "payload": {"type": "task_started"}}) + "\n")
for f in glob.glob(f"{H}/.claude/projects/*/{ids['data-pipeline/0']}.jsonl"):
    os.utime(f, None)
