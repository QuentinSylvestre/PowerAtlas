#!/usr/bin/env python3
"""Disposable ACP v3 probe harness for kiro-cli's permission surface.

Rebuilt for Phase 0 of plans/260921_ACP_PERMISSION_PROFILE_AND_LOOPBACK_CREDENTIAL.md
-- the original P1-P5 probe that produced that plan's Current State findings
lived only in a session scratchpad and was never committed. This is its
replacement, committed so Phase 0's own findings and Phase 7's live
re-verification can both run it.

It spawns ``kiro-cli acp --agent-engine v3`` directly over stdio and speaks
newline-delimited JSON-RPC 2.0 to it -- never through PowerAtlas, never
touching a live PowerAtlas process. One process instance is one disposable
ACP session: initialize, session/new (binding to the ``--agent`` modeId),
an optional single session/prompt turn, then exit. Every inbound
``session/request_permission`` is recorded in full and answered per
``--answer`` (default: reject, so nothing the agent asks about actually
runs); every inbound ``_kiro/auth/getAccessToken`` is answered by shelling
out to ``kiro-cli chat _ get-kas-token``.

Token hygiene: the OIDC access token obtained this way is kept in-process
only. It is never printed, logged, or written to a ``--json-out`` dump --
the one outbound message shape that would carry it (the getAccessToken
reply) is redacted before being recorded anywhere.

Usage:
    python tools/acp_permission_probe.py --agent kiro_default --cwd <dir>
    python tools/acp_permission_probe.py --agent pa-probe-shell-ask \\
        --cwd <dir> --prompt "Run the shell command: pwd" --answer reject \\
        --json-out out.json

Exit status is 0 whenever the harness itself completed cleanly, regardless
of what kiro-cli did or didn't prompt for -- "no prompt fired" is a
measurement, not a harness failure. A nonzero exit means the harness could
not complete the handshake or timed out; see the printed "error" field.
"""

from __future__ import annotations

import argparse
import json
import queue
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

KIRO_BINARY = "kiro-cli"
ACP_ARGS = ("acp", "--agent-engine", "v3")
PROTOCOL_VERSION = 1
DEFAULT_TIMEOUT_SECONDS = 45.0
TOKEN_FETCH_TIMEOUT_SECONDS = 15.0
_WRAPPER_SUFFIXES = frozenset({".cmd", ".bat"})
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class ProbeError(RuntimeError):
    """The harness itself failed -- a spawn, handshake, or timeout problem,
    distinct from kiro-cli refusing or prompting for something."""


def _redact_for_log(msg: dict) -> dict:
    """Copy ``msg`` with an access token blanked out, if it carries one.

    The only message shape that ever carries the OIDC token is this
    harness's own reply to ``_kiro/auth/getAccessToken`` (a
    ``result.accessToken`` field). Every other frame passes through
    unchanged. Applied to BOTH directions before anything is appended to
    the frame log, so a ``--json-out`` dump can never leak it.
    """
    result = msg.get("result")
    if isinstance(result, dict) and "accessToken" in result:
        redacted = dict(msg)
        redacted["result"] = {
            key: ("<redacted>" if key == "accessToken" else value)
            for key, value in result.items()
        }
        return redacted
    return msg


class Probe:
    """One disposable ``kiro-cli acp --agent-engine v3`` session."""

    def __init__(self, agent: str, cwd: Path, timeout: float, answer: str,
                 verbose: bool = False) -> None:
        self.agent = agent
        self.cwd = cwd
        self.timeout = timeout
        self.answer = answer  # "reject" | "allow_always" | "allow_once"
        self.verbose = verbose

        self._proc: subprocess.Popen | None = None
        self._reader: threading.Thread | None = None
        self._inbox: "queue.Queue[dict | None]" = queue.Queue()
        self._write_lock = threading.Lock()
        self._next_id = 1
        self._t0 = 0.0

        # Findings, populated as frames arrive.
        self.frames: list[dict] = []  # redacted, bidirectional, for --json-out
        self.agent_info: dict[str, Any] = {}
        self.session_id: str | None = None
        self.mode_in_effect: dict[str, Any] = {}
        self.mode_coerced: bool = False
        self.mode_catalogue: list | None = None
        self.mode_catalogue_source: str | None = None
        self.permission_requests: list[dict] = []
        self.tool_calls: list[dict] = []
        self.stop_reason: str | None = None
        self.errors: list[str] = []

    # -- process lifecycle -------------------------------------------------

    def _log(self, *parts: Any) -> None:
        if self.verbose:
            print(*parts, file=sys.stderr)

    def spawn(self) -> None:
        exe = shutil.which(KIRO_BINARY)
        if not exe:
            raise ProbeError(f"'{KIRO_BINARY}' is not on PATH -- nothing to connect to.")
        if Path(exe).suffix.lower() in _WRAPPER_SUFFIXES:
            raise ProbeError(
                f"'{exe}' is a shell wrapper; spawning it with pipes needs "
                "shell=True, which cannot hold clean stdio for JSON-RPC.")
        self.cwd.mkdir(parents=True, exist_ok=True)
        self._t0 = time.monotonic()
        try:
            self._proc = subprocess.Popen(
                [exe, *ACP_ARGS],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                cwd=str(self.cwd),
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=_CREATE_NO_WINDOW,
            )
        except OSError as exc:
            raise ProbeError(f"could not start kiro-cli: {exc}") from exc
        self._reader = threading.Thread(target=self._reader_loop, daemon=True)
        self._reader.start()

    def _reader_loop(self) -> None:
        proc = self._proc
        assert proc is not None and proc.stdout is not None
        try:
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    self._log("UNPARSEABLE LINE:", line[:300])
                    continue
                self._inbox.put(msg)
        finally:
            self._inbox.put(None)  # sentinel: stdout closed

    def kill(self) -> None:
        proc = self._proc
        if proc is not None and proc.poll() is None:
            try:
                proc.kill()
                proc.wait(timeout=5)
            except Exception:
                pass

    # -- wire I/O ------------------------------------------------------

    def _write(self, obj: dict, *, log: bool = True) -> None:
        line = json.dumps(obj, separators=(",", ":"))
        with self._write_lock:
            proc = self._proc
            if proc is None or proc.stdin is None or proc.poll() is not None:
                raise ProbeError("kiro-cli process is not running.")
            try:
                proc.stdin.write(line + "\n")
                proc.stdin.flush()
            except (OSError, ValueError) as exc:
                raise ProbeError(f"lost kiro-cli's stdin: {exc}") from exc
        if log:
            self.frames.append({
                "dir": "out",
                "t": round(time.monotonic() - self._t0, 3),
                **_redact_for_log(obj),
            })

    def _send_request(self, method: str, params: dict) -> int:
        request_id = self._next_id
        self._next_id += 1
        self._write({"jsonrpc": "2.0", "id": request_id, "method": method,
                      "params": params})
        return request_id

    # -- inbound dispatch -------------------------------------------------

    def _dispatch_one(self, msg: dict, awaited_id: int | None) -> dict | None:
        """Route one inbound message.

        Returns the message itself if it is the response to ``awaited_id``;
        otherwise handles it as a side effect (an inbound request we must
        answer, or a notification we record) and returns None.
        """
        self.frames.append({
            "dir": "in",
            "t": round(time.monotonic() - self._t0, 3),
            **_redact_for_log(msg),
        })
        is_response = "method" not in msg and "id" in msg
        if is_response:
            if awaited_id is not None and msg.get("id") == awaited_id:
                return msg
            self.errors.append(f"unmatched response id={msg.get('id')!r} "
                                f"(awaiting {awaited_id!r})")
            return None
        if "method" in msg and "id" in msg:
            self._handle_inbound_request(msg)
            return None
        if "method" in msg:
            self._handle_notification(msg)
            return None
        self.errors.append(f"unrecognized frame shape: {msg!r:.200}")
        return None

    def _handle_inbound_request(self, msg: dict) -> None:
        method = msg.get("method")
        request_id = msg.get("id")
        if method == "_kiro/auth/getAccessToken":
            self._fulfill_token(request_id)
            return
        if method == "session/request_permission":
            self._answer_permission(msg)
            return
        self._write({"jsonrpc": "2.0", "id": request_id,
                      "error": {"code": -32601, "message": f"Unsupported: {method}"}})
        self.errors.append(f"refused unsupported inbound request: {method!r}")

    def _fulfill_token(self, request_id) -> None:
        """Answer ``_kiro/auth/getAccessToken`` via ``kiro-cli chat _ get-kas-token``.

        Mirrors ``acp.py``'s ``_fulfill_token`` reply shape exactly: the four
        fields nested under the subprocess's own ``["data"]`` key, forwarded
        flat under ``result``. R8-equivalent token hygiene: on failure the
        exception is never included in the response or in a logged frame.
        """
        exe = shutil.which(KIRO_BINARY)
        if exe is None:
            response = {"jsonrpc": "2.0", "id": request_id,
                        "error": {"code": -32000, "message": "kiro-cli binary not found"}}
            self._write(response)
            self.errors.append("token fetch failed: kiro-cli not on PATH")
            return
        try:
            proc_result = subprocess.run(
                [exe, "chat", "_", "get-kas-token"],
                capture_output=True, text=True, timeout=TOKEN_FETCH_TIMEOUT_SECONDS,
                creationflags=_CREATE_NO_WINDOW,
            )
            if proc_result.returncode != 0:
                raise RuntimeError(f"get-kas-token exited {proc_result.returncode}")
            token_data = json.loads(proc_result.stdout)["data"]
            response = {
                "jsonrpc": "2.0", "id": request_id,
                "result": {
                    "accessToken": token_data["accessToken"],
                    "expiresAt": token_data["expiresAt"],
                    "profileArn": token_data.get("profileArn", ""),
                    "provider": token_data.get("provider", ""),
                },
            }
        except Exception as exc:
            # Deliberately no exception detail in the response or the error
            # log -- get-kas-token's stdout/stderr may carry partial token
            # material (same rationale as acp.py's _fulfill_token, R8).
            response = {"jsonrpc": "2.0", "id": request_id,
                        "error": {"code": -32000, "message": "token fetch failed"}}
            self.errors.append("token fetch failed (see stderr with --verbose)")
            self._log("token fetch error:", type(exc).__name__)
        self._write(response)  # _write applies _redact_for_log before recording

    def _answer_permission(self, msg: dict) -> None:
        request_id = msg.get("id")
        params = msg.get("params") or {}
        options = params.get("options") or []
        chosen = self._pick_option(options)
        if chosen is None:
            params = dict(params)
            params["answered_with"] = None
            self.permission_requests.append(params)
            self._write({"jsonrpc": "2.0", "id": request_id,
                        "error": {"code": -32000, "message": "no matching option"}})
            self.errors.append(
                f"permission request had no options at all (id={request_id})")
            return
        chosen_kind = next(
            (opt.get("kind") for opt in options if opt.get("optionId") == chosen), None)
        params = dict(params)
        params["answered_with"] = {"optionId": chosen, "kind": chosen_kind}
        self.permission_requests.append(params)
        self._write({"jsonrpc": "2.0", "id": request_id,
                    "result": {"optionId": chosen}})

    def _pick_option(self, options: list[dict]) -> str | None:
        # Exact match first: a substring match here is a real trap -- "always"
        # matches BOTH "allow_always" and "reject_always", so a naive
        # substring-only lookup for --answer allow_always could silently
        # select "Always deny" instead (found via advisor review before this
        # was ever used for the step-8 always-allow measurement).
        exact = {
            "reject": "reject_once",
            "allow_always": "allow_always",
            "allow_once": "allow_once",
        }[self.answer]
        for opt in options:
            if str(opt.get("kind", "")) == exact:
                return opt.get("optionId")
        # Fall back to a substring match among options whose kind starts with
        # the expected verb (allow_/reject_), never a bare "always" substring.
        verb = "allow" if self.answer.startswith("allow") else "reject"
        for opt in options:
            kind = str(opt.get("kind", "")).lower()
            if kind.startswith(verb):
                return opt.get("optionId")
        if options:
            self.errors.append(
                f"no option kind matched {self.answer!r} (wanted {exact!r}) among "
                f"{[opt.get('kind') for opt in options]!r}; used the first "
                "option instead so the turn does not hang")
            return options[0].get("optionId")
        return None

    def _handle_notification(self, msg: dict) -> None:
        method = msg.get("method")
        params = msg.get("params") or {}
        if method != "session/update":
            return
        update = params.get("update") or {}
        kind = update.get("sessionUpdate")
        if kind == "config_option_update":
            for opt in update.get("configOptions") or []:
                if opt.get("id") == "mode":
                    self.mode_catalogue = opt.get("options")
                    self.mode_catalogue_source = "config_option_update"
        elif kind in ("tool_call", "tool_call_update"):
            self.tool_calls.append({"kind": kind, "update": update})

    # -- top-level ACP calls ------------------------------------------------

    def _await_response(self, request_id: int, timeout: float) -> dict:
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ProbeError(
                    f"timed out after {timeout:.0f}s waiting for response to "
                    f"request {request_id}")
            try:
                msg = self._inbox.get(timeout=remaining)
            except queue.Empty:
                raise ProbeError(
                    f"timed out after {timeout:.0f}s waiting for response to "
                    f"request {request_id}")
            if msg is None:
                raise ProbeError("kiro-cli exited before answering.")
            result = self._dispatch_one(msg, request_id)
            if result is not None:
                return result

    def drain(self, seconds: float) -> None:
        """Dispatch stray frames for a grace period with no outstanding
        request of our own -- catches a late permission_request or
        config_option_update that arrives after the call that provoked it
        already returned."""
        deadline = time.monotonic() + seconds
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            try:
                msg = self._inbox.get(timeout=remaining)
            except queue.Empty:
                return
            if msg is None:
                return
            self._dispatch_one(msg, None)

    def initialize(self) -> dict:
        request_id = self._send_request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "clientCapabilities": {
                "fs": {"readTextFile": False, "writeTextFile": False},
                "terminal": False,
            },
        })
        resp = self._await_response(request_id, self.timeout)
        result = resp.get("result") or {}
        self.agent_info = result.get("agentInfo") or {}
        return result

    def new_session(self) -> dict:
        params = {
            "cwd": str(self.cwd),
            "mcpServers": [],
            "_meta": {"kiro": {"modeId": self.agent, "steering": []}},
        }
        request_id = self._send_request("session/new", params)
        resp = self._await_response(request_id, self.timeout)
        result = resp.get("result") or {}
        self.session_id = (result.get("_meta") or {}).get("id")
        self.mode_in_effect = {
            "_meta.agentMode": (result.get("_meta") or {}).get("agentMode"),
            "modes.currentModeId": (result.get("modes") or {}).get("currentModeId"),
        }
        # R-2 (silent coercion to "vibe"): make a mismatch between what was
        # requested and what kiro-cli actually bound impossible to miss.
        # This is exactly the class of divergence an operator scanning
        # printed JSON by eye can skim past -- do not rely on that.
        effective_values = {v for v in self.mode_in_effect.values() if v is not None}
        self.mode_coerced = bool(effective_values) and self.agent not in effective_values
        if self.mode_coerced:
            # Unconditional, not gated on --verbose: Phase 7 exists to catch
            # exactly this, and it must not depend on the operator having
            # remembered a flag.
            print(f"*** MODE COERCION: requested agent={self.agent!r} but "
                  f"kiro-cli reports {self.mode_in_effect!r} ***", file=sys.stderr)
            self.errors.append(
                f"mode coercion: requested {self.agent!r}, kiro-cli reports "
                f"{self.mode_in_effect!r}")
        if not self.mode_catalogue:
            modes = result.get("modes") or {}
            if modes.get("availableModes"):
                self.mode_catalogue = modes["availableModes"]
                self.mode_catalogue_source = "session/new result.modes.availableModes"
        return result

    def prompt(self, text: str) -> dict:
        if not self.session_id:
            raise ProbeError("no session -- call new_session() first")
        request_id = self._send_request("session/prompt", {
            "sessionId": self.session_id,
            "prompt": [{"type": "text", "text": text}],
        })
        resp = self._await_response(request_id, self.timeout)
        result = resp.get("result") or {}
        self.stop_reason = result.get("stopReason")
        return result

    def summary(self) -> dict:
        return {
            "agent": self.agent,
            "cwd": str(self.cwd),
            "agent_info": self.agent_info,
            "session_id": self.session_id,
            "mode_in_effect": self.mode_in_effect,
            "mode_coerced": self.mode_coerced,
            "mode_catalogue_source": self.mode_catalogue_source,
            "mode_catalogue": self.mode_catalogue,
            "stop_reason": self.stop_reason,
            "permission_requests": self.permission_requests,
            "tool_calls": self.tool_calls,
            "errors": self.errors,
        }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--agent", required=True,
                         help="modeId / agent name to bind session/new to "
                              "(a built-in task mode, or a custom agent file "
                              "name under ~/.kiro/agents/)")
    parser.add_argument("--cwd", required=True,
                         help="probe working directory (created if missing)")
    parser.add_argument("--prompt", default=None,
                         help="if given, run one session/prompt turn with this text; "
                              "if omitted, the probe stops after session/new + drain "
                              "(mode-catalogue-only run)")
    parser.add_argument("--answer", choices=["reject", "allow_always", "allow_once"],
                         default="reject",
                         help="which option kind to pick for every "
                              "session/request_permission (default: reject, so "
                              "nothing the agent asks about actually runs)")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--drain", type=float, default=2.0,
                         help="grace period in seconds to catch stray frames "
                              "after the main call returns")
    parser.add_argument("--json-out", default=None,
                         help="write the full redacted bidirectional frame log here")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    probe = Probe(agent=args.agent, cwd=Path(args.cwd), timeout=args.timeout,
                  answer=args.answer, verbose=args.verbose)
    exit_code = 0
    try:
        probe.spawn()
        probe.initialize()
        probe.new_session()
        if args.prompt:
            probe.prompt(args.prompt)
        probe.drain(args.drain)
    except ProbeError as exc:
        exit_code = 1
        print(json.dumps({"error": str(exc), **probe.summary()}, indent=2))
    else:
        print(json.dumps(probe.summary(), indent=2))
    finally:
        probe.kill()

    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps({"summary": probe.summary(), "frames": probe.frames}, indent=2),
            encoding="utf-8")

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
