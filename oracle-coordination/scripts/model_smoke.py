"""Small live provider check against the local Qwen serving; not a statistical semantic-quality benchmark.

Usage: .venv/bin/python scripts/model_smoke.py [--provider compatible|ollama|nemoclaw] [--model NAME] ...
"""

import argparse
import asyncio
import json
from pathlib import Path

from oracle.cli import add_provider_arguments, provider_from
from oracle.protocol import ConflictAssessment


CASES = [
    ("compatible_uuid", "UUID", "UUID string", "CONSISTENT"),
    (
        "authentication_mismatch",
        "JWT bearer token only; no cookies",
        "session-cookie authentication only; no bearer token",
        "CONFLICTING",
    ),
]


async def main():
    parser = argparse.ArgumentParser()
    add_provider_arguments(parser)
    parser.add_argument("--output", default="runtime/model-smoke.json")
    args = parser.parse_args()
    provider = provider_from(args)
    results = []
    for name, left, right, expected in CASES:
        context = {
            "subject": name,
            "facts": [
                {
                    "fact_id": "spec-1",
                    "subject": name,
                    "predicate": "contract",
                    "value": left,
                    "source": "project_spec",
                    "confidence": 0.95,
                },
                {
                    "fact_id": "agent-2",
                    "subject": name,
                    "predicate": "contract",
                    "value": right,
                    "source": "agent_decision",
                    "confidence": 0.8,
                },
            ],
            "incomplete": False,
        }
        try:
            answer = await provider.structured_completion("conflict-detector", context, ConflictAssessment)
        except Exception as error:  # noqa: BLE001 - the smoke test reports every failure kind
            result = {"case": name, "expected": expected, "passed": False, "error": f"{type(error).__name__}: {error}"}
        else:
            ids = set(answer.evidence_fact_ids)
            passed = answer.classification.value == expected and ids.issubset({"spec-1", "agent-2"}) and bool(ids)
            result = {
                "case": name,
                "expected": expected,
                "passed": passed,
                "assessment": answer.model_dump(mode="json"),
                "metrics": provider.last_metrics,
            }
        results.append(result)
        print(json.dumps(result), flush=True)
    target = Path(args.output)
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps({"provider": args.provider, "model": getattr(provider, "model", None), "results": results}, indent=2))
    if not all(r["passed"] for r in results):
        raise SystemExit("A semantic smoke case failed; inspect " + str(target))


asyncio.run(main())
