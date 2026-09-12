"""Human CLI, restricted server RPC, and local bridge entrypoints."""

import argparse
import asyncio
import getpass
import json
import os
from pathlib import Path
import sys

from pydantic import ValidationError

from .config import build_bridge, default_db, read_bridge
from .policy import OracleError
from .protocol import Authority, Envelope, Visibility
from .service import Oracle
from .store import Store, dump


def output(value):
    print(json.dumps(value, indent=2, default=str))


def read_json(path=None):
    if path and path != "-":
        data = Path(path).read_bytes()
    else:
        data = sys.stdin.buffer.read(128001)
    if len(data) > 128000:
        raise OracleError("TOO_LARGE", "Request exceeds 128 KB")
    return json.loads(data)


def parser():
    p = argparse.ArgumentParser(prog="oracle", description="ORACLE coordination and human decision CLI")
    p.add_argument("--db", default=default_db())
    p.add_argument("--principal", default=os.environ.get("ORACLE_PRINCIPAL", getpass.getuser()))
    sub = p.add_subparsers(dest="action", required=True)
    init = sub.add_parser("init")
    init.add_argument("project")
    init.add_argument("--owner")
    init.add_argument("--team", default="default")
    init.add_argument("--organization", default="default")
    grant = sub.add_parser("grant")
    grant.add_argument("project")
    grant.add_argument("human")
    grant.add_argument("--role", default="developer")
    share = sub.add_parser("share", help="Allow a consumer project to see a provider project's contract")
    share.add_argument("provider")
    share.add_argument("consumer")
    share.add_argument("subject")
    enroll_p = sub.add_parser("enroll", help="Grant membership and install a restricted SSH key for one participant")
    enroll_p.add_argument("human")
    enroll_p.add_argument("project")
    enroll_p.add_argument("--pubkey", required=True, help="Path to the participant's OpenSSH public key (.pub) or -")
    enroll_p.add_argument("--role", default="developer", choices=["developer", "component_owner", "project_owner"])
    enroll_p.add_argument("--authorized-keys", default=None, help="Default ~/.ssh/authorized_keys")
    enroll_p.add_argument(
        "--executable", default=None, help="oracle-server binary the forced command runs (default: next to this Python)"
    )
    enroll_p.add_argument("--dry-run", action="store_true", help="Print the authorized_keys line without writing or granting")
    for command in ["status", "sessions", "questions", "conflicts", "decisions", "facts", "curate"]:
        s = sub.add_parser(command)
        s.add_argument("project")
        s.add_argument("--json", action="store_true", help="Machine-readable output (status/sessions default to text)")
    question = sub.add_parser("question", help="Show one question with positions and the model suggestion")
    question.add_argument("question_id")
    answer = sub.add_parser("answer", help="Answer a question interactively or with --choose/--value")
    answer.add_argument("question_id")
    answer.add_argument("--choose", type=int, help="Pick a numbered option without prompting")
    answer.add_argument("--value", help="JSON value to decide without prompting")
    answer.add_argument("--reason")
    answer.add_argument("--visibility", choices=list(Visibility), default=Visibility.DEPENDENCY_CONSUMERS)
    for command in ["decide", "correct"]:
        d = sub.add_parser(command)
        d.add_argument("project")
        d.add_argument("subject")
        d.add_argument("predicate")
        d.add_argument("value", help="JSON value")
        d.add_argument("--reason", required=True)
        d.add_argument("--question")
        d.add_argument("--conflict")
        d.add_argument("--visibility", choices=list(Visibility), default=Visibility.DEPENDENCY_CONSUMERS)
    prop = sub.add_parser("propose", help="Open a bounded mediation round on a paused conflict")
    prop.add_argument("conflict")
    prop.add_argument("value", nargs="?", help="JSON value; omit with --suggested to adopt the model proposal")
    prop.add_argument("--reason")
    prop.add_argument("--suggested", action="store_true", help="Use the validated model suggestion stored on the conflict")
    spec = sub.add_parser("import-spec")
    spec.add_argument("project")
    spec.add_argument("path")
    worker = sub.add_parser("worker", help="Run queued semantic reasoning jobs on the local model")
    add_provider_arguments(worker)
    worker.add_argument("--once", action="store_true")
    demo = sub.add_parser("demo", help="Deterministic three-repository demo (no model required)")
    demo.add_argument("--output-dir", default="runtime/demo")
    live = sub.add_parser("live-demo", help="Three-repository demo with the local model reasoning in the loop")
    live.add_argument("--output-dir", default="runtime/live-demo")
    live.add_argument("--narrate", action="store_true", help="Print the story step by step on stderr")
    live.add_argument("--pace", type=float, default=0.0, help="Seconds to wait between steps so a dashboard viewer can follow")
    add_provider_arguments(live)
    dash = sub.add_parser("dashboard", help="Read-only live web dashboard (Live + Replay tabs) over an Oracle database")
    dash.add_argument("--host", default="127.0.0.1", help="Bind address; 0.0.0.0 exposes the read-only view on the LAN")
    dash.add_argument("--port", type=int, default=8765)
    tl = sub.add_parser("timeline", help="Replay the audit ledger of --db as a lane-oriented transcript")
    tl.add_argument("--heartbeats", action="store_true", help="Include HEARTBEAT steps (hidden by default)")
    tl.add_argument("--json", action="store_true", help="Emit the full timeline structure instead of text")
    return p


def add_provider_arguments(p):
    from .model import DEFAULT_ENDPOINT

    p.add_argument(
        "--provider",
        choices=["compatible", "ollama", "nemoclaw"],
        default="compatible",
        help="compatible = local OpenAI-style server such as the NemoClaw vLLM route (default)",
    )
    p.add_argument("--model", default=None, help="Served model name; discovered automatically for compatible")
    p.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    p.add_argument("--sandbox", default=None, help="NemoClaw sandbox name for --provider nemoclaw")
    p.add_argument("--nemoclaw", default=str(Path.home() / ".local/bin/nemoclaw"))


def provider_from(args):
    from .model import build_provider

    return build_provider(
        args.provider, model=args.model, endpoint=args.endpoint, sandbox=args.sandbox, nemoclaw=args.nemoclaw
    )


def main():
    args = parser().parse_args()
    if args.action == "demo":
        from .demo import run_demo

        output(asyncio.run(run_demo(Path(args.output_dir))))
        return
    if args.action == "live-demo":
        from .live import run_live

        output(asyncio.run(run_live(Path(args.output_dir), provider_from(args), narrate=args.narrate, pace=args.pace)))
        return
    if args.action == "dashboard":
        from .dashboard import serve

        serve(args.db, args.host, args.port)
        return
    if args.action == "timeline":
        from .timeline import build_timeline, render_text

        timeline = build_timeline(args.db)
        try:
            if args.json:
                output(timeline)
            else:
                print(render_text(timeline, include_heartbeats=args.heartbeats))
            sys.stdout.flush()
        except BrokenPipeError:  # `oracle timeline | head`
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return
    store = Store(args.db)
    oracle = Oracle(store)
    try:
        action = args.action
        if action == "init":
            result = oracle.create_project(
                args.project, args.owner or args.principal, team=args.team, organization=args.organization
            )
        elif action == "grant":
            result = oracle.grant(args.principal, args.project, args.human, args.role) or {
                "status": "granted"
            }
        elif action == "share":
            result = oracle.share(args.principal, args.provider, args.consumer, args.subject) or {
                "status": "shared"
            }
        elif action == "enroll":
            from .enroll import enroll

            public_key = sys.stdin.read() if args.pubkey == "-" else Path(args.pubkey).read_text()
            result = enroll(
                oracle,
                args.principal,
                args.human,
                args.project,
                public_key,
                role=args.role,
                authorized_keys=args.authorized_keys,
                executable=args.executable,
                db=args.db,
                dry_run=args.dry_run,
            )
        elif action in {"status", "sessions"}:
            result = oracle.status(args.principal, args.project)
            if not args.json:
                print(render_status(result))
                return
        elif action in {"questions", "conflicts", "decisions", "facts"}:
            result = oracle.review(args.principal, args.project, action)
        elif action == "curate":
            result = oracle.curate(args.principal, args.project)
        elif action == "question":
            result = oracle.question_view(args.principal, args.question_id)
            if not getattr(args, "json", False):
                print(render_question(result))
                return
        elif action == "answer":
            result = answer_question(oracle, args)
        elif action in {"decide", "correct"}:
            result = oracle.decide(
                args.principal,
                args.project,
                args.subject,
                args.predicate,
                json.loads(args.value),
                args.reason,
                question_id=args.question,
                conflict_id=args.conflict,
                visibility=args.visibility,
            )
        elif action == "propose":
            if args.value is None and not args.suggested:
                raise ValueError("Provide a JSON value or --suggested")
            result = oracle.propose(
                args.principal,
                args.conflict,
                json.loads(args.value) if args.value is not None else None,
                args.reason,
                use_suggestion=args.suggested,
            )
        elif action == "import-spec":
            result = import_spec(oracle, args.principal, args.project, args.path)
        elif action == "worker":
            asyncio.run(run_worker(oracle, args))
            return
        output(result)
    except (OracleError, ValidationError, ValueError) as error:
        output({"error": getattr(error, "code", "INVALID_INPUT"), "message": str(error)})
        raise SystemExit(1)
    finally:
        store.close()


def render_status(status):
    """Specification section 75 dashboard, as plain text."""
    sessions = status["sessions"]
    counts = {state: sum(s["connectivity"] == state for s in sessions) for state in ["ONLINE", "STALE", "DISCONNECTED", "CLOSED"]}
    paused = sum(s["work_state"] != "RUNNING" for s in sessions)
    lines = [
        f"PROJECT: {status['project_id']}",
        "",
        f"{len(sessions)} sessions   {counts['ONLINE']} online · {counts['STALE']} stale · {counts['DISCONNECTED']} disconnected · {counts['CLOSED']} closed",
        f"{len({s['human_id'] for s in sessions})} humans",
        "",
        "STATUS",
        f"  ✓ {len(sessions) - paused} sessions running",
        f"  ? {status['open_questions']} unresolved specification questions",
        f"  ! {status['open_conflicts']} open conflicts ({paused} sessions paused or awaiting acknowledgement)",
        f"  ● {status['decisions']} recorded decisions",
        "",
        "SESSIONS",
    ]
    for s in sessions:
        lines.append(
            f"  {s['session_id'][:20]:<20} {s['human_id']:<12} {s['repository'][:20]:<20} {s['agent_type'][:14]:<14} "
            f"{s['work_state']:<18} {s['connectivity']:<12} {s['last_seen'][:19]}"
        )
    return "\n".join(lines)


def render_question(view):
    """Specification section 74 clarification prompt."""
    lines = [
        f"Oracle Question {view['question_id']}   (project {view['project_id']}, status {view['status']})",
        f"Subject: {view['subject']}.{view['predicate']}",
        "",
        view["question"],
        "",
    ]
    suggestion = view.get("suggestion")
    if suggestion:
        lines += [suggestion["question"], ""]
        if suggestion.get("why_it_matters"):
            lines += ["Why it matters: " + suggestion["why_it_matters"], ""]
    if view["positions"]:
        lines.append("Current positions:")
        for position in view["positions"]:
            holders = ", ".join(
                (h.get("human", "") + "@" if h.get("human") else "") + h["project"] + f" ({h['source']} {h['confidence']:.2f})"
                for h in position["held_by"]
            )
            lines.append(f"  {json.dumps(position['value']):<24} {holders}")
        if view["restricted_evidence_count"]:
            lines.append(f"  ({view['restricted_evidence_count']} position(s) not disclosed to you)")
        lines.append("")
    if view["related_contracts"]:
        lines.append("Related approved contracts:")
        for c in view["related_contracts"]:
            lines.append(f"  {c['subject']}.{c['predicate']} = {json.dumps(c['value'])}  [{c['source']}]")
        lines.append("")
    if suggestion and suggestion.get("recommendation"):
        model = suggestion.get("model") or "local model"
        lines += [f"Recommendation ({model}): {suggestion['recommendation']}"]
        if suggestion.get("recommendation_reason"):
            lines.append("  " + suggestion["recommendation_reason"])
        lines.append("")
    if view.get("assessment"):
        a = view["assessment"]
        lines += [f"Semantic assessment: {a['classification']} (confidence {a['confidence']:.2f}, severity {a['severity']}) — {a['summary']}", ""]
    affected = ", ".join((a.get("human", "") + "@" if a.get("human") else "") + a["project"] + f" [{a['work_state']}]" for a in view["affected"])
    lines.append(f"Affected sessions: {affected or 'none'}")
    return "\n".join(lines)


def answer_options(view):
    """Numbered options: model quick-selects first, then every observed position, then custom."""
    options = []
    seen = set()
    for label in (view.get("suggestion") or {}).get("options", []):
        if label not in seen:
            options.append({"label": label, "value": label})
            seen.add(label)
    for position in view["positions"]:
        label = json.dumps(position["value"])
        if label not in seen and position["value"] not in seen:
            options.append({"label": label, "value": position["value"]})
            seen.add(label)
    return options


def answer_question(oracle, args):
    view = oracle.question_view(args.principal, args.question_id)
    if view["status"] != "OPEN":
        raise OracleError("INVALID_STATE", "Question is not open")
    options = answer_options(view)
    if args.value is not None:
        value = json.loads(args.value)
    elif args.choose is not None:
        if not 1 <= args.choose <= len(options):
            raise ValueError(f"Choose a number between 1 and {len(options)}")
        value = options[args.choose - 1]["value"]
    else:
        print(render_question(view))
        print()
        for index, option in enumerate(options, 1):
            marker = "  (recommended)" if (view.get("suggestion") or {}).get("recommendation") == option["label"] else ""
            print(f"  [{index}] {option['label']}{marker}")
        print(f"  [{len(options) + 1}] Enter a custom JSON value")
        print(f"  [{len(options) + 2}] Leave open")
        choice = input("Choose: ").strip()
        if not choice.isdigit() or not 1 <= int(choice) <= len(options) + 2:
            raise ValueError("Invalid choice")
        choice = int(choice)
        if choice == len(options) + 2:
            return {"status": "OPEN", "question_id": args.question_id}
        value = json.loads(input("JSON value: ")) if choice == len(options) + 1 else options[choice - 1]["value"]
    reason = args.reason
    if not reason:
        reason = (input("Reason (recorded with the decision): ").strip() if args.value is None and args.choose is None else "") or (
            f"Project owner answered {args.question_id}"
        )
    return oracle.decide(
        args.principal,
        view["project_id"],
        view["subject"],
        view["predicate"],
        value,
        reason,
        question_id=args.question_id,
        visibility=args.visibility,
    )


def import_spec(oracle, principal, project, path):
    import hashlib
    import yaml

    oracle.policy.owner(principal, project)
    data = Path(path).read_bytes()
    config = yaml.safe_load(data)
    if config.get("project_id") != project:
        raise ValueError("Specification project_id mismatch")
    reason = (
        "Approved project specification: " + Path(path).name + " sha256:" + hashlib.sha256(data).hexdigest()
    )
    coordination = {k: v for k, v in (config.get("coordination") or {}).items() if k in {"heartbeat_seconds", "negotiation_rounds", "strict_coordination", "oracle_arbitration", "low_risk_subjects"}}
    protected = list(config.get("protected_contracts") or [])
    if coordination or protected:
        record = oracle.store.record("projects", project)
        record["rules"].update(coordination)
        if protected:
            record["rules"]["protected_contracts"] = protected
        with oracle.store.transaction():
            oracle.store.db.execute("UPDATE projects SET data=? WHERE id=?", (dump(record), project))
            oracle.store.audit(principal, "project_rules_imported", project, {"coordination": coordination, "protected_contracts": protected})
    results = []
    for requirement in config.get("requirements", []):
        results.append(
            oracle.decide(
                principal,
                project,
                requirement["subject"],
                requirement["predicate"],
                requirement["value"],
                reason,
                source=Authority.PROJECT_SPEC,
                visibility=requirement.get("visibility", Visibility.PROJECT),
            )
        )
    return {"imported": len(results), "decisions": results, "coordination": coordination, "protected_contracts": protected}


async def run_worker(oracle, args):
    from .reasoning import ReasoningWorker

    worker = ReasoningWorker(oracle, provider_from(args))
    while True:
        result = await worker.once()
        if args.once or result["status"] != "IDLE":
            output(result)
            sys.stdout.flush()
        if args.once:
            return
        await asyncio.sleep(2 if result["status"] == "IDLE" else 0.1)


def server_main():
    p = argparse.ArgumentParser(prog="oracle-server")
    p.add_argument("--db", default=default_db())
    sub = p.add_subparsers(dest="action", required=True)
    sub.add_parser("serve-ssh")
    ingest = sub.add_parser("ingest")
    ingest.add_argument("--stdin", action="store_true", required=True)
    poll = sub.add_parser("poll")
    poll.add_argument("--session", required=True)
    poll.add_argument("--after", type=int, default=0)
    args = p.parse_args()
    principal = os.environ.get("ORACLE_PRINCIPAL")
    if not principal:
        output(
            {
                "ok": False,
                "error": {
                    "code": "UNAUTHENTICATED",
                    "message": "SSH forced command must bind ORACLE_PRINCIPAL",
                },
            }
        )
        return
    store = Store(args.db)
    oracle = Oracle(store)
    try:
        from .rpc import dispatch

        if args.action == "serve-ssh":
            result = dispatch(oracle, principal, read_json())
        elif args.action == "ingest":
            result = oracle.ingest(principal, Envelope.model_validate(read_json()))
        else:
            result = oracle.poll(principal, args.session, args.after)
        output({"ok": True, "result": result})
    except OracleError as error:
        output({"ok": False, "error": {"code": error.code, "message": str(error)}})
    except (ValueError, KeyError, TypeError, ValidationError):
        output(
            {"ok": False, "error": {"code": "INVALID_REQUEST", "message": "Request failed schema validation"}}
        )
    finally:
        store.close()


def bridge_main():
    p = argparse.ArgumentParser(prog="oracle-bridge")
    p.add_argument("--config", required=True)
    sub = p.add_subparsers(dest="action", required=True)
    for name in ["start", "status", "checkpoint", "messages", "flush", "mcp"]:
        sub.add_parser(name)
    reg = sub.add_parser("register")
    reg.add_argument("file", help="Registration JSON file or -")
    report = sub.add_parser("report")
    report.add_argument("type")
    report.add_argument("file")
    args = p.parse_args()
    config = read_bridge(args.config)
    bridge = build_bridge(config)
    try:
        if args.action == "mcp":
            from .mcp_server import create_mcp

            create_mcp(bridge).run(transport="stdio")
        elif args.action == "start":
            asyncio.run(bridge.run(config.heartbeat_seconds))
        elif args.action == "status":
            output(bridge.status())
        elif args.action == "register":
            from .protocol import Registration

            output(asyncio.run(bridge.register(Registration.model_validate(read_json(args.file)))))
        elif args.action == "report":
            from .protocol import EventType

            output(asyncio.run(bridge.emit(EventType(args.type), read_json(args.file))))
        else:
            operation = {"checkpoint": bridge.checkpoint, "messages": bridge.poll, "flush": bridge.flush}[
                args.action
            ]
            output(asyncio.run(operation()))
    except (OracleError, ValueError) as error:
        output({"error": getattr(error, "code", "INVALID_INPUT"), "message": str(error)})
        raise SystemExit(1)
    finally:
        bridge.close()
