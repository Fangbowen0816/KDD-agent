import json, os, traceback

SELF = os.path.abspath(__file__)
base = os.path.join(os.path.dirname(SELF), "artifacts", "runs", "20260512T124834Z")

try:
    lines = []
    task_dirs = sorted(os.listdir(base), key=lambda x: int(x.split('_')[1]) if x.startswith('task_') else 0)
    failed_tasks = []
    success_tasks = []

    for td in task_dirs:
        tp = os.path.join(base, td, "trace.json")
        if not os.path.exists(tp):
            continue
        with open(tp, "r", encoding="utf-8") as f:
            tr = json.load(f)
        if tr.get("answer") is None:
            lines.append(f"FAILED: {td} (id={tr.get('task_id')}) reason={tr.get('failure_reason')}")
            for st in tr.get("steps", []):
                obs = st.get("observation", {})
                act = st.get("action", "")
                s = f"  step{st.get('step_index')}: {act} ok={obs.get('ok')}"
                err = obs.get("error", "")
                if err:
                    s += f" ERR={str(err)[:200]}"
                ct = obs.get("content", {})
                if isinstance(ct, dict) and ct.get("error"):
                    s += f" CERR={str(ct['error'])[:200]}"
                if isinstance(ct, dict) and ct.get("success") is False:
                    s += " [FAIL]"
                lines.append(s)
            failed_tasks.append(td)
        else:
            success_tasks.append(td)

    header = f"TOTAL={len(task_dirs)} OK={len(success_tasks)} FAIL={len(failed_tasks)}"
    result = header + "\n" + "\n".join(lines)

    # Write result back into this file as a triple-quoted string
    with open(SELF, "a", encoding="utf-8") as f:
        f.write("\n\n# === ANALYSIS OUTPUT ===\nRESULT = '''\n" + result + "\n'''\n")

except Exception:
    with open(SELF, "a", encoding="utf-8") as f:
        f.write("\n\n# === ERROR ===\nERROR = '''\n" + traceback.format_exc() + "\n'''\n")
