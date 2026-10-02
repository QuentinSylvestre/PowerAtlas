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


class TestCodexToolNames:
    """261001_CODEX_BUILT_IN_PROVIDER_SESSIONS_LIVE_DOT_AND_OVERVIEW Phase 1, D14."""

    @pytest.mark.parametrize("name, kind, title", [
        ("shell_command", "execute", "Run command"),
        ("exec", "execute", "Run command"),
        ("apply_patch", "edit", "Apply patch"),
        ("view_image", "read", "View image"),
    ])
    def test_codex_tool_names_have_their_own_kind_and_title(self, name, kind, title):
        got_title, got_kind, _ = tt._derive_tool_display(name, {})
        assert (got_kind, got_title) == (kind, title)

    def test_a_shell_command_call_shows_its_command(self):
        frames = tt.translate_transcript([TranscriptEvent(
            kind="tool_call", tool_call_id="c1", tool_name="shell_command",
            tool_args={"command": "git status", "workdir": "C:\\w"})], "s")
        assert frames[0]["payload"]["command"] == "git status"
        assert frames[0]["payload"]["kind"] == "execute"

    def test_an_unlisted_codex_tool_still_falls_back_safely(self):
        _, kind, _ = tt._derive_tool_display("spawn_agent", {"task": "x"})
        assert kind == "other"


class TestOutcomeUnknownResults:
    """A result flagged `outcome_unknown` (only the Codex adapter sets it) is shown
    as finished, a neutral status that claims neither success nor failure (plan D13
    as amended 2026-10-01); every other unknown result behaves exactly as before."""

    @staticmethod
    def _frames(result):
        return tt.translate_transcript([
            TranscriptEvent(kind="tool_call", tool_call_id="c1", tool_name="exec", tool_args={}),
            result], "s")

    def test_flagged_unknown_outcome_is_finished_never_completed(self):
        frames = self._frames(TranscriptEvent(
            kind="tool_result", tool_call_id="c1", success=None, outcome_unknown=True))
        assert [f["type"] for f in frames] == ["tool_call", "tool_update"]
        assert frames[1]["payload"] == {"toolCallId": "c1", "status": "finished"}

    def test_unflagged_unknown_outcome_stays_started(self):
        frames = self._frames(TranscriptEvent(kind="tool_result", tool_call_id="c1", success=None))
        assert [f["type"] for f in frames] == ["tool_call"]

    def test_a_known_outcome_wins_over_the_flag(self):
        failed = self._frames(TranscriptEvent(
            kind="tool_result", tool_call_id="c1", success=False, outcome_unknown=True))
        assert failed[1]["payload"]["status"] == "failed"
        ok = self._frames(TranscriptEvent(
            kind="tool_result", tool_call_id="c1", success=True, outcome_unknown=True))
        assert ok[1]["payload"]["status"] == "completed"

    def test_flagged_result_without_its_call_is_still_dropped(self):
        assert tt.translate_transcript([TranscriptEvent(
            kind="tool_result", tool_call_id="orphan", success=None, outcome_unknown=True)], "s") == []

    def test_only_a_flag_can_change_the_outcome_a_transcript_event_defaults_to(self):
        assert TranscriptEvent(kind="tool_result").outcome_unknown is False


class TestMarkdownWorkIsBounded:
    """Final review finding B2 (261001_CODEX_BUILT_IN_PROVIDER_SESSIONS_LIVE_DOT_AND_OVERVIEW).
    mistune's inline parser is quadratic on `"[a](" * n` (8 KiB 1.0 s, 32 KiB 15.9 s, measured), so
    one hostile message in a rollout cost minutes of CPU in a worker thread. The limits degrade to
    the `chunk` frame (or one plain `text` token), never to a missing message."""

    @staticmethod
    def _rendered(frames):
        return [f for f in frames if f["type"] == "rendered"]

    def test_a_hostile_256_kib_message_is_translated_within_seconds_and_keeps_its_text(self):
        import time
        text = "[a](" * (256 * 1024 // 4)
        started = time.perf_counter()
        frames = tt.translate_transcript([TranscriptEvent(kind="assistant", text=text)], "s")
        elapsed = time.perf_counter() - started
        # Unbounded this is about 20 minutes; bounded it is milliseconds. 10 s is the generous bound.
        assert elapsed < 10.0, elapsed
        assert [f["payload"]["text"] for f in frames if f["type"] == "chunk"] == [text]
        assert self._rendered(frames) == [], "over the per-message limit: plain text only"

    def test_many_hostile_paragraphs_under_the_size_limits_stop_at_the_time_budget(self, monkeypatch):
        """Each paragraph (7 KiB) and each message (about 63 KiB) is inside its limit, so only the
        time budget bounds the call: 80 such messages took minutes before."""
        import time
        monkeypatch.setattr(tt, "MD_BUDGET_SECONDS", 1.0)
        para = "[a](" * 1792                           # 7 KiB, about 0.7 s to parse unbounded
        text = (para + "\n\n") * 9
        events = [TranscriptEvent(kind="assistant", text=text) for _ in range(80)]
        started = time.perf_counter()
        frames = tt.translate_transcript(events, "s")
        elapsed = time.perf_counter() - started
        assert elapsed < tt.MD_BUDGET_SECONDS + 4.0, elapsed   # the budget plus one 7 KiB paragraph
        assert len([f for f in frames if f["type"] == "chunk"]) == 80, "no message is lost"

    def test_after_the_deadline_no_message_is_parsed_and_every_message_keeps_its_chunk(self, monkeypatch):
        now = [100.0]
        monkeypatch.setattr(tt, "_clock", lambda: now[0])
        real = tt._get_markdown_parser()
        parsed = []

        def spy(text):
            parsed.append(text)
            now[0] += 1.0                               # each parse takes one "second"
            return real(text)
        monkeypatch.setattr(tt, "_get_markdown_parser", lambda: spy)
        monkeypatch.setattr(tt, "MD_BUDGET_SECONDS", 3.0)
        events = [TranscriptEvent(kind="assistant", text=f"m{i}") for i in range(6)]
        frames = tt.translate_transcript(events, "s")
        assert parsed == ["m0", "m1", "m2"], "the clock reaches the deadline after three parses"
        assert [f["payload"]["text"] for f in frames if f["type"] == "chunk"] == [f"m{i}" for i in range(6)]
        assert len(self._rendered(frames)) == 3

    def test_a_paragraph_over_the_inline_limit_is_plain_text_and_its_neighbours_still_render(self):
        big = "word " * 2000 + "*marked*"                      # over MD_MAX_INLINE_CHARS, and it has markup
        assert len(big) > tt.MD_MAX_INLINE_CHARS
        text = "# Title\n\n" + big.strip() + "\n\n- one\n- two\n"
        frames = tt.translate_transcript([TranscriptEvent(kind="assistant", text=text)], "s")
        types = [t["type"] for t in self._rendered(frames)[0]["payload"]["tokens"] if t["type"] != "blank_line"]
        assert types == ["heading", "paragraph", "list"]
        paragraph = self._rendered(frames)[0]["payload"]["tokens"][2]
        assert paragraph["children"] == [{"type": "text", "raw": big.strip()}]
        # And the same paragraph one character under the limit is parsed as markdown.
        edge = "*" + "a" * (tt.MD_MAX_INLINE_CHARS - 3) + "*"
        assert len(edge) == tt.MD_MAX_INLINE_CHARS - 1
        frames = tt.translate_transcript([TranscriptEvent(kind="assistant", text=edge)], "s")
        assert self._rendered(frames)[0]["payload"]["tokens"][0]["children"][0]["type"] == "emphasis"

    def test_a_normal_long_message_renders_exactly_as_unbounded_mistune_does(self):
        """Behaviour for normal sizes is unchanged: the tokens equal what the plain parser gives
        for a 50 KB message with headings, lists, a table, code and a 7.9 KB paragraph."""
        section = ("## Section\n\nSome *text* with `code` and [a link](http://x.y/z).\n\n"
                   "- a\n- b\n  - c\n\n| h1 | h2 |\n|----|----|\n| 1 | 2 |\n\n"
                   "```python\nprint(1)\n```\n\n")
        text = (section * 400)[:40_000] + "\n\n" + "long " * 1580 + "\n"
        assert 45_000 < len(text) <= tt.MD_MAX_MESSAGE_CHARS
        frames = tt.translate_transcript([TranscriptEvent(kind="assistant", text=text)], "s")
        assert self._rendered(frames)[0]["payload"]["tokens"] == tt._get_markdown_parser()(text)

    def test_the_message_size_limit_has_its_boundary_at_exactly_the_constant(self, monkeypatch):
        monkeypatch.setattr(tt, "MD_MAX_MESSAGE_CHARS", 100)
        at = TranscriptEvent(kind="user", text="a" * 100)
        over = TranscriptEvent(kind="user", text="a" * 101)
        assert len(self._rendered(tt.translate_transcript([at], "s"))) == 1
        assert self._rendered(tt.translate_transcript([over], "s")) == []
        assert len(tt.translate_transcript([over], "s")) == 1, "the chunk is still sent"

    def test_the_rendered_total_holds_its_cap_and_later_messages_keep_their_chunks(self, monkeypatch):
        import json
        event = TranscriptEvent(kind="assistant", text="**bold** text")
        one = len(json.dumps(tt._get_markdown_parser()(event.text)))
        for cap, kept in ((2 * one, 2), (2 * one - 1, 1), (one - 1, 0), (10 * one, 5)):
            monkeypatch.setattr(tt, "MD_MAX_RENDERED_BYTES", cap)
            frames = tt.translate_transcript([event] * 5, "s")
            rendered = self._rendered(frames)
            assert len(rendered) == kept, (cap, len(rendered))
            assert sum(len(json.dumps(f["payload"]["tokens"])) for f in rendered) <= cap
            assert len([f for f in frames if f["type"] == "chunk"]) == 5

    def test_once_the_rendered_total_is_full_no_later_message_is_parsed(self, monkeypatch):
        import json
        real = tt._get_markdown_parser()
        parsed = []
        monkeypatch.setattr(tt, "_get_markdown_parser", lambda: lambda text: parsed.append(text) or real(text))
        monkeypatch.setattr(tt, "MD_MAX_RENDERED_BYTES", len(json.dumps(real("**a**"))))
        frames = tt.translate_transcript(
            [TranscriptEvent(kind="user", text="**a**")] * 5, "s")
        assert len(parsed) == 2, "the first fits, the second overflows and stops the rest"
        assert len(self._rendered(frames)) == 1 and len(frames) == 6

    def test_the_tool_frames_are_not_counted_in_the_markdown_limits(self, monkeypatch):
        monkeypatch.setattr(tt, "MD_MAX_RENDERED_BYTES", 1)
        events = [TranscriptEvent(kind="assistant", text="x"),
                  TranscriptEvent(kind="tool_call", tool_call_id="c1", tool_name="shell_command",
                                  tool_args={"command": "ls"}),
                  TranscriptEvent(kind="tool_result", tool_call_id="c1", success=True)]
        assert [f["type"] for f in tt.translate_transcript(events, "s")] == [
            "chunk", "tool_call", "tool_update"]
