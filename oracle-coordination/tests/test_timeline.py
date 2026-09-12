import pytest

from oracle.demo import run_demo
from oracle.timeline import build_timeline, render_text

KINDS = {
    "register", "report", "checkpoint", "heartbeat", "classification", "question_opened", "conflict_confirmed",
    "command", "pause_ack", "model", "model_failed", "proposal", "response", "round_closed", "decision",
    "arbitration", "decision_ack", "resume_request", "gate", "setup", "curate", "overlap", "other",
}


def test_missing_database_is_waiting(tmp_path):
    timeline = build_timeline(tmp_path / "missing.db")
    assert timeline == {"status": "waiting", "steps": [], "participants": []}
    assert isinstance(render_text(timeline), str)


@pytest.mark.asyncio
async def test_demo_timeline(tmp_path):
    result = await run_demo(tmp_path / "demo")
    timeline = build_timeline(result["database"])
    assert timeline["status"] == "ok"
    lanes = [p["lane"] for p in timeline["participants"]]
    assert lanes == ["alice", "bob", "carol", "owner", "oracle", "model"]
    kinds = {p["lane"]: p["kind"] for p in timeline["participants"]}
    assert kinds["alice"] == "human" and kinds["owner"] == "owner"
    assert kinds["oracle"] == "oracle" and kinds["model"] == "model"
    assert next(p for p in timeline["participants"] if p["lane"] == "alice")["project"] == "backend"

    steps = timeline["steps"]
    assert steps and steps[0]["kind"] in {"setup", "register"}
    assert all(s["kind"] in KINDS for s in steps)
    assert all(len(s["title"]) <= 120 for s in steps)
    assert [s["seq"] for s in steps] == sorted(s["seq"] for s in steps)
    assert not any(s["title"].endswith("negotiation_response") for s in steps)

    question = next(s for s in steps if s["kind"] == "question_opened")
    assert "User.id.type" in question["title"]
    qid = question["refs"]["question_id"]
    pauses = [s for s in steps if s["kind"] == "command" and s["payload"]["type"] == "PAUSE"]
    pauses_for_question = [s for s in pauses if s["refs"].get("question_id") == qid]
    assert pauses_for_question and all(s["seq"] > question["seq"] for s in pauses_for_question)
    assert all("PAUSE" in s["title"] and "safe checkpoint" in s["title"] for s in pauses)

    commands = [s for s in steps if s["kind"] == "command"]
    assert commands and all(s["target"] in {"alice", "bob", "carol"} for s in commands)
    assert all(s["lane"] == "oracle" for s in commands)
    final = [s for s in commands if s["payload"]["type"] == "FINAL_DECISION"]
    assert final and all(s["detail"].startswith("Decision d-") for s in final)
    negotiation = [s for s in commands if s["payload"]["type"] == "NEGOTIATION_MESSAGE"]
    assert negotiation and all("peer position ACCEPT" in s["title"] for s in negotiation)

    reports = [s for s in steps if s["kind"] == "report"]
    assert any('alice assumes User.id.type = "UUID"' in s["title"] and "CONSISTENT" in s["title"] for s in reports)
    assert any("UNDERSPECIFIED" in s["title"] for s in reports)
    assert any("CONFLICTING" in s["title"] and "SECURITY_CONFLICT" in s["title"] for s in reports)
    register = [s for s in steps if s["kind"] == "register"]
    assert len(register) == 3 and "generic-demo" in register[0]["title"] and "backend" in register[0]["title"]

    decisions = [s for s in steps if s["kind"] == "decision"]
    assert len(decisions) == 4
    assert any('owner decided User.id.type = "UUID" (human_decision)' == s["title"] for s in decisions)
    assert any(s["lane"] == "oracle" and "oracle_arbitration" in s["title"] for s in decisions)
    assert all(s["title"].endswith(")") for s in decisions)  # the authority suffix survives long values
    assert any(s["kind"] == "arbitration" for s in steps)
    assert any(s["kind"] == "pause_ack" and s["state"]["sessions"][s["lane"]]["work_state"] == "PAUSED" for s in steps)
    assert any(s["kind"] == "proposal" and s["lane"] == "owner" for s in steps)
    assert any(s["kind"] == "round_closed" for s in steps)

    # Cumulative state: the first PAUSE leaves the target PAUSE_REQUESTED; a later question does not reopen.
    first_pause = pauses[0]
    assert first_pause["state"]["sessions"][first_pause["target"]]["work_state"] == "PAUSE_REQUESTED"
    after_question = question["state"]
    assert qid in after_question["open_questions"]
    answered = next(s for s in decisions if s["refs"].get("question_id") == qid)
    assert qid not in answered["state"]["open_questions"]
    conflict = next(s for s in steps if s["kind"] == "conflict_confirmed")
    assert conflict["refs"]["conflict_id"] in conflict["state"]["open_conflicts"]

    last = steps[-1]["state"]
    assert set(last["sessions"]) == {"alice", "bob", "carol"}
    assert all(s["work_state"] == "RUNNING" for s in last["sessions"].values())
    assert last["open_conflicts"] == [] and last["open_questions"] == []
    assert last["decisions"] == 4 and last["model_runs"] == 0

    text = render_text(timeline)
    assert "PAUSE" in text and "decided" in text
    assert "heartbeat" not in text
    assert text.splitlines()[-1].startswith("-- ")
    assert len(text.splitlines()) > len([s for s in steps if s["kind"] != "heartbeat"])
