"""Codex adapter: rollout JSONL to canonical (PLAN 7.2)."""

import pytest
from conftest import fixture_lines

from oracle_gateway.adapters import get_adapter
from oracle_gateway.adapters.base import AdapterError

LINES = fixture_lines("codex_sample.jsonl")


def parse():
    return get_adapter("codex").parse(LINES)


def by_id(batch, external_id):
    return next(e for e in batch.events if e.external_id == external_id)


def test_session_header_comes_from_the_session_meta_line():
    session = parse().session
    assert session.external_id == "01a08d2b-360b-7fa0-acb6-4a6b1213d7a8"
    assert session.agent_kind == "codex"
    assert session.workspace == "/Users/demo/dev/ledger"
    assert session.agent_version == "0.153.4"
    assert session.started_at.isoformat() == "2026-09-10T21:13:34.493000+00:00"
    assert session.metadata["originator"] == "Codex Desktop"


def test_only_response_items_become_events():
    batch = parse()
    # 8 response_item lines; session_meta + 2 event_msg + turn_context + world_state
    # + token_usage_record = 6 skipped
    assert len(batch.events) == 8
    assert batch.skipped == 6


def test_seq_follows_the_rollout_ordinal():
    assert by_id(parse(), "msg_user_1").seq == 5


def test_user_and_assistant_messages_map_to_text_blocks():
    user = by_id(parse(), "msg_user_1")
    assert user.type == "user_message"
    assert user.content == [{"type": "text", "text": "Reconcile the double-entry totals in ledger.py"}]
    assistant = by_id(parse(), "msg_asst_1")
    assert assistant.type == "assistant_message"
    assert assistant.role == "assistant"


def test_developer_message_is_typed_system():
    assert by_id(parse(), "msg_dev_1").type == "system"


def test_reasoning_becomes_a_thinking_block_from_the_summary():
    event = by_id(parse(), "rs_1")
    assert event.type == "thinking"
    assert event.content == [
        {"type": "thinking", "thinking": "Check how totals are accumulated before editing."}
    ]


def test_custom_tool_call_parses_its_json_input():
    event = by_id(parse(), "ct_1")
    assert event.type == "tool_call"
    block = event.content[0]
    assert block["type"] == "tool_use"
    assert block["id"] == "call_abc"
    assert block["name"] == "shell"
    assert block["input"]["command"][0] == "bash"


def test_function_call_parses_its_arguments():
    block = by_id(parse(), "fc_1").content[0]
    assert block["name"] == "apply_patch"
    assert block["input"]["path"] == "ledger.py"


def test_tool_outputs_reference_the_call_id_and_get_a_derived_event_id():
    event = by_id(parse(), "01a08d2b-360b-7fa0-acb6-4a6b1213d7a8:8")
    assert event.type == "tool_result"
    assert event.role == "tool"
    assert event.content[0]["tool_use_id"] == "call_abc"
    assert "def total" in event.text


def test_model_and_turn_id_are_carried_forward_from_context_lines():
    event = by_id(parse(), "msg_asst_1")
    assert event.model == "gpt-5.6-luna"
    assert event.metadata["turn_id"] == "turn-1"


def test_missing_session_meta_is_a_client_error_not_a_crash():
    with pytest.raises(AdapterError):
        get_adapter("codex").parse([LINES[5]])


def test_unparseable_lines_are_counted_never_raised():
    batch = get_adapter("codex").parse(LINES + ["{ broken"])
    assert batch.skipped == 7


def test_title_comes_from_the_first_user_prompt_since_codex_has_no_title():
    assert parse().session.title == "Reconcile the double-entry totals in ledger.py"


def test_developer_context_is_not_mistaken_for_the_title():
    assert "app-context" not in (parse().session.title or "")


def test_harness_injected_context_never_becomes_the_title():
    """Codex injects <recommended_plugins> and friends as user-role messages."""
    injected = (
        '{"timestamp":"2026-09-10T21:13:41.0Z","ordinal":4,"type":"response_item",'
        '"payload":{"type":"message","id":"msg_inj","role":"user","content":'
        '[{"type":"input_text","text":"<recommended_plugins>\\nAirtable, Alpaca'
        '\\n</recommended_plugins>"}]}}'
    )
    batch = get_adapter("codex").parse(LINES[:5] + [injected] + LINES[5:])
    assert batch.session.title == "Reconcile the double-entry totals in ledger.py"
    # ...but it is still stored as an event.
    assert by_id(batch, "msg_inj").type == "user_message"


def test_a_prompt_that_merely_mentions_a_tag_is_still_a_title():
    prompt = (
        '{"timestamp":"2026-09-10T21:13:42.0Z","ordinal":4,"type":"response_item",'
        '"payload":{"type":"message","id":"msg_real","role":"user","content":'
        '[{"type":"input_text","text":"<div> is not rendering, any idea why?"}]}}'
    )
    batch = get_adapter("codex").parse(LINES[:5] + [prompt] + LINES[5:])
    assert batch.session.title == "<div> is not rendering, any idea why?"


def test_a_long_injected_block_is_still_recognised_after_truncation():
    """The chrome check must run on the full text, not the 120-char title slice."""
    filler = "- Airtable (airtable@openai-curated-remote)\\n" * 8
    injected = (
        '{"timestamp":"2026-09-10T21:13:41.0Z","ordinal":4,"type":"response_item",'
        '"payload":{"type":"message","id":"msg_long","role":"user","content":'
        '[{"type":"input_text","text":"<recommended_plugins>\\n' + filler +
        '</recommended_plugins>"}]}}'
    )
    batch = get_adapter("codex").parse(LINES[:5] + [injected] + LINES[5:])
    assert batch.session.title == "Reconcile the double-entry totals in ledger.py"
