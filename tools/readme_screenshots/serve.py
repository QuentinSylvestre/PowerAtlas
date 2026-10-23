"""Serve PowerAtlas from the fake home for screenshots. Stubs the pywin32 job-object modules so the ACP
supervisor will spawn the stand-in agent on Linux. Screenshot tooling only; never use for real sessions."""
import os, sys, asyncio
from unittest.mock import MagicMock
for _m in ('win32api','win32con','win32job'):
    sys.modules[_m] = MagicMock()
import uvicorn
from power_atlas import web as w, config as c
from power_atlas.web import acp as acp_module, app
cfg = c.load_config()
if acp_module is not None:
    acp_module.apply_config(cfg)
w.set_startup_config(cfg)
from power_atlas import agent_profile
port = int(os.environ.get("PA_PORT", "4915"))
agent_profile.set_listen_port(port)
uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning", ws_max_size=16*1024*1024, proxy_headers=False)
