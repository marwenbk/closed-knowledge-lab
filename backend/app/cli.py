from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError

from app.config import PROJECT_ROOT, get_settings
from app.db import get_engine
from app.kb import KnowledgeImportError, import_knowledge_base
from app.services import KnowledgeBaseUnavailable, kb_status, readiness

DEFAULT_MANIFEST = PROJECT_ROOT / "knowledge_base" / "manifest.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="TopMed backend management CLI")
    resources = parser.add_subparsers(dest="resource", required=True)
    kb_parser = resources.add_parser("kb", help="Manage the knowledge base")
    kb_commands = kb_parser.add_subparsers(dest="command", required=True)

    import_parser = kb_commands.add_parser("import", help="Import a generated KB manifest")
    import_parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    import_parser.add_argument("--activate", action="store_true")
    kb_commands.add_parser("status", help="Show the active KB status")
    system_parser = resources.add_parser("system", help="Inspect backend system state")
    system_commands = system_parser.add_subparsers(dest="command", required=True)
    system_commands.add_parser("ready", help="Verify backend readiness")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    engine = get_engine()
    try:
        if args.resource == "kb" and args.command == "import":
            import_result = import_knowledge_base(engine, args.manifest, activate=args.activate)
            print(
                json.dumps(
                    {
                        "kb_version_id": str(import_result.kb_version_id),
                        "dataset_id": import_result.dataset_id,
                        "dataset_version": import_result.dataset_version,
                        "status": import_result.status,
                        "document_count": import_result.document_count,
                        "chunk_count": import_result.chunk_count,
                        "no_op": import_result.no_op,
                    },
                    indent=2,
                )
            )
        elif args.resource == "kb" and args.command == "status":
            print(json.dumps(kb_status(engine), indent=2))
        elif args.resource == "system" and args.command == "ready":
            settings = get_settings()
            readiness_result = readiness(
                engine,
                expected_dataset_id=settings.expected_dataset_id,
                expected_dataset_version=settings.expected_dataset_version,
            )
            print(json.dumps(readiness_result, indent=2))
            if readiness_result["status"] != "ready":
                return 1
    except (KnowledgeImportError, KnowledgeBaseUnavailable, SQLAlchemyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
