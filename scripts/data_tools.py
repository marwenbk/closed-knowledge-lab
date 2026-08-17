from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SEED_PATH = PROJECT_ROOT / "data" / "seed_rules.yaml"
DEFAULT_FACT_CATALOG_PATH = PROJECT_ROOT / "data" / "fact_catalog.yaml"
DEFAULT_EVAL_BLUEPRINTS_PATH = PROJECT_ROOT / "data" / "eval_blueprints.yaml"
DEFAULT_CONFLICT_FIXTURES_PATH = PROJECT_ROOT / "data" / "conflict_fixtures.yaml"
DEFAULT_EVAL_OUTPUT_PATH = PROJECT_ROOT / "evals" / "cases.yaml"
DEFAULT_TEMPLATE_DIR = PROJECT_ROOT / "data" / "templates"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "knowledge_base"

EXPECTED_TEMPLATES = (
    "01-service-overview.md.j2",
    "02-eligibility.md.j2",
    "03-consultation-hours.md.j2",
    "04-specialties.md.j2",
    "05-family-members.md.j2",
    "06-employer-plans.md.j2",
    "07-consultation-flow.md.j2",
    "08-prescription-policy.md.j2",
    "09-cancellation.md.j2",
    "10-refund-policy.md.j2",
    "11-privacy-policy.md.j2",
    "12-support.md.j2",
    "13-escalation-procedure.md.j2",
    "14-billing-and-payments.md.j2",
    "15-service-limitations.md.j2",
)


class DataToolError(RuntimeError):
    pass


def normalize_text(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n").strip() + "\n"


def load_yaml_mapping(path: Path) -> dict[str, Any]:
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise DataToolError(f"Required file does not exist: {path}") from exc
    except yaml.YAMLError as exc:
        raise DataToolError(f"Invalid YAML in {path}: {exc}") from exc

    if not isinstance(loaded, dict):
        raise DataToolError(f"Expected a YAML mapping in {path}")
    return loaded


def require_mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise DataToolError(f"Expected mapping at {path}")
    return value


def require_keys(value: Mapping[str, Any], keys: Iterable[str], path: str) -> None:
    missing = [key for key in keys if key not in value]
    if missing:
        raise DataToolError(f"Missing required keys at {path}: {', '.join(missing)}")


def validate_seed_contract(seed: Mapping[str, Any]) -> None:
    require_keys(
        seed,
        (
            "schema_version",
            "dataset",
            "service",
            "plans",
            "specialties",
            "service_hours",
            "eligibility",
            "dependents",
            "consultation_flow",
            "policies",
            "privacy",
            "support",
            "escalation",
            "limitations",
            "intentional_gaps",
        ),
        "seed",
    )

    dataset = require_mapping(seed["dataset"], "seed.dataset")
    require_keys(
        dataset, ("id", "version", "generator_version", "language", "timezone"), "seed.dataset"
    )

    plans = require_mapping(seed["plans"], "seed.plans")
    require_keys(plans, ("consumer", "employer_tiers", "employer_rules"), "seed.plans")
    consumer = require_mapping(plans["consumer"], "seed.plans.consumer")
    employer = require_mapping(plans["employer_tiers"], "seed.plans.employer_tiers")
    require_keys(consumer, ("essencial", "familia", "premium"), "seed.plans.consumer")
    require_keys(employer, ("silver", "gold", "platinum"), "seed.plans.employer_tiers")

    if not isinstance(seed["intentional_gaps"], list) or not seed["intentional_gaps"]:
        raise DataToolError("seed.intentional_gaps must be a non-empty list")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def combined_checksum(paths: Iterable[Path], relative_to: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda item: item.relative_to(relative_to).as_posix()):
        relative_path = path.relative_to(relative_to).as_posix().encode("utf-8")
        digest.update(relative_path)
        digest.update(b"\0")
        digest.update(normalize_text(path.read_text(encoding="utf-8")).encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def word_count(value: str) -> int:
    return len(re.findall(r"\b[\wÀ-ÿ]+(?:[-'][\wÀ-ÿ]+)*\b", value, flags=re.UNICODE))


def section_count(value: str) -> int:
    return sum(1 for line in value.splitlines() if re.match(r"^#{1,6}\s+", line))


def parse_front_matter(value: str, path: Path) -> tuple[dict[str, Any], str]:
    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    if not normalized.startswith("---\n"):
        raise DataToolError(f"Missing YAML front matter in {path}")

    try:
        raw_front_matter, body = normalized[4:].split("\n---\n", 1)
    except ValueError as exc:
        raise DataToolError(f"Unclosed YAML front matter in {path}") from exc

    try:
        metadata = yaml.safe_load(raw_front_matter)
    except yaml.YAMLError as exc:
        raise DataToolError(f"Invalid front matter in {path}: {exc}") from exc
    if not isinstance(metadata, dict):
        raise DataToolError(f"Front matter must be a mapping in {path}")
    return metadata, body


def money_brl(value: Any) -> str:
    return str(value).replace(".", ",")


def day_range_pt(value: str) -> str:
    labels = {
        "every_day": "todos os dias",
        "monday_to_friday": "de segunda a sexta-feira",
        "monday_to_saturday": "de segunda-feira a sábado",
    }
    try:
        return labels[value]
    except KeyError as exc:
        raise DataToolError(f"Unsupported day range: {value}") from exc


def consultation_step_pt(value: str) -> str:
    labels = {
        "sign_in": "Entrar na conta",
        "choose_service": "Escolher o serviço",
        "confirm_profile": "Confirmar o perfil que será atendido",
        "enter_queue_or_choose_appointment": "Entrar na fila imediata ou escolher um horário",
        "complete_consultation": "Realizar a consulta",
        "store_consultation_summary": "Armazenar o resumo da consulta",
        "arrange_follow_up_when_available": "Organizar o acompanhamento quando disponível",
    }
    try:
        return labels[value]
    except KeyError as exc:
        raise DataToolError(f"Unsupported consultation step: {value}") from exc
