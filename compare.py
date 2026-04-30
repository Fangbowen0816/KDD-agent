import os
import pandas as pd
import numpy as np
import json

PRED_DIR = "artifacts/runs/20260429T144801Z"
GOLD_DIR = "data/public/output"

TOL = 1e-6


# =========================
# normalization
# =========================
def normalize_value(v):
    """
    unify numeric + string formats
    """
    if pd.isna(v):
        return "__NA__"
    try:
        return round(float(v), 6)
    except:
        return str(v).strip().lower()


def normalize_col(col):
    return col.strip().lower()


def load_csv(path):
    df = pd.read_csv(path)
    df.columns = [normalize_col(c) for c in df.columns]
    return df


def df_to_serializable(df):
    return {
        "columns": df.columns.tolist(),
        "rows": df.values.tolist()
    }


# =========================
# core comparison logic
# =========================
def compare_by_content_inclusion(pred_df, gold_df):
    """
    New Rules:
    1. Column names are ignored
    2. Each column is treated as unordered multiset
    3. Prediction must contain ALL gold columns
    4. Extra columns in prediction are allowed
    """

    # =========================
    # convert each column → normalized multiset
    # =========================
    def build_col_sets(df):
        col_sets = []
        for c in df.columns:
            vals = [normalize_value(v) for v in df[c].tolist()]
            col_sets.append(sorted(vals))
        return col_sets

    pred_col_sets = build_col_sets(pred_df)
    gold_col_sets = build_col_sets(gold_df)

    # =========================
    # try to match every gold column
    # =========================
    pred_used = [False] * len(pred_col_sets)

    for j, gc in enumerate(gold_col_sets):
        matched = False

        for i, pc in enumerate(pred_col_sets):
            if pred_used[i]:
                continue

            if pc == gc:
                pred_used[i] = True
                matched = True
                break

        if not matched:
            return False, "missing_or_mismatched_gold_column", {
                "gold_column_index": j,
                "pred_columns": pred_df.columns.tolist(),
                "gold_columns": gold_df.columns.tolist(),
                "pred_data": df_to_serializable(pred_df),
                "gold_data": df_to_serializable(gold_df)
            }, 0.0

    # =========================
    # all gold columns matched → correct
    # =========================
    return True, None, None, 1.0


# =========================
# main evaluation loop
# =========================
def main():
    tasks = [
        t for t in os.listdir(PRED_DIR)
        if os.path.isdir(os.path.join(PRED_DIR, t))
        and t.startswith("task_")
    ]

    report = {}

    match_tasks = []
    mismatch_tasks = []
    mismatch_details = {}

    for task in sorted(tasks):
        pred_path = os.path.join(PRED_DIR, task, "prediction.csv")
        gold_path = os.path.join(GOLD_DIR, task, "gold.csv")

        if not os.path.exists(pred_path):
            mismatch_tasks.append(task)
            mismatch_details[task] = {
                "correct": False,
                "error_type": "missing_prediction",
                "error_detail": "prediction.csv not found",
                "accuracy": 0.0
            }
            continue

        try:
            pred_df = load_csv(pred_path)
            gold_df = load_csv(gold_path)

            ok, err_type, detail, acc = compare_by_content_inclusion(pred_df, gold_df)

            if ok:
                match_tasks.append(task)
            else:
                mismatch_tasks.append(task)
                mismatch_details[task] = {
                    "correct": False,
                    "error_type": err_type,
                    "error_detail": detail,
                    "accuracy": acc
                }

        except Exception as e:
            mismatch_tasks.append(task)
            mismatch_details[task] = {
                "correct": False,
                "error_type": "runtime_error",
                "error_detail": str(e),
                "accuracy": 0.0
            }

    # =========================
    # 构建最终报告
    # =========================
    final_report = {
        "match": {
            "count": len(match_tasks),
            "tasks": match_tasks
        },
        "mismatch": {
            "count": len(mismatch_tasks),
            "tasks": mismatch_tasks,
            "details": mismatch_details   # ✅ 只在这里放详细信息
        }
    }

    # =========================
    # console summary
    # =========================
    print("\n===== SUMMARY =====")
    print(f"Match: {len(match_tasks)}")
    print(f"Mismatch: {len(mismatch_tasks)}")

    print("\n===== MISMATCH TASKS =====")
    for t in mismatch_tasks:
        print(t)

    # =========================
    # save report
    # =========================
    with open("per_task_report_0429_2.json", "w") as f:
        json.dump(final_report, f, indent=2, ensure_ascii=False)

    print("\nSaved: per_task_report_0429_2.json")
    
if __name__ == "__main__":
    main()