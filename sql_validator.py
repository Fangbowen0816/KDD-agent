import importlib.util
import json
from pathlib import Path
from typing import Any

import pandas as pd

from compare import compare_by_content_inclusion, load_csv
from src.data_agent_baseline.tools.dataengine import DataEngine


# 在这里列出你想验证的任务；SQL 内容放在 SQL_FILE_PATH 指向的文件中。
TASKS = [
    # "task_163",
    # "task_169",
    # "task_173",
    # "task_180",
    # "task_199",
    # "task_218",
    # "task_22",
    # "task_249",
    # "task_25",
    # "task_257",
    # "task_269",
    # "task_80",
    # "task_86",
    "task_200",
]

SQL_FILE_PATH = Path("manual_sql.py")
INPUT_ROOT = Path("data/public/input")
GOLD_ROOT = Path("data/public/output")
REPORT_PATH = Path("manual_sql_report.json")


def main() -> None:
    sql_map = load_sql_map(SQL_FILE_PATH)

    report = {
        "sql_file": str(SQL_FILE_PATH),
        "tasks": {},
        "summary": {
            "total": 0,
            "correct": 0,
            "wrong": 0,
            "error": 0,
            "missing_sql": 0,
        },
    }

    for task_id in TASKS:
        result = validate_task(task_id, sql_map.get(task_id))
        report["tasks"][task_id] = result
        report["summary"]["total"] += 1
        report["summary"][result["status"]] += 1

    print_summary(report)
    REPORT_PATH.write_text(
        json.dumps(report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"\nSaved: {REPORT_PATH}")


def load_sql_map(path: Path) -> dict[str, str]:
    if not path.exists():
        raise FileNotFoundError(
            f"SQL file not found: {path}. Create it with SQLS = {{...}} or change "
            "SQL_FILE_PATH in sql_validator.py."
        )

    payload = load_python_sqls(path)
    return normalize_sql_map(payload)


def load_python_sqls(path: Path) -> dict[str, Any]:
    spec = importlib.util.spec_from_file_location("manual_sql_module", path)
    if spec is None or spec.loader is None:
        raise ValueError(f"Could not load Python SQL file: {path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    payload = getattr(module, "SQLS", None)
    if payload is None:
        raise ValueError("Python SQL file must define SQLS = {...}.")
    return payload


def normalize_sql_map(payload: Any) -> dict[str, str]:
    if not isinstance(payload, dict):
        raise ValueError("SQL mapping must be an object keyed by task id.")

    sql_map: dict[str, str] = {}
    for task_id, value in payload.items():
        if not isinstance(value, str):
            raise ValueError(f"Invalid SQL entry for {task_id}. Use a SQL string.")
        sql_map[str(task_id)] = value.strip()
    return sql_map


def validate_task(task_id: str, sql: str | None) -> dict[str, Any]:
    if not sql:
        return {
            "status": "missing_sql",
            "correct": False,
            "error_type": "missing_sql",
            "error_detail": f"No SQL found for {task_id}",
            "accuracy": 0.0,
        }

    context_dir = INPUT_ROOT / task_id / "context"
    gold_path = GOLD_ROOT / task_id / "gold.csv"
    if not context_dir.exists():
        return failure(
            "error",
            "missing_context",
            f"Context dir not found: {context_dir}",
            sql,
        )
    if not gold_path.exists():
        return failure(
            "error",
            "missing_gold",
            f"Gold file not found: {gold_path}",
            sql,
        )

    engine = DataEngine()
    load_result = engine.register_context_dir(context_dir)
    if not load_result.get("success"):
        return failure("error", "load_data_failed", load_result, sql)

    query_result = engine.query(sql, limit=10000)
    if not query_result.get("success"):
        return failure("error", "sql_execution_failed", query_result.get("error"), sql)

    pred_df = pd.DataFrame(
        query_result["data"]["rows"],
        columns=query_result["data"]["columns"],
    )
    gold_df = load_csv(gold_path)
    correct, err_type, err_detail, accuracy = compare_by_content_inclusion(pred_df, gold_df)
    status = "correct" if correct else "wrong"

    return {
        "status": status,
        "correct": correct,
        "error_type": err_type,
        "error_detail": err_detail,
        "accuracy": accuracy,
        "sql": sql,
        "row_count": query_result["row_count"],
        "columns": query_result["data"]["columns"],
        "rows": query_result["data"]["rows"],
        "gold_columns": gold_df.columns.tolist(),
        "gold_rows": gold_df.values.tolist(),
    }


def failure(status: str, error_type: str, detail: Any, sql: str) -> dict[str, Any]:
    return {
        "status": status,
        "correct": False,
        "error_type": error_type,
        "error_detail": detail,
        "accuracy": 0.0,
        "sql": sql,
    }


def print_summary(report: dict[str, Any]) -> None:
    summary = report["summary"]
    print("\n===== SQL VALIDATION SUMMARY =====")
    print(f"Total: {summary['total']}")
    print(f"Correct: {summary['correct']}")
    print(f"Wrong: {summary['wrong']}")
    print(f"Error: {summary['error']}")
    print(f"Missing SQL: {summary['missing_sql']}")

    print("\n===== TASK RESULTS =====")
    for task_id, result in report["tasks"].items():
        status = result["status"]
        accuracy = result.get("accuracy", 0.0)
        error_type = result.get("error_type")
        suffix = f", {error_type}" if error_type else ""
        print(f"{task_id}: {status}, accuracy={accuracy}{suffix}")


if __name__ == "__main__":
    main()
