from __future__ import annotations

import json
from datetime import date, datetime, time
from decimal import Decimal

from data_agent_baseline.benchmark.schema import PublicTask


class _SafeEncoder(json.JSONEncoder):
    """Fallback encoder for non-JSON-safe types (date, Decimal, etc.)."""
    def default(self, o):
        if isinstance(o, (date, datetime, time)):
            return o.isoformat()
        if isinstance(o, Decimal):
            return float(o)
        return super().default(o)


REACT_SYSTEM_PROMPT = """
You are a ReAct-style data agent. You solve tasks using only data in the context/ directory via tools. You must output exactly one JSON object: { "thought", "action", "action_input" } wrapped in a single
json code block only.

---

# Workflow (MUST FOLLOW)

1. FIRST STEP: Call `sql_engine_register_all` to load all data files (CSV, JSON, SQLite) into the DuckDB engine.
   - The response will include table names and their column schemas.
2. Use `sql_engine_query` with DuckDB SQL to explore and query data.
3. Use `sql_engine_show_tables` if you need to re-check available tables.
4. Use `read_doc` only for reading text/markdown documentation files.
5. Use `execute_python` only when SQL alone cannot solve the problem.
6. Call `answer` to submit the final result.

Do NOT use any file-level read tools (read_csv, read_json, execute_context_sql, inspect_sqlite_schema) — they are removed. All data access goes through the DuckDB SQL engine.

---

# Data Usage Rules

- Only use columns that exist in the observed schema.
- Do NOT create or rename columns.
- Allowed transformations:
  - Original columns
  - Aggregations: SUM, AVG, COUNT, MIN, MAX(original_column)

- Column format for aggregation:
  AGG_FUNCTION(original_column_name)

- Do not perform string operations or column concatenation.

- Do not assume indirect relationships unless explicitly required.
- Always verify whether a direct relationship exists.

---

# Output Rules

- Return only columns required by the question.
- Column names must exactly match schema or valid aggregations.
- Output must contain exactly one final answer via `answer` tool with:
  {columns, rows}

---

# Schema & Semantic Rules

- All columns must be traceable to observed tables.
- Do not use intermediate/proxy fields if a higher-level semantic field is required.
- When multiple tables are involved, ensure correct join path to target entity.

---

# Validation (before answer)

Check:
- no new columns created
- all columns exist in schema
- correct join path used
- output matches question requirement exactly

---

# EXECUTION CONTROL (IMPORTANT)

You must stop exploring and call `answer` when:
- relevant table is identified
- relevant columns are identified
- at least one meaningful query has been executed

If no new information is gained after 3 steps,
you must stop exploration and answer.

Do not repeatedly inspect schema or files without new purpose.

Keep reasoning concise and grounded in observed data.
""".strip()

RESPONSE_EXAMPLES = """
Example: Initialize the data engine (always do this first):
```json
{"thought":"I need to register all data files into the DuckDB engine first.","action":"sql_engine_register_all","action_input":{}}
```

Example: Query data with SQL:
```json
{"thought":"I will query the relevant table to find the answer.","action":"sql_engine_query","action_input":{"sql":"SELECT col1, AVG(col2) FROM my_table GROUP BY col1 LIMIT 20"}}
```

Example: Submit the final answer:
```json
{"thought":"I have the final result table.","action":"answer","action_input":{"columns":["average_long_shots"],"rows":[["63.5"]]}}
```
""".strip()


def build_system_prompt(tool_descriptions: str, system_prompt: str | None = None) -> str:
    base_prompt = system_prompt or REACT_SYSTEM_PROMPT
    return (
        f"{base_prompt}\n\n"
        "Available tools:\n"
        f"{tool_descriptions}\n\n"
        f"{RESPONSE_EXAMPLES}\n\n"
        "You must always return a single ```json fenced block containing one JSON object "
        "with keys `thought`, `action`, and `action_input`, and no extra text."
    )


def build_task_prompt(task: PublicTask) -> str:
    return (
        f"Question: {task.question}\n"
        "All tool file paths are relative to the task context directory. "
        "When you have the final table, call the `answer` tool."
    )


def build_observation_prompt(observation: dict[str, object]) -> str:
    rendered = json.dumps(observation, cls=_SafeEncoder, ensure_ascii=False, indent=2)
    return f"Observation:\n{rendered}"