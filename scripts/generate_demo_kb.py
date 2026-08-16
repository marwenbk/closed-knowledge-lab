from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined, TemplateError

from data_tools import (
    DEFAULT_OUTPUT_DIR,
    DEFAULT_SEED_PATH,
    DEFAULT_TEMPLATE_DIR,
    EXPECTED_TEMPLATES,
    DataToolError,
    combined_checksum,
    consultation_step_pt,
    day_range_pt,
    load_yaml_mapping,
    money_brl,
    normalize_text,
    section_count,
    sha256_bytes,
    sha256_file,
    validate_seed_contract,
    word_count,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate the deterministic TopCare knowledge base.")
    parser.add_argument("--force", action="store_true", help="Replace changed generated files.")
    parser.add_argument("--seed", type=Path, default=DEFAULT_SEED_PATH)
    parser.add_argument("--templates", type=Path, default=DEFAULT_TEMPLATE_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args()


def build_environment(template_dir: Path) -> Environment:
    environment = Environment(
        loader=FileSystemLoader(template_dir),
        undefined=StrictUndefined,
        autoescape=False,
        keep_trailing_newline=True,
    )
    environment.filters["money_brl"] = money_brl
    environment.filters["day_range_pt"] = day_range_pt
    environment.filters["consultation_step_pt"] = consultation_step_pt
    return environment


def render_documents(seed: dict[str, Any], template_dir: Path) -> dict[str, str]:
    missing = [name for name in EXPECTED_TEMPLATES if not (template_dir / name).is_file()]
    if missing:
        raise DataToolError(f"Missing templates: {', '.join(missing)}")

    unexpected = sorted(path.name for path in template_dir.glob("*.md.j2") if path.name not in EXPECTED_TEMPLATES)
    if unexpected:
        raise DataToolError(f"Unexpected templates: {', '.join(unexpected)}")

    environment = build_environment(template_dir)
    rendered: dict[str, str] = {}
    try:
        for template_name in EXPECTED_TEMPLATES:
            output_name = template_name.removesuffix(".j2")
            rendered[output_name] = normalize_text(environment.get_template(template_name).render(seed=seed))
    except TemplateError as exc:
        raise DataToolError(f"Template rendering failed: {exc}") from exc
    return rendered


def build_manifest(
    seed: dict[str, Any], seed_path: Path, template_dir: Path, rendered: dict[str, str]
) -> dict[str, Any]:
    documents = []
    for output_name, content in rendered.items():
        document_id = output_name.removesuffix(".md").split("-", 1)[1]
        documents.append(
            {
                "document_id": document_id,
                "path": output_name,
                "sha256": sha256_bytes(content.encode("utf-8")),
                "word_count": word_count(content),
                "section_count": section_count(content),
            }
        )

    dataset = seed["dataset"]
    template_paths = [template_dir / name for name in EXPECTED_TEMPLATES]
    return {
        "dataset_id": dataset["id"],
        "dataset_version": dataset["version"],
        "generator_version": dataset["generator_version"],
        "language": dataset["language"],
        "seed_checksum": sha256_file(seed_path),
        "template_checksum": combined_checksum(template_paths, template_dir),
        "documents": documents,
    }


def assert_safe_writes(outputs: dict[Path, str], force: bool) -> None:
    changed = []
    for path, content in outputs.items():
        if path.exists() and path.read_text(encoding="utf-8") != content:
            changed.append(path)
    if changed and not force:
        formatted = "\n".join(f"  - {path}" for path in changed)
        raise DataToolError(
            "Refusing to overwrite changed generated files:\n"
            f"{formatted}\nRun again with --force after reviewing the differences."
        )


def generate(seed_path: Path, template_dir: Path, output_dir: Path, force: bool) -> None:
    seed_path = seed_path.resolve()
    template_dir = template_dir.resolve()
    output_dir = output_dir.resolve()

    seed = load_yaml_mapping(seed_path)
    validate_seed_contract(seed)
    rendered = render_documents(seed, template_dir)
    manifest = build_manifest(seed, seed_path, template_dir, rendered)
    manifest_text = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"

    outputs = {output_dir / name: content for name, content in rendered.items()}
    outputs[output_dir / "manifest.json"] = manifest_text
    assert_safe_writes(outputs, force)

    output_dir.mkdir(parents=True, exist_ok=True)
    for path, content in outputs.items():
        path.write_text(content, encoding="utf-8", newline="\n")

    total_words = sum(document["word_count"] for document in manifest["documents"])
    print(f"✓ Generated {len(rendered)} documents ({total_words} words)")
    print(f"✓ Wrote manifest: {output_dir / 'manifest.json'}")


def main() -> int:
    args = parse_args()
    try:
        generate(args.seed, args.templates, args.output, args.force)
    except DataToolError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
