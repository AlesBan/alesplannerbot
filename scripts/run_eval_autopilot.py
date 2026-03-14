from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.database.db import SessionLocal
from app.services.eval_harness import EvalHarnessService


def main() -> None:
    parser = argparse.ArgumentParser(description="Run local eval autopilot (no Telegram UI).")
    parser.add_argument("--telegram-id", type=int, required=True, help="Telegram user id from DB")
    parser.add_argument("--count", type=int, default=100, help="How many generated questions")
    args = parser.parse_args()

    with SessionLocal() as db:
        harness = EvalHarnessService(db)
        run = harness.generate_run(args.telegram_id, count=args.count)
        rated = EvalHarnessService.auto_rate_run(run["run_id"])
        summary = (rated or {}).get("autograde_summary") or {}

        report = {
            "run_id": run["run_id"],
            "telegram_id": args.telegram_id,
            "items": len(run.get("items", [])),
            "good": int(summary.get("good") or 0),
            "bad": int(summary.get("bad") or 0),
            "accuracy": float(summary.get("accuracy") or 0.0),
            "json_path": str((Path("eval_runs") / f"{run['run_id']}.json").resolve()),
            "csv_hint": f"/eval/run/{run['run_id']}/export.csv",
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
