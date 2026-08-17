from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

from data_tools import (
    DEFAULT_EVAL_BLUEPRINTS_PATH,
    DEFAULT_EVAL_OUTPUT_PATH,
    DEFAULT_FACT_CATALOG_PATH,
    DEFAULT_CONFLICT_FIXTURES_PATH,
    DataToolError,
    load_yaml_mapping,
    normalize_text,
)


CATEGORY_MINIMUMS = {
    "direct_answer": 15,
    "multi_document": 10,
    "partial_answer": 10,
    "missing_information": 10,
    "out_of_scope": 10,
    "ambiguity_follow_up": 10,
    "typo_paraphrase": 10,
    "prompt_injection": 10,
    "user_falsehood": 10,
}
VALID_SUITES = {"retrieval", "pipeline", "adversarial"}
VALID_STATUSES = {
    "ANSWERABLE",
    "PARTIALLY_ANSWERABLE",
    "AMBIGUOUS",
    "NOT_ANSWERABLE",
    "CONFLICTING_EVIDENCE",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate deterministic TopMed evaluation cases.")
    parser.add_argument("--blueprints", type=Path, default=DEFAULT_EVAL_BLUEPRINTS_PATH)
    parser.add_argument("--fact-catalog", type=Path, default=DEFAULT_FACT_CATALOG_PATH)
    parser.add_argument("--conflict-fixtures", type=Path, default=DEFAULT_CONFLICT_FIXTURES_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_EVAL_OUTPUT_PATH)
    parser.add_argument("--force", action="store_true", help="Replace a changed generated evaluation file.")
    return parser.parse_args()


def inherited(group: dict[str, Any], variant: dict[str, Any], key: str, default: Any = None) -> Any:
    return variant[key] if key in variant else group.get(key, default)


def normalize_messages(value: Any, case_id: str) -> list[str]:
    if isinstance(value, str):
        messages = [value]
    elif isinstance(value, list) and value and all(isinstance(item, str) and item for item in value):
        messages = value
    else:
        raise DataToolError(f"Evaluation case {case_id} must define one or more string messages")
    return messages


def build_expected_documents(required_fact_ids: list[str], facts: dict[str, Any]) -> dict[str, list[str]]:
    canonical = {facts[fact_id]["canonical_document"] for fact_id in required_fact_ids}
    acceptable = {
        document
        for fact_id in required_fact_ids
        for document in facts[fact_id]["acceptable_documents"]
        if document not in canonical
    }
    return {
        "canonical": sorted(canonical),
        "acceptable": sorted(acceptable),
    }


def validate_conflict_fixtures(
    fixtures_data: dict[str, Any], catalog: dict[str, Any]
) -> set[str]:
    if fixtures_data.get("dataset_id") != catalog.get("dataset_id"):
        raise DataToolError("Conflict fixture and fact catalog dataset IDs do not match")
    if fixtures_data.get("dataset_version") != catalog.get("dataset_version"):
        raise DataToolError("Conflict fixture and fact catalog versions do not match")
    fixtures = fixtures_data.get("fixtures")
    facts = catalog.get("facts")
    if not isinstance(fixtures, list) or not fixtures:
        raise DataToolError("Conflict fixtures must define a non-empty fixture list")
    fixture_ids: set[str] = set()
    namespaces: set[str] = set()
    document_ids: set[str] = set()
    for fixture in fixtures:
        if not isinstance(fixture, dict):
            raise DataToolError("Every conflict fixture must be a mapping")
        fixture_id = fixture.get("id")
        if not isinstance(fixture_id, str) or not fixture_id:
            raise DataToolError("Every conflict fixture needs an ID")
        if fixture_id in fixture_ids:
            raise DataToolError(f"Duplicate conflict fixture ID: {fixture_id}")
        fixture_ids.add(fixture_id)
        conflict_fact_id = fixture.get("conflicts_with_fact_id")
        if conflict_fact_id not in facts:
            raise DataToolError(f"Fixture {fixture_id} references an unknown fact")
        namespace = fixture.get("namespace")
        if not isinstance(namespace, str) or not namespace.startswith("eval-conflict-"):
            raise DataToolError(f"Fixture {fixture_id} must use an isolated eval-conflict namespace")
        if namespace in namespaces:
            raise DataToolError(f"Duplicate conflict fixture namespace: {namespace}")
        namespaces.add(namespace)
        document = fixture.get("document")
        if not isinstance(document, dict) or not all(
            isinstance(document.get(key), str) and document[key]
            for key in ("document_id", "title", "content")
        ):
            raise DataToolError(f"Fixture {fixture_id} must define a complete temporary document")
        if document["document_id"] in document_ids:
            raise DataToolError(f"Duplicate conflict fixture document ID: {document['document_id']}")
        document_ids.add(document["document_id"])
        if facts[conflict_fact_id]["expected_fragment"] in document["content"]:
            raise DataToolError(f"Fixture {fixture_id} repeats the canonical fact instead of contradicting it")
    return fixture_ids


def generate_cases(
    blueprints: dict[str, Any],
    catalog: dict[str, Any],
    fixture_ids: set[str],
) -> tuple[list[dict[str, Any]], Counter[str]]:
    if blueprints.get("dataset_id") != catalog.get("dataset_id"):
        raise DataToolError("Evaluation blueprint and fact catalog dataset IDs do not match")
    if blueprints.get("dataset_version") != catalog.get("dataset_version"):
        raise DataToolError("Evaluation blueprint and fact catalog versions do not match")

    facts = catalog.get("facts")
    groups = blueprints.get("groups")
    if not isinstance(facts, dict):
        raise DataToolError("Fact catalog facts must be a mapping")
    if not isinstance(groups, list) or not groups:
        raise DataToolError("Evaluation blueprints must define groups")

    cases: list[dict[str, Any]] = []
    case_ids: set[str] = set()
    group_ids: set[str] = set()
    category_counts: Counter[str] = Counter()

    for group in groups:
        if not isinstance(group, dict):
            raise DataToolError("Every evaluation group must be a mapping")
        group_id = group.get("id")
        variants = group.get("variants")
        if not isinstance(group_id, str) or not group_id:
            raise DataToolError("Every evaluation group needs a non-empty ID")
        if group_id in group_ids:
            raise DataToolError(f"Duplicate evaluation group ID: {group_id}")
        group_ids.add(group_id)
        if not isinstance(variants, list) or not variants:
            raise DataToolError(f"Evaluation group {group_id} needs variants")

        for index, raw_variant in enumerate(variants, start=1):
            variant = {"messages": raw_variant} if isinstance(raw_variant, str) else raw_variant
            if not isinstance(variant, dict):
                raise DataToolError(f"Invalid variant in evaluation group {group_id}")

            case_id = variant.get("id", f"{group_id}_{index:03d}")
            if not isinstance(case_id, str) or not case_id:
                raise DataToolError(f"Invalid case ID in group {group_id}")
            if case_id in case_ids:
                raise DataToolError(f"Duplicate evaluation case ID: {case_id}")
            case_ids.add(case_id)

            suite = inherited(group, variant, "suite")
            category = inherited(group, variant, "category")
            expected_status = inherited(group, variant, "expected_status")
            required_fact_ids = inherited(group, variant, "required_fact_ids", [])
            forbidden_fact_ids = inherited(group, variant, "forbidden_fact_ids", [])
            if suite not in VALID_SUITES:
                raise DataToolError(f"Invalid suite for {case_id}: {suite}")
            if not isinstance(category, str) or not category:
                raise DataToolError(f"Missing category for {case_id}")
            if expected_status not in VALID_STATUSES:
                raise DataToolError(f"Invalid expected status for {case_id}: {expected_status}")
            if not isinstance(required_fact_ids, list) or not isinstance(forbidden_fact_ids, list):
                raise DataToolError(f"Fact ID fields must be lists for {case_id}")

            referenced = [*required_fact_ids, *forbidden_fact_ids]
            missing_facts = sorted({fact_id for fact_id in referenced if fact_id not in facts})
            if missing_facts:
                raise DataToolError(f"Unknown fact IDs in {case_id}: {', '.join(missing_facts)}")
            if set(required_fact_ids) & set(forbidden_fact_ids):
                raise DataToolError(f"Case {case_id} requires and forbids the same fact")

            case: dict[str, Any] = {
                "id": case_id,
                "dataset_id": blueprints["dataset_id"],
                "dataset_version": blueprints["dataset_version"],
                "suite": suite,
                "category": category,
                "language": blueprints.get("language", "pt-BR"),
                "messages": normalize_messages(variant.get("messages"), case_id),
                "expected_status": expected_status,
                "required_fact_ids": required_fact_ids,
                "forbidden_fact_ids": forbidden_fact_ids,
                "expected_documents": build_expected_documents(required_fact_ids, facts),
            }
            for optional_key in (
                "required_phrases",
                "clarification_intent",
                "requires_fixture",
                "fixture_id",
            ):
                optional_value = inherited(group, variant, optional_key)
                if optional_value is not None:
                    case[optional_key] = optional_value
            if case.get("requires_fixture"):
                fixture_id = case.get("fixture_id")
                if fixture_id not in fixture_ids:
                    raise DataToolError(f"Case {case_id} references unknown fixture {fixture_id}")
                if expected_status != "CONFLICTING_EVIDENCE":
                    raise DataToolError(f"Fixture case {case_id} must expect CONFLICTING_EVIDENCE")
            cases.append(case)
            category_counts[category] += 1

    if len(cases) < 95:
        raise DataToolError(f"Generated only {len(cases)} evaluation cases; at least 95 are required")
    for category, minimum in CATEGORY_MINIMUMS.items():
        actual = category_counts[category]
        if actual < minimum:
            raise DataToolError(f"Category {category} has {actual} cases; at least {minimum} are required")
    return cases, category_counts


def generate(
    blueprints_path: Path,
    fact_catalog_path: Path,
    conflict_fixtures_path: Path,
    output_path: Path,
    force: bool,
) -> None:
    blueprints = load_yaml_mapping(blueprints_path.resolve())
    catalog = load_yaml_mapping(fact_catalog_path.resolve())
    fixtures = load_yaml_mapping(conflict_fixtures_path.resolve())
    fixture_ids = validate_conflict_fixtures(fixtures, catalog)
    cases, category_counts = generate_cases(blueprints, catalog, fixture_ids)
    output = normalize_text(yaml.safe_dump(cases, allow_unicode=True, sort_keys=False, width=120))
    output_path = output_path.resolve()
    if output_path.exists() and output_path.read_text(encoding="utf-8") != output and not force:
        raise DataToolError(
            f"Refusing to overwrite changed generated evaluations: {output_path}\n"
            "Run again with --force after reviewing the differences."
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(output, encoding="utf-8", newline="\n")

    print(f"✓ Generated {len(cases)} evaluation cases")
    summary = ", ".join(f"{category}={count}" for category, count in sorted(category_counts.items()))
    print(f"✓ Category counts: {summary}")
    print(f"✓ Wrote evaluations: {output_path}")


def main() -> int:
    args = parse_args()
    try:
        generate(args.blueprints, args.fact_catalog, args.conflict_fixtures, args.output, args.force)
    except DataToolError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
