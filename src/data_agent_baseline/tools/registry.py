from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


from data_agent_baseline.benchmark.schema import AnswerTable, PublicTask
from data_agent_baseline.tools.filesystem import (
    list_context_tree,
    read_doc_preview,
)
from data_agent_baseline.tools.python_exec import execute_python_code

from data_agent_baseline.tools.dataengine import DataEngine
engine: DataEngine | None = None

def reset_engine():
    """Create a fresh DataEngine for each task."""
    global engine
    engine = DataEngine()

EXECUTE_PYTHON_TIMEOUT_SECONDS = 30


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


def _list_context(task: PublicTask, action_input: dict[str, Any]) -> ToolExecutionResult:
    max_depth = int(action_input.get("max_depth", 4))
    return ToolExecutionResult(ok=True, content=list_context_tree(task, max_depth=max_depth))


def _read_doc(task: PublicTask, action_input: dict[str, Any]) -> ToolExecutionResult:
    path = str(action_input["path"])
    max_chars = int(action_input.get("max_chars", 4000))
    return ToolExecutionResult(ok=True, content=read_doc_preview(task, path, max_chars=max_chars))


def _execute_python(task: PublicTask, action_input: dict[str, Any]) -> ToolExecutionResult:
    code = str(action_input["code"])
    content = execute_python_code(
        context_root=task.context_dir,
        code=code,
        timeout_seconds=EXECUTE_PYTHON_TIMEOUT_SECONDS,
    )
    return ToolExecutionResult(ok=bool(content.get("success")), content=content)


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

MAX_FILE_SIZE_BYTES = 100 * 1024 * 1024  # 100 MB


def _sql_engine_register_all(task: PublicTask, _action_input: dict[str, Any]) -> ToolExecutionResult:
    import os
    global engine
    reset_engine()
    context_root = task.context_dir
    results = []
    for root, _, files in os.walk(context_root):
        for file in sorted(files):
            full_path = os.path.join(root, file)
            try:
                fsize = os.path.getsize(full_path)
            except OSError:
                fsize = 0
            if fsize > MAX_FILE_SIZE_BYTES:
                results.append({
                    "success": False,
                    "error": f"Skipped: file too large ({fsize // (1024*1024)}MB > 100MB)",
                    "file": os.path.basename(full_path),
                })
                continue
            res = engine.register(full_path)
            results.append(res)
    tables_info = engine.show_tables()
    schema_info = {}
    for tbl_name, meta in engine.catalog.items():
        schema_info[tbl_name] = meta.get("columns", [])
    return ToolExecutionResult(ok=True, content={
        "message": "All context files registered into DuckDB engine",
        "tables": tables_info,
        "schema": schema_info,
        "results": results
    })

def _sql_engine_query(_task: PublicTask, action_input: dict[str, Any]) -> ToolExecutionResult:
    sql = str(action_input["sql"])
    return ToolExecutionResult(ok=True, content=engine.query(sql))

def _sql_engine_show_tables(_task: PublicTask, _action_input: dict[str, Any]) -> ToolExecutionResult:
    return ToolExecutionResult(ok=True, content=engine.show_tables())

@dataclass(slots=True)
class ToolRegistry:
    specs: dict[str, ToolSpec]
    handlers: dict[str, ToolHandler]

    def describe_for_prompt(self) -> str:
        lines = []
        for name in sorted(self.specs):
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
        "execute_python": ToolSpec(
            name="execute_python",
            description=(
                "Execute arbitrary Python code with the task context directory as the "
                "working directory. The tool returns the code's captured stdout as `output`. "
                f"The execution timeout is fixed at {EXECUTE_PYTHON_TIMEOUT_SECONDS} seconds."
            ),
            input_schema={
                "code": "import os\nprint(sorted(os.listdir('.')))",
            },
        ),
        "list_context": ToolSpec(
            name="list_context",
            description="List files and directories available under context.",
            input_schema={"max_depth": 4},
        ),
        "read_doc": ToolSpec(
            name="read_doc",
            description="Read a text-like document inside context.",
            input_schema={"path": "relative/path/to/file.md", "max_chars": 4000},
        ),
        "sql_engine_register_all": ToolSpec(
            name="sql_engine_register_all",
            description="Scan and register all files (CSV, JSON, DB) in the context into the SQL engine. Run this at the beginning of the task.",
            input_schema={},
        ),
        "sql_engine_query": ToolSpec(
            name="sql_engine_query",
            description="Execute a DuckDB SQL query against registered tables. Returns columns and rows.",
            input_schema={"sql": "SELECT * FROM table_name LIMIT 10"},
        ),
        "sql_engine_show_tables": ToolSpec(
            name="sql_engine_show_tables",
            description="List all table names currently available in the SQL engine.",
            input_schema={},
        ),
    }
    handlers = {
        "answer": _answer,
        "execute_python": _execute_python,
        "list_context": _list_context,
        "read_doc": _read_doc,
        "sql_engine_register_all": _sql_engine_register_all,
        "sql_engine_query": _sql_engine_query,
        "sql_engine_show_tables": _sql_engine_show_tables,
    }
    return ToolRegistry(specs=specs, handlers=handlers)
