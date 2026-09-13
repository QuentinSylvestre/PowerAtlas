"""Tests for transcript_translator.py: converts a provider's full transcript
(data.TranscriptEvent list) into the ACP wire protocol's own frame shapes,
so the dashboard's static panel and the live ACP page can share one
renderer (dashboard/ACP-merge plan, Phase 1c/2)."""

import pytest

from power_atlas import transcript_translator as tt
from power_atlas.data import TranscriptEvent


class TestTranslateTranscriptTextEvents:
    def test_empty_events_yields_no_frames(self):
        assert tt.translate_transcript([], "sess1") == []

    def test_user_event_emits_chunk_then_rendered(self):
        frames = tt.translate_transcript(
            [TranscriptEvent(kind="user", text="hello world")], "sess1")
        assert [f["type"] for f in frames] == ["chunk", "rendered"]
        assert frames[0]["payload"] == {"role": "user", "text": "hello world"}
        assert frames[0]["sessionId"] == "sess1"
        assert isinstance(frames[1]["payload"]["tokens"], list)
        assert frames[1]["payload"]["tokens"]

    def test_assistant_event_uses_agent_role(self):
        frames = tt.translate_transcript(
            [TranscriptEvent(kind="assistant", text="hi there")], "sess1")
        assert frames[0]["type"] == "chunk"
        assert frames[0]["payload"]["role"] == "agent"

    def test_empty_text_event_emits_nothing(self):
        assert tt.translate_transcript([TranscriptEvent(kind="user", text="")], "sess1") == []

    def test_session_id_threaded_through_every_frame(self):
        frames = tt.translate_transcript(
            [TranscriptEvent(kind="user", text="q")], "sess-xyz")
        assert all(f["sessionId"] == "sess-xyz" for f in frames)

    def test_markdown_unavailable_degrades_to_chunk_only(self, monkeypatch):
        """A rendering is an upgrade, never load-bearing (mirrors acp.py's
        own _close_bubble rule) -- if mistune can't be used, the chunk frame
        alone still carries the text."""
        monkeypatch.setattr(tt, "_get_markdown_parser", lambda: None)
        frames = tt.translate_transcript(
            [TranscriptEvent(kind="user", text="hello")], "sess1")
        assert [f["type"] for f in frames] == ["chunk"]

    def test_markdown_parse_failure_degrades_to_chunk_only(self, monkeypatch):
        def _boom(text):
            raise RuntimeError("boom")
        monkeypatch.setattr(tt, "_get_markdown_parser", lambda: _boom)
        frames = tt.translate_transcript(
            [TranscriptEvent(kind="user", text="hello")], "sess1")
        assert [f["type"] for f in frames] == ["chunk"]

    def test_markdown_returning_non_list_degrades_to_chunk_only(self, monkeypatch):
        monkeypatch.setattr(tt, "_get_markdown_parser", lambda: (lambda text: None))
        frames = tt.translate_transcript(
            [TranscriptEvent(kind="user", text="hello")], "sess1")
        assert [f["type"] for f in frames] == ["chunk"]

    def test_multiple_turns_preserve_order(self):
        events = [
            TranscriptEvent(kind="user", text="q1"),
            TranscriptEvent(kind="assistant", text="a1"),
        ]
        frames = tt.translate_transcript(events, "sess1")
        chunk_frames = [f for f in frames if f["type"] == "chunk"]
        assert [f["payload"]["text"] for f in chunk_frames] == ["q1", "a1"]


class TestTranslateTranscriptToolEvents:
    def test_tool_call_emits_started_status(self):
        events = [TranscriptEvent(kind="tool_call", tool_call_id="tc1",
                                   tool_name="fs_write", tool_args={"path": "a.py"})]
        frames = tt.translate_transcript(events, "sess1")
        assert len(frames) == 1
        assert frames[0]["type"] == "tool_call"
        assert frames[0]["payload"]["toolCallId"] == "tc1"
        assert frames[0]["payload"]["status"] == "started"
        assert frames[0]["payload"]["kind"] == "edit"
        assert frames[0]["payload"]["title"] == "Write file"
        assert frames[0]["payload"]["command"] == "a.py"

    def test_tool_call_without_id_is_dropped(self):
        events = [TranscriptEvent(kind="tool_call", tool_call_id="",
                                   tool_name="fs_write", tool_args={})]
        assert tt.translate_transcript(events, "sess1") == []

    def test_successful_result_emits_tool_update_completed(self):
        events = [
            TranscriptEvent(kind="tool_call", tool_call_id="tc1", tool_name="shell", tool_args={"command": "ls"}),
            TranscriptEvent(kind="tool_result", tool_call_id="tc1", success=True),
        ]
        frames = tt.translate_transcript(events, "sess1")
        assert [f["type"] for f in frames] == ["tool_call", "tool_update"]
        assert frames[1]["payload"] == {"toolCallId": "tc1", "status": "completed"}

    def test_failed_result_emits_tool_update_failed(self):
        events = [
            TranscriptEvent(kind="tool_call", tool_call_id="tc1", tool_name="shell", tool_args={}),
            TranscriptEvent(kind="tool_result", tool_call_id="tc1", success=False),
        ]
        frames = tt.translate_transcript(events, "sess1")
        assert frames[1]["payload"]["status"] == "failed"

    def test_result_with_unknown_success_is_dropped_call_stays_started(self):
        """The call was seen but its outcome wasn't -- leave it at "started"
        rather than mislabel it, the same as a live call this client never
        saw resolve."""
        events = [
            TranscriptEvent(kind="tool_call", tool_call_id="tc1", tool_name="shell", tool_args={}),
            TranscriptEvent(kind="tool_result", tool_call_id="tc1", success=None),
        ]
        frames = tt.translate_transcript(events, "sess1")
        assert [f["type"] for f in frames] == ["tool_call"]

    def test_result_with_no_matching_call_is_dropped(self):
        events = [TranscriptEvent(kind="tool_result", tool_call_id="orphan", success=True)]
        assert tt.translate_transcript(events, "sess1") == []

    def test_unknown_tool_name_falls_back_to_other_kind_and_titlecased_name(self):
        events = [TranscriptEvent(kind="tool_call", tool_call_id="tc1",
                                   tool_name="frobnicate_widget", tool_args={})]
        frames = tt.translate_transcript(events, "sess1")
        assert frames[0]["payload"]["kind"] == "other"
        assert frames[0]["payload"]["title"] == "Frobnicate Widget"


class TestDeriveToolDisplay:
    def test_command_key_priority_order(self):
        _, _, command = tt._derive_tool_display("shell", {
            "path": "ignored.py", "command": "echo hi",
        })
        assert command == "echo hi"

    def test_falls_back_to_json_when_no_priority_key_present(self):
        import json
        _, _, command = tt._derive_tool_display("unknown_tool", {"foo": "bar"})
        assert json.loads(command) == {"foo": "bar"}

    def test_empty_args_falls_back_to_json_dump_like_no_priority_key(self):
        """Matches acp.py's own _tool_input_text: nothing named is absent, not
        "we have no idea what ran" -- so it serializes what's there, {}."""
        _, _, command = tt._derive_tool_display("shell", {})
        assert command == "{}"

    def test_non_dict_args_yields_empty_command(self):
        _, _, command = tt._derive_tool_display("shell", None)
        assert command == ""

    def test_known_kind_vocabulary_matches_acp_html(self):
        """These must stay inside the same fixed set acp.html's
        TOOL_KIND_ICON recognizes, or a translated call's icon silently
        fails to resolve on the client."""
        known_kinds = {"read", "edit", "delete", "move", "search", "execute",
                        "think", "fetch", "switch_mode", "other"}
        for tool_name in tt._TOOL_KIND_BY_NAME:
            _, kind, _ = tt._derive_tool_display(tool_name, {})
            assert kind in known_kinds
