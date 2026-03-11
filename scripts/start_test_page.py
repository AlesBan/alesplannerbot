from __future__ import annotations

import argparse
import subprocess
import sys
import time
import webbrowser

from app.database.db import SessionLocal
from app.services.eval_harness import EvalHarnessService


def main() -> None:
    parser = argparse.ArgumentParser(description="Start local test page for question evaluation.")
    parser.add_argument("--telegram-id", type=int, required=True, help="Telegram user id from DB")
    parser.add_argument("--count", type=int, default=100, help="Question count when template mode is used")
    parser.add_argument(
        "--questions-file",
        type=str,
        default="eval_runs/user-questions.csv",
        help="CSV/TXT/JSON file with user questions",
    )
    parser.add_argument("--port", type=int, default=8000, help="Local web port")
    args = parser.parse_args()

    with SessionLocal() as db:
        # If file does not exist yet, create ready-to-edit template.
        from pathlib import Path

        qpath = Path(args.questions_file)
        if not qpath.exists():
            template_path = EvalHarnessService.create_questions_template(path=str(qpath), rows=max(30, args.count))
            print(f"Questions template created: {template_path}")

        run = EvalHarnessService(db).generate_run(
            telegram_id=args.telegram_id,
            count=args.count,
            questions_source_path=str(qpath),
        )

    run_id = run["run_id"]
    url = f"http://127.0.0.1:{args.port}/eval/ui/{run_id}"
    print(f"Run id: {run_id}")
    print(f"Open in browser: {url}")

    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(args.port),
        ]
    )
    try:
        time.sleep(1.5)
        webbrowser.open(url)
        proc.wait()
    except KeyboardInterrupt:
        pass
    finally:
        if proc.poll() is None:
            proc.terminate()


if __name__ == "__main__":
    main()
