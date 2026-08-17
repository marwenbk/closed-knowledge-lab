from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from data_tools import DataToolError

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate and validate local TopMed demo data.")
    parser.add_argument("--force", action="store_true", help="Replace changed generated data.")
    parser.add_argument("--api-url", default="http://localhost:8000")
    parser.add_argument(
        "--assistant-key", default=os.environ.get("WIDGET_ASSISTANT_KEY", "topmed-local-demo")
    )
    parser.add_argument("--origin", default="http://localhost:3000")
    parser.add_argument("--seed-runtime", action="store_true")
    parser.add_argument("--ready-timeout", type=int, default=120)
    return parser.parse_args()


def run(script_name: str, *arguments: str) -> None:
    command = [sys.executable, str(SCRIPTS_DIR / script_name), *arguments]
    subprocess.run(command, cwd=PROJECT_ROOT, check=True)


def request_json(
    url: str,
    *,
    method: str = "GET",
    payload: dict[str, object] | None = None,
) -> dict[str, object]:
    headers = {"Accept": "application/json"}
    body = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise DataToolError(f"HTTP {exc.code} from {url}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise DataToolError(f"Could not reach {url}: {exc.reason}") from exc
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise DataToolError(f"Non-JSON response from {url}") from exc
    if not isinstance(parsed, dict):
        raise DataToolError(f"Expected a JSON object from {url}")
    return parsed


def wait_until_ready(api_url: str, timeout_seconds: int) -> None:
    base_url = api_url.rstrip("/")
    deadline = time.monotonic() + timeout_seconds
    last_error: str | None = None
    while time.monotonic() < deadline:
        try:
            request_json(f"{base_url}/ready")
            print("✓ API reports ready")
            return
        except DataToolError as exc:
            last_error = str(exc)
            time.sleep(2)
    raise DataToolError(f"API did not become ready within {timeout_seconds}s: {last_error}")


def main() -> int:
    args = parse_args()
    generator_arguments = ("--force",) if args.force else ()
    try:
        run("generate_demo_kb.py", *generator_arguments)
        run("validate_demo_kb.py")
        run("generate_evals.py", *generator_arguments)
        run("seed_demo_runtime.py", "--dry-run")
        if args.seed_runtime:
            if not args.assistant_key:
                raise DataToolError(
                    "--assistant-key or WIDGET_ASSISTANT_KEY is required with --seed-runtime"
                )
            wait_until_ready(args.api_url, args.ready_timeout)
            run(
                "seed_demo_runtime.py",
                "--base-url",
                args.api_url,
                "--assistant-key",
                args.assistant_key,
                "--origin",
                args.origin,
            )
    except subprocess.CalledProcessError as exc:
        return exc.returncode
    except DataToolError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print("✓ Local demo data contract is ready")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
