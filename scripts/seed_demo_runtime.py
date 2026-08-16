from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from data_tools import (
    DEFAULT_CONFLICT_FIXTURES_PATH,
    DataToolError,
    PROJECT_ROOT,
    load_yaml_mapping,
)


DEFAULT_SCENARIOS_PATH = PROJECT_ROOT / "demo" / "scenarios.yaml"
DEFAULT_REPORT_PATH = PROJECT_ROOT / "demo" / "seed-report.json"
VALID_STATUSES = {
    "ANSWERABLE",
    "PARTIALLY_ANSWERABLE",
    "AMBIGUOUS",
    "NOT_ANSWERABLE",
    "CONFLICTING_EVIDENCE",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replay versioned demo scenarios through the real chat API.")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--admin-token")
    parser.add_argument("--scenarios", type=Path, default=DEFAULT_SCENARIOS_PATH)
    parser.add_argument("--conflict-fixtures", type=Path, default=DEFAULT_CONFLICT_FIXTURES_PATH)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT_PATH)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--include-disabled",
        action="store_true",
        help="Include scenarios that require a separately prepared fixture namespace.",
    )
    return parser.parse_args()


def validate_scenarios(data: dict[str, Any], fixture_ids: set[str]) -> list[dict[str, Any]]:
    for key in ("schema_version", "dataset_id", "dataset_version", "scenarios"):
        if key not in data:
            raise DataToolError(f"Scenario file is missing {key}")
    scenarios = data["scenarios"]
    if not isinstance(scenarios, list) or len(scenarios) < 10:
        raise DataToolError("At least 10 runtime demo scenarios are required")

    scenario_ids: set[str] = set()
    for scenario in scenarios:
        if not isinstance(scenario, dict):
            raise DataToolError("Every scenario must be a mapping")
        scenario_id = scenario.get("id")
        if not isinstance(scenario_id, str) or not scenario_id:
            raise DataToolError("Every scenario needs a non-empty ID")
        if scenario_id in scenario_ids:
            raise DataToolError(f"Duplicate scenario ID: {scenario_id}")
        scenario_ids.add(scenario_id)
        if not isinstance(scenario.get("title"), str) or not scenario["title"]:
            raise DataToolError(f"Scenario {scenario_id} needs a title")
        messages = scenario.get("messages")
        if not isinstance(messages, list) or not messages:
            raise DataToolError(f"Scenario {scenario_id} needs messages")
        for message in messages:
            if not isinstance(message, dict) or not isinstance(message.get("text"), str) or not message["text"]:
                raise DataToolError(f"Scenario {scenario_id} contains an invalid message")
            expected_status = message.get("expected_status")
            if expected_status is not None and expected_status not in VALID_STATUSES:
                raise DataToolError(f"Scenario {scenario_id} has invalid status {expected_status}")
        if scenario.get("requires_fixture") and scenario.get("enabled_by_default", True):
            raise DataToolError(f"Fixture scenario {scenario_id} must be disabled by default")
        if scenario.get("requires_fixture") and scenario["requires_fixture"] not in fixture_ids:
            raise DataToolError(f"Scenario {scenario_id} references an unknown conflict fixture")
        feedback = scenario.get("feedback")
        if feedback is not None and (
            not isinstance(feedback, dict)
            or not isinstance(feedback.get("rating"), str)
            or not feedback["rating"]
        ):
            raise DataToolError(f"Scenario {scenario_id} contains invalid feedback")
    return scenarios


def post_json(url: str, payload: dict[str, Any], token: str | None = None) -> dict[str, Any]:
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise DataToolError(f"HTTP {exc.code} from {url}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise DataToolError(f"Could not reach {url}: {exc.reason}") from exc
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise DataToolError(f"Non-JSON response from {url}") from exc
    if not isinstance(parsed, dict):
        raise DataToolError(f"Expected a JSON object from {url}")
    return parsed


def replay_scenario(
    base_url: str,
    scenario: dict[str, Any],
    admin_token: str | None,
) -> dict[str, Any]:
    conversation_id: str | None = None
    message_results = []
    started = time.perf_counter()
    for message in scenario["messages"]:
        response = post_json(
            f"{base_url.rstrip('/')}/api/chat",
            {"conversation_id": conversation_id, "message": message["text"]},
        )
        response_conversation_id = response.get("conversation_id")
        if not isinstance(response_conversation_id, str) or not response_conversation_id:
            raise DataToolError(f"Scenario {scenario['id']} received no conversation_id")
        if conversation_id is not None and response_conversation_id != conversation_id:
            raise DataToolError(f"Scenario {scenario['id']} changed conversation_id between turns")
        conversation_id = response_conversation_id
        expected_status = message.get("expected_status")
        actual_status = response.get("status")
        if expected_status is not None and actual_status != expected_status:
            raise DataToolError(
                f"Scenario {scenario['id']} expected {expected_status}, received {actual_status}"
            )
        message_results.append(
            {
                "message_id": response.get("message_id"),
                "rag_run_id": response.get("rag_run_id"),
                "expected_status": expected_status,
                "actual_status": actual_status,
            }
        )
    feedback_submitted = False
    if scenario.get("feedback"):
        if not admin_token:
            raise DataToolError(f"Scenario {scenario['id']} requires --admin-token for feedback")
        last_rag_run_id = message_results[-1]["rag_run_id"]
        if not isinstance(last_rag_run_id, str) or not last_rag_run_id:
            raise DataToolError(f"Scenario {scenario['id']} received no rag_run_id for feedback")
        post_json(
            f"{base_url.rstrip('/')}/api/admin/feedback",
            {
                "rag_run_id": last_rag_run_id,
                "rating": scenario["feedback"]["rating"],
                "note": scenario["feedback"].get("note"),
            },
            admin_token,
        )
        feedback_submitted = True
    return {
        "id": scenario["id"],
        "conversation_id": conversation_id,
        "duration_ms": round((time.perf_counter() - started) * 1000),
        "messages": message_results,
        "feedback_submitted": feedback_submitted,
    }


def write_report(path: Path, data: dict[str, Any]) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def main() -> int:
    args = parse_args()
    try:
        scenario_data = load_yaml_mapping(args.scenarios.resolve())
        fixture_data = load_yaml_mapping(args.conflict_fixtures.resolve())
        if fixture_data.get("dataset_id") != scenario_data.get("dataset_id"):
            raise DataToolError("Scenario and conflict fixture dataset IDs do not match")
        if fixture_data.get("dataset_version") != scenario_data.get("dataset_version"):
            raise DataToolError("Scenario and conflict fixture versions do not match")
        fixtures = fixture_data.get("fixtures")
        if not isinstance(fixtures, list):
            raise DataToolError("Conflict fixture file must define fixtures")
        fixture_ids = {
            fixture["id"]
            for fixture in fixtures
            if isinstance(fixture, dict) and isinstance(fixture.get("id"), str)
        }
        scenarios = validate_scenarios(scenario_data, fixture_ids)
        selected = [
            scenario
            for scenario in scenarios
            if args.include_disabled or scenario.get("enabled_by_default", True)
        ]
        if args.dry_run:
            print(f"✓ Validated {len(scenarios)} runtime scenarios")
            print(f"✓ {len(selected)} scenarios enabled for replay")
            return 0

        results = []
        try:
            for scenario in selected:
                results.append(replay_scenario(args.base_url, scenario, args.admin_token))
        except DataToolError as exc:
            write_report(
                args.report,
                {
                    "dataset_id": scenario_data["dataset_id"],
                    "dataset_version": scenario_data["dataset_version"],
                    "seeded_at": datetime.now(UTC).isoformat(),
                    "base_url": args.base_url,
                    "status": "failed",
                    "error": str(exc),
                    "completed_scenario_count": len(results),
                    "results": results,
                },
            )
            raise
        report = {
            "dataset_id": scenario_data["dataset_id"],
            "dataset_version": scenario_data["dataset_version"],
            "seeded_at": datetime.now(UTC).isoformat(),
            "base_url": args.base_url,
            "status": "complete",
            "scenario_count": len(results),
            "results": results,
        }
        write_report(args.report, report)
        print(f"✓ Replayed {len(results)} runtime scenarios")
        print(f"✓ Wrote seed report: {args.report.resolve()}")
    except DataToolError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
