from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from sqlalchemy.exc import SQLAlchemyError

from app.answering import AnsweringError, answer_knowledge
from app.config import PROJECT_ROOT, get_settings
from app.db import get_engine
from app.embeddings import EmbeddingError, OnnxE5EmbeddingProvider, embed_knowledge_base
from app.evaluation import EvaluationError, load_evaluation_data, run_evaluation, write_report
from app.kb import KnowledgeImportError, activate_knowledge_base, import_knowledge_base
from app.llm import DeepSeekProvider, LLMError
from app.retrieval import RetrievalError, retrieve_knowledge
from app.services import KnowledgeBaseUnavailable, kb_status, readiness

DEFAULT_MANIFEST = PROJECT_ROOT / "knowledge_base" / "manifest.json"


def print_result(result: Any) -> None:
    print(json.dumps(asdict(result), default=str, indent=2))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="TopMed backend management CLI")
    resources = parser.add_subparsers(dest="resource", required=True)
    kb_parser = resources.add_parser("kb", help="Manage the knowledge base")
    kb_commands = kb_parser.add_subparsers(dest="command", required=True)

    import_parser = kb_commands.add_parser("import", help="Import a generated KB manifest")
    import_parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    embed_parser = kb_commands.add_parser("embed", help="Embed a draft knowledge-base version")
    embed_parser.add_argument("--dataset-id")
    embed_parser.add_argument("--dataset-version")
    embed_parser.add_argument(
        "--download",
        action="store_true",
        help="Download and checksum the pinned model when it is not cached",
    )
    activate_parser = kb_commands.add_parser(
        "activate", help="Activate a completely embedded knowledge-base version"
    )
    activate_parser.add_argument("--dataset-id")
    activate_parser.add_argument("--dataset-version")
    retrieve_parser = kb_commands.add_parser("retrieve", help="Run closed-KB hybrid retrieval")
    retrieve_parser.add_argument("--query", required=True, help="Portuguese retrieval query")
    answer_parser = kb_commands.add_parser("answer", help="Generate a verified closed-KB answer")
    answer_parser.add_argument("--query", required=True, help="Portuguese customer question")
    kb_commands.add_parser("status", help="Show the active KB status")
    system_parser = resources.add_parser("system", help="Inspect backend system state")
    system_commands = system_parser.add_subparsers(dest="command", required=True)
    system_commands.add_parser("ready", help="Verify backend readiness")
    eval_parser = resources.add_parser("eval", help="Run versioned evaluation gates")
    eval_commands = eval_parser.add_subparsers(dest="command", required=True)
    run_eval_parser = eval_commands.add_parser("run", help="Evaluate the active knowledge base")
    run_eval_parser.add_argument(
        "--live",
        action="store_true",
        help="Also run all 100 answer cases through DeepSeek (uses API credit)",
    )
    run_eval_parser.add_argument("--output", type=Path, help="Write the JSON report to this path")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    engine = get_engine()
    settings = get_settings()
    llm_provider: DeepSeekProvider | None = None
    try:
        if args.resource == "kb" and args.command == "import":
            import_result = import_knowledge_base(engine, args.manifest)
            print_result(import_result)
        elif args.resource == "kb" and args.command == "embed":
            if args.download:
                print(
                    "Preparing the pinned checksum-verified embedding model...",
                    file=sys.stderr,
                )
            provider = OnnxE5EmbeddingProvider(settings, download=args.download)
            embedding_result = embed_knowledge_base(
                engine,
                provider,
                batch_size=settings.embedding_batch_size,
                dataset_id=args.dataset_id or settings.expected_dataset_id,
                dataset_version=args.dataset_version or settings.expected_dataset_version,
            )
            print_result(embedding_result)
        elif args.resource == "kb" and args.command == "activate":
            activation_result = activate_knowledge_base(
                engine,
                dataset_id=args.dataset_id or settings.expected_dataset_id,
                dataset_version=args.dataset_version or settings.expected_dataset_version,
                expected_embedding_model=settings.embedding_model_id,
                expected_embedding_version=settings.embedding_model_revision,
                expected_embedding_dimensions=settings.embedding_dimensions,
            )
            print_result(activation_result)
        elif args.resource == "kb" and args.command == "retrieve":
            provider = OnnxE5EmbeddingProvider(settings)
            retrieval_result = retrieve_knowledge(engine, provider, settings, args.query)
            print(json.dumps(retrieval_result.as_dict(), ensure_ascii=False, indent=2))
        elif args.resource == "kb" and args.command == "answer":
            provider = OnnxE5EmbeddingProvider(settings)
            llm_provider = DeepSeekProvider(settings)
            result = answer_knowledge(engine, provider, llm_provider, settings, args.query)
            print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))
        elif args.resource == "kb" and args.command == "status":
            print(json.dumps(kb_status(engine, dataset_id=settings.expected_dataset_id), indent=2))
        elif args.resource == "system" and args.command == "ready":
            OnnxE5EmbeddingProvider(settings)
            llm_provider = DeepSeekProvider(settings)
            llm_version = llm_provider.ensure_ready()
            readiness_result = readiness(
                engine,
                expected_dataset_id=settings.expected_dataset_id,
                expected_embedding_model=settings.embedding_model_id,
                expected_embedding_version=settings.embedding_model_revision,
                expected_embedding_dimensions=settings.embedding_dimensions,
                embedding_runtime_ready=True,
                llm_runtime_ready=True,
                expected_chat_model=settings.chat_model,
                chat_model_version=llm_version,
            )
            print(json.dumps(readiness_result, indent=2))
            if readiness_result["status"] != "ready":
                return 1
        elif args.resource == "eval" and args.command == "run":
            provider = OnnxE5EmbeddingProvider(settings)
            if args.live:
                llm_provider = DeepSeekProvider(settings)
            report = run_evaluation(
                engine,
                provider,
                settings,
                load_evaluation_data(),
                llm_provider=llm_provider,
            )
            if args.output:
                write_report(report, args.output)
            print(json.dumps(report, ensure_ascii=False, indent=2))
            if not report["passed"]:
                return 1
    except (
        EmbeddingError,
        AnsweringError,
        EvaluationError,
        KnowledgeImportError,
        KnowledgeBaseUnavailable,
        LLMError,
        RetrievalError,
        SQLAlchemyError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        if llm_provider is not None:
            llm_provider.close()
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
