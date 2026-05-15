from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from data_agent_baseline.benchmark.schema import AnswerTable, PublicTask
from data_agent_baseline.tools.dataengine import DataEngine


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ToolExecutionResult:
    ok: bool
    content: dict[str, Any]
    is_terminal: bool = False
    answer: AnswerTable | None = None


ToolHandler = Callable[[PublicTask, dict[str, Any]], ToolExecutionResult]


def _answer(_: PublicTask, action_input: dict[str, Any]) -> ToolExecutionResult:
    columns = action_input.get("columns")
    rows = action_input.get("rows")
    if not isinstance(columns, list) or not columns or not all(isinstance(item, str) for item in columns):
        raise ValueError("answer.columns must be a non-empty list of strings.")
    if not isinstance(rows, list):
        raise ValueError("answer.rows must be a list.")

    normalized_rows: list[list[Any]] = []
    for row in rows:
        if not isinstance(row, list):
            raise ValueError("Each answer row must be a list.")
        if len(row) != len(columns):
            raise ValueError("Each answer row must match the number of columns.")
        normalized_rows.append(list(row))

    answer = AnswerTable(columns=list(columns), rows=normalized_rows)
    return ToolExecutionResult(
        ok=True,
        content={
            "status": "submitted",
            "column_count": len(columns),
            "row_count": len(normalized_rows),
        },
        is_terminal=True,
        answer=answer,
    )


def _inspect_data_catalog_handler(
    engine: DataEngine,
    generated_catalog: dict[str, Any] | None,
    schema_sample_rows: int,
) -> ToolHandler:
    def handler(_: PublicTask, action_input: dict[str, Any]) -> ToolExecutionResult:
        sample_rows = int(action_input.get("sample_rows", schema_sample_rows))
        return ToolExecutionResult(
            ok=True,
            content={
                "engine_schema": engine.describe_schema(sample_rows=sample_rows),
                "generated_catalog": generated_catalog or {},
            },
        )

    return handler


def _execute_dataengine_sql_handler(engine: DataEngine, sql_result_limit: int) -> ToolHandler:
    def handler(_: PublicTask, action_input: dict[str, Any]) -> ToolExecutionResult:
        sql = str(action_input["sql"])
        limit = int(action_input.get("limit", sql_result_limit))
        result = engine.query(sql, limit=limit)
        return ToolExecutionResult(ok=bool(result.get("success")), content=result)

    return handler


@dataclass(slots=True)
class ToolRegistry:
    specs: dict[str, ToolSpec]
    handlers: dict[str, ToolHandler]

    def describe_for_prompt(self, tool_names: list[str] | None = None) -> str:
        lines = []
        selected_names = sorted(tool_names or self.specs)
        for name in selected_names:
            if name not in self.specs:
                continue
            spec = self.specs[name]
            lines.append(f"- {spec.name}: {spec.description}")
            lines.append(f"  input_schema: {spec.input_schema}")
        return "\n".join(lines)

    def execute(self, task: PublicTask, action: str, action_input: dict[str, Any]) -> ToolExecutionResult:
        if action not in self.handlers:
            raise KeyError(f"Unknown tool: {action}")
        return self.handlers[action](task, action_input)


def create_default_tool_registry() -> ToolRegistry:
    specs = {
        "answer": ToolSpec(
            name="answer",
            description="Submit the final answer table. This is the only valid terminating action.",
            input_schema={
                "columns": ["column_name"],
                "rows": [["value_1"]],
            },
        ),
    }
    handlers = {
        "answer": _answer,
    }
    return ToolRegistry(specs=specs, handlers=handlers)


def create_dataengine_tool_registry(
    engine: DataEngine,
    *,
    generated_catalog: dict[str, Any] | None = None,
    sql_result_limit: int = 200,
    schema_sample_rows: int = 3,
) -> ToolRegistry:
    specs = {
        "answer": ToolSpec(
            name="answer",
            description="Submit the final answer table. This is the only valid terminating action.",
            input_schema={
                "columns": ["column_name"],
                "rows": [["value_1"]],
            },
        ),
        "execute_dataengine_sql": ToolSpec(
            name="execute_dataengine_sql",
            description=(
                "Run a read-only DuckDB SELECT query over the DataEngine tables. "
                "Use table names exactly as shown in the schema/catalog."
            ),
            input_schema={"sql": "SELECT ...", "limit": sql_result_limit},
        ),
        "inspect_data_catalog": ToolSpec(
            name="inspect_data_catalog",
            description="Inspect the already-loaded DataEngine schema and generated field catalog.",
            input_schema={"sample_rows": schema_sample_rows},
        ),
    }
    handlers = {
        "answer": _answer,
        "execute_dataengine_sql": _execute_dataengine_sql_handler(engine, sql_result_limit),
        "inspect_data_catalog": _inspect_data_catalog_handler(
            engine,
            generated_catalog,
            schema_sample_rows,
        ),
    }
    return ToolRegistry(specs=specs, handlers=handlers)
