from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import get_config
from .generator import ConversationGenerator
from .jobs import JobManager


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate synthetic Sudoku conversation datasets.")
    subparsers = parser.add_subparsers(dest="command")

    run_parser = subparsers.add_parser("run", help="Run a generation job")
    run_parser.add_argument("--samples", type=int, default=None)
    run_parser.add_argument("--conversation-type", choices=["single_turn", "multi_turn", "both"], default=None)
    run_parser.add_argument("--max-turns", type=int, default=None)
    run_parser.add_argument("--job-name", default=None)

    status_parser = subparsers.add_parser("status", help="Check progress for a job")
    status_parser.add_argument("--job-name", required=False)
    status_parser.add_argument("--job-dir", required=False)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    config = get_config()

    if args.command == "run":
        samples = args.samples or config.get("samples", 10)
        conversation_type = args.conversation_type or config.get("conversation_type", "both")
        max_turns = args.max_turns or config.get("max_turns", 6)
        generator = ConversationGenerator(config)
        result = generator.run(
            samples=samples,
            conversation_type=conversation_type,
            max_turns=max_turns,
            job_name=args.job_name,
        )
        print(json.dumps(result, indent=2, sort_keys=True))

    elif args.command == "status":
        if args.job_dir:
            job_dir = Path(args.job_dir)
            progress_path = job_dir / "progress.json"
            if progress_path.exists():
                print(progress_path.read_text(encoding="utf-8"))
            else:
                print(f"No progress file found at {progress_path}")
        elif args.job_name:
            config = get_config()
            job_dir = Path(config["output_path"]) / args.job_name
            progress_path = job_dir / "progress.json"
            if progress_path.exists():
                print(progress_path.read_text(encoding="utf-8"))
            else:
                print(json.dumps({
                    "job_name": args.job_name,
                    "status": "not_found",
                    "message": f"No job found at {job_dir}",
                }, indent=2, sort_keys=True))
        else:
            print("Please provide either --job-name or --job-dir.")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
