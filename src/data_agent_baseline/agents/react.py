from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from data_agent_baseline.agents.model import ModelAdapter, ModelMessage, ModelStep
from data_agent_baseline.agents.prompt import (
    BASE_SYSTEM_PROMPT,
    build_answer_prompt,
    build_catalog_prompt,
    build_nl2sql_prompt,
    build_plan_prompt,
    build_system_prompt,
)
from data_agent_baseline.agents.runtime import (
    AgentRunResult,
    AgentRuntimeState,
    StepRecord,
    build_trace_payload,
)
from data_agent_baseline.benchmark.schema import AnswerTable, PublicTask
from data_agent_baseline.tools.dataengine import DataEngine
from data_agent_baseline.tools.registry import ToolRegistry, create_dataengine_tool_registry

SQL_HISTORY_WINDOW = 2
SQL_PREVIEW_ROWS = 2
SQL_ERROR_CHARS = 240


@dataclass(frozen=True, slots=True)
class ReActAgentConfig:
    max_steps: int = 16
    max_sql_attempts: int = 5
    sql_result_limit: int = 200
    catalog_sample_rows: int = 3


def _strip_json_fence(raw_response: str) -> str:
    text = raw_response.strip()
    fence_match = re.search(r"```json\s*(.*?)\s*```", text, flags=re.IGNORECASE | re.DOTALL)
    if fence_match is not None:
        return fence_match.group(1).strip()
    generic_fence_match = re.search(r"```\s*(.*?)\s*```", text, flags=re.DOTALL)
    if generic_fence_match is not None:
        return generic_fence_match.group(1).strip()
    return text


def _load_single_json_object(text: str) -> dict[str, Any]:
    payload, end = json.JSONDecoder().raw_decode(text)
    remainder = text[end:].strip()
    if remainder:
        cleaned_remainder = re.sub(r"(?:\\[nrt])+", "", remainder).strip()
        if cleaned_remainder:
            raise ValueError("Model response must contain only one JSON object.")
    if not isinstance(payload, dict):
        raise ValueError("Model response must be a JSON object.")
    return payload


def parse_model_payload(raw_response: str) -> dict[str, Any]:
    normalized = _strip_json_fence(raw_response)
    return _load_single_json_object(normalized)


def parse_model_step(raw_response: str) -> ModelStep:
    payload = parse_model_payload(raw_response)

    thought = payload.get("thought", "")
    action = payload.get("action")
    action_input = payload.get("action_input", {})
    if not isinstance(thought, str):
        raise ValueError("thought must be a string.")
    if not isinstance(action, str) or not action:
        raise ValueError("action must be a non-empty string.")
    if not isinstance(action_input, dict):
        raise ValueError("action_input must be a JSON object.")

    return ModelStep(
        thought=thought,
        action=action,
        action_input=action_input,
        raw_response=raw_response,
    )


def _normalize_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if isinstance(item, str) and item]


def _shorten_text(value: Any, *, max_chars: int) -> str | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) <= max_chars:
        return text
    return f"{text[: max_chars - 3]}..."


def _extract_result_data(result: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(result, dict):
        return None
    payload = result.get("data")
    if isinstance(payload, dict):
        return payload
    return None


def _extract_table_names(value: Any) -> set[str]:
    table_names: set[str] = set()
    if isinstance(value, str):
        for table_name, _ in re.findall(r"\b([A-Za-z_]\w*)\.([A-Za-z_]\w*)\b", value):
            table_names.add(table_name)
        return table_names
    if isinstance(value, dict):
        for item in value.values():
            table_names.update(_extract_table_names(item))
        return table_names
    if isinstance(value, list):
        for item in value:
            table_names.update(_extract_table_names(item))
    return table_names


def _compact_plan(plan: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "target",
        "source_tables",
        "table_roles",
        "join_steps",
        "filters",
        "select_expressions",
        "aggregations",
        "group_by",
        "order_by",
        "limit",
        "distinct",
        "final_columns",
        "validation_checks",
    ]
    return {key: plan[key] for key in keys if key in plan}


def _compact_catalog(catalog: dict[str, Any]) -> dict[str, Any]:
    compact_tables: dict[str, Any] = {}
    tables = catalog.get("tables")
    if isinstance(tables, dict):
        for table_name, table_payload in tables.items():
            if not isinstance(table_name, str) or not isinstance(table_payload, dict):
                continue
            compact_columns: dict[str, Any] = {}
            columns = table_payload.get("columns")
            if isinstance(columns, dict):
                for column_name, column_payload in columns.items():
                    if not isinstance(column_name, str) or not isinstance(column_payload, dict):
                        continue
                    compact_column = {
                        "semantic": str(column_payload.get("semantic", "")),
                        "type_hint": str(column_payload.get("type_hint", "unknown")),
                        "notes": str(column_payload.get("notes", "")),
                    }
                    compact_columns[column_name] = compact_column
            compact_tables[table_name] = {
                "description": str(table_payload.get("description", "")),
                "columns": compact_columns,
            }

    compact_join_hints: list[dict[str, str]] = []
    join_hints = catalog.get("join_hints")
    if isinstance(join_hints, list):
        for hint in join_hints:
            if not isinstance(hint, dict):
                continue
            compact_join_hints.append(
                {
                    "left_table": str(hint.get("left_table", "")),
                    "left_column": str(hint.get("left_column", "")),
                    "right_table": str(hint.get("right_table", "")),
                    "right_column": str(hint.get("right_column", "")),
                    "confidence": str(hint.get("confidence", "")),
                }
            )

    return {
        "tables": compact_tables,
        "join_hints": compact_join_hints,
        "task_relevant_fields": _normalize_string_list(catalog.get("task_relevant_fields")),
        "warnings": _normalize_string_list(catalog.get("warnings")),
    }


def _get_case_insensitive_mapping(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    mapping: dict[str, Any] = {}
    for key, value in payload.items():
        if isinstance(key, str):
            mapping[key.lower()] = value
    return mapping


def _align_catalog_with_schema(
    catalog: dict[str, Any],
    engine_schema: dict[str, Any],
) -> dict[str, Any]:
    schema_tables = engine_schema.get("tables")
    if not isinstance(schema_tables, dict):
        return catalog

    raw_tables = _get_case_insensitive_mapping(catalog.get("tables"))
    aligned_tables: dict[str, Any] = {}
    valid_columns_by_table: dict[str, set[str]] = {}

    for table_name, schema_payload in schema_tables.items():
        if not isinstance(table_name, str) or not isinstance(schema_payload, dict):
            continue

        raw_table_payload = raw_tables.get(table_name.lower(), {})
        if not isinstance(raw_table_payload, dict):
            raw_table_payload = {}
        raw_columns = _get_case_insensitive_mapping(raw_table_payload.get("columns"))

        aligned_columns: dict[str, Any] = {}
        schema_columns = schema_payload.get("columns")
        if isinstance(schema_columns, list):
            for column_name in schema_columns:
                if not isinstance(column_name, str):
                    continue
                raw_column_payload = raw_columns.get(column_name.lower(), {})
                if not isinstance(raw_column_payload, dict):
                    raw_column_payload = {}
                aligned_columns[column_name] = {
                    "semantic": str(raw_column_payload.get("semantic", "")),
                    "type_hint": str(raw_column_payload.get("type_hint", "unknown")),
                    "notes": str(raw_column_payload.get("notes", "")),
                }

        aligned_tables[table_name] = {
            "description": str(raw_table_payload.get("description", "")),
            "columns": aligned_columns,
        }
        valid_columns_by_table[table_name] = set(aligned_columns)

    aligned_join_hints: list[dict[str, str]] = []
    raw_join_hints = catalog.get("join_hints")
    if isinstance(raw_join_hints, list):
        for hint in raw_join_hints:
            if not isinstance(hint, dict):
                continue
            left_table = str(hint.get("left_table", ""))
            left_column = str(hint.get("left_column", ""))
            right_table = str(hint.get("right_table", ""))
            right_column = str(hint.get("right_column", ""))
            if (
                left_table in valid_columns_by_table
                and right_table in valid_columns_by_table
                and left_column in valid_columns_by_table[left_table]
                and right_column in valid_columns_by_table[right_table]
            ):
                aligned_join_hints.append(
                    {
                        "left_table": left_table,
                        "left_column": left_column,
                        "right_table": right_table,
                        "right_column": right_column,
                        "confidence": str(hint.get("confidence", "")),
                    }
                )

    aligned_task_relevant_fields: list[str] = []
    for field_ref in _normalize_string_list(catalog.get("task_relevant_fields")):
        if "." not in field_ref:
            continue
        table_name, column_name = field_ref.split(".", 1)
        if column_name in valid_columns_by_table.get(table_name, set()):
            aligned_task_relevant_fields.append(field_ref)

    return {
        "tables": aligned_tables,
        "join_hints": aligned_join_hints,
        "task_relevant_fields": aligned_task_relevant_fields,
        "warnings": _normalize_string_list(catalog.get("warnings")),
    }


def _build_focused_schema(
    engine_schema: dict[str, Any],
    plan: dict[str, Any],
    catalog: dict[str, Any],
) -> dict[str, Any] | None:
    tables_payload = engine_schema.get("tables")
    if not isinstance(tables_payload, dict) or len(tables_payload) <= 1:
        return None

    relevant_tables = set(_normalize_string_list(plan.get("source_tables")))
    relevant_tables.update(_extract_table_names(plan))
    relevant_tables.update(_extract_table_names(catalog.get("task_relevant_fields", [])))
    if not relevant_tables:
        return None

    focused_tables = {
        name: value for name, value in tables_payload.items() if name in relevant_tables
    }
    if not focused_tables or len(focused_tables) >= len(tables_payload):
        return None

    return {
        "table_count": len(focused_tables),
        "tables": focused_tables,
    }


def _classify_sql_issue(
    *,
    ok: bool,
    is_final: bool,
    result: dict[str, Any] | None,
    error_message: str | None,
) -> str:
    if ok:
        row_count = result.get("row_count") if isinstance(result, dict) else None
        if row_count == 0:
            return "empty_result"
        return "final_result_ready" if is_final else "partial_success"

    message = (error_message or "").lower()
    if "column" in message:
        return "unknown_column"
    if "table" in message or "catalog" in message:
        return "unknown_table"
    if "group by" in message or "aggregate" in message:
        return "group_or_aggregation_error"
    if "parser" in message or "syntax" in message:
        return "syntax_error"
    if "type" in message or "cast" in message:
        return "type_mismatch"
    return "execution_error"


def _summarize_sql_attempt(
    *,
    sql: str | None,
    ok: bool,
    is_final: bool,
    result: dict[str, Any] | None = None,
    error_message: str | None = None,
) -> dict[str, Any]:
    data = _extract_result_data(result)
    columns = _normalize_string_list(data.get("columns")) if data else []
    rows = data.get("rows") if data else []
    preview_rows = rows[:SQL_PREVIEW_ROWS] if isinstance(rows, list) else []
    summary = {
        "sql": sql,
        "ok": ok,
        "is_final": is_final,
        "issue_hint": _classify_sql_issue(
            ok=ok,
            is_final=is_final,
            result=result,
            error_message=error_message,
        ),
    }
    if columns:
        summary["returned_columns"] = columns
    if preview_rows:
        summary["sample_rows_preview"] = preview_rows
    if isinstance(result, dict):
        row_count = result.get("row_count")
        if isinstance(row_count, int):
            summary["row_count"] = row_count
        truncated = result.get("truncated")
        if isinstance(truncated, bool):
            summary["truncated"] = truncated
    short_error = _shorten_text(error_message, max_chars=SQL_ERROR_CHARS)
    if short_error is not None:
        summary["error_message_short"] = short_error
    return summary


def _build_answer_from_result(
    result: dict[str, Any],
    plan: dict[str, Any],
) -> AnswerTable | None:
    data = _extract_result_data(result)
    if data is None:
        return None

    raw_columns = data.get("columns")
    raw_rows = data.get("rows")
    if not isinstance(raw_columns, list) or not isinstance(raw_rows, list):
        return None

    rows: list[list[Any]] = []
    for row in raw_rows:
        if not isinstance(row, list):
            return None
        rows.append(list(row))

    result_columns = [str(column) for column in raw_columns]
    planned_columns = _normalize_string_list(plan.get("final_columns"))
    columns = planned_columns if len(planned_columns) == len(result_columns) else result_columns
    return AnswerTable(columns=columns, rows=rows)


class ReActAgent:
    def __init__(
        self,
        *,
        model: ModelAdapter,
        tools: ToolRegistry | None = None,
        config: ReActAgentConfig | None = None,
        system_prompt: str | None = None,
        trace_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.model = model
        self.tools = tools
        self.config = config or ReActAgentConfig()
        self.system_prompt = system_prompt or BASE_SYSTEM_PROMPT
        self.trace_callback = trace_callback

    def _build_messages(self, user_content: str) -> list[ModelMessage]:
        system_content = build_system_prompt(
            "",
            system_prompt=self.system_prompt,
        )
        return [
            ModelMessage(role="system", content=system_content),
            ModelMessage(role="user", content=user_content),
        ]

    def _complete_json(self, user_content: str) -> tuple[str, dict[str, Any]]:
        raw_response = self.model.complete(self._build_messages(user_content))
        return raw_response, parse_model_payload(raw_response)

    def _task_record_payload(self, task: PublicTask) -> dict[str, Any]:
        task_json_path = task.task_dir / "task.json"
        source_path = task_json_path
        if source_path.exists():
            payload = json.loads(source_path.read_text())
            if isinstance(payload, dict):
                return payload
        return {
            "task_id": task.task_id,
            "difficulty": task.difficulty,
            "question": task.question,
        }

    def _read_markdown_sections(self, task: PublicTask, paths: list[Path]) -> tuple[list[str], str]:
        relative_paths = []
        sections = []
        for path in paths:
            if not path.exists() or not path.is_file():
                continue
            content = path.read_text(errors="replace").strip()
            if not content:
                continue
            relative_path = path.relative_to(task.context_dir).as_posix()
            relative_paths.append(relative_path)
            sections.append(f"[{relative_path}]\n{content}")
        return relative_paths, "\n\n".join(sections)

    def _catalog_knowledge_text(self, task: PublicTask) -> str:
        _, text = self._read_markdown_sections(task, [task.context_dir / "knowledge.md"])
        return text

    def _plan_doc_bundle(self, task: PublicTask) -> tuple[list[str], str]:
        doc_dir = task.context_dir / "doc"
        if not doc_dir.exists():
            return [], ""
        markdown_paths = sorted(path for path in doc_dir.rglob("*.md") if path.is_file())
        return self._read_markdown_sections(task, markdown_paths)

    def _append_step(
        self,
        task_id: str,
        state: AgentRuntimeState,
        *,
        phase: str,
        thought: str,
        action: str,
        action_input: dict[str, Any],
        raw_response: str,
        observation: dict[str, Any],
        ok: bool,
    ) -> None:
        state.steps.append(
            StepRecord(
                step_index=len(state.steps) + 1,
                thought=thought,
                action=action,
                action_input=action_input,
                raw_response=raw_response,
                observation=observation,
                ok=ok,
                phase=phase,
            )
        )
        self._emit_trace(task_id, state, status="running")

    def _emit_trace(self, task_id: str, state: AgentRuntimeState, *, status: str) -> None:
        if self.trace_callback is None:
            return
        self.trace_callback(
            build_trace_payload(
                task_id=task_id,
                state=state,
                status=status,
            )
        )

    def _load_data(self, task: PublicTask, state: AgentRuntimeState) -> DataEngine:
        engine = DataEngine()
        loaded_data = engine.register_context_dir(task.context_dir)
        state.loaded_data = loaded_data
        ok = bool(loaded_data.get("table_count"))
        self._append_step(
            task.task_id,
            state,
            phase="load_data",
            thought="Loaded supported context files into DataEngine.",
            action="load_data",
            action_input={"context_dir": str(task.context_dir)},
            raw_response="",
            observation={
                "ok": ok,
                "content": loaded_data,
            },
            ok=ok,
        )
        if not ok:
            raise RuntimeError("No supported data files were loaded from context.")
        return engine

    def _generate_catalog(
        self,
        task: PublicTask,
        state: AgentRuntimeState,
        engine_schema: dict[str, Any],
    ) -> dict[str, Any]:
        raw_response, payload = self._complete_json(
            build_catalog_prompt(
                task,
                task_record=self._task_record_payload(task),
                schema_knowledge_text=self._catalog_knowledge_text(task),
                engine_schema=engine_schema,
            )
        )
        catalog = payload.get("catalog")
        if not isinstance(catalog, dict):
            raise ValueError("Catalog stage response must contain a catalog object.")
        compact_catalog = _compact_catalog(_align_catalog_with_schema(catalog, engine_schema))
        state.catalog = compact_catalog
        self._append_step(
            task.task_id,
            state,
            phase="catalog",
            thought=str(payload.get("thought", "")),
            action="generate_catalog",
            action_input={},
            raw_response=raw_response,
            observation={
                "ok": True,
                "content": {
                    "catalog": compact_catalog,
                },
            },
            ok=True,
        )
        return compact_catalog

    def _generate_plan(
        self,
        task: PublicTask,
        state: AgentRuntimeState,
        catalog: dict[str, Any],
        engine_schema: dict[str, Any],
    ) -> dict[str, Any]:
        plan_doc_files, plan_doc_text = self._plan_doc_bundle(task)
        raw_response, payload = self._complete_json(
            build_plan_prompt(
                task,
                catalog=catalog,
                engine_schema=engine_schema,
                plan_doc_text=plan_doc_text,
            )
        )
        plan = payload.get("plan")
        if not isinstance(plan, dict):
            raise ValueError("Plan stage response must contain a plan object.")
        state.plan = plan
        state.focused_schema = _build_focused_schema(engine_schema, plan, catalog)
        self._append_step(
            task.task_id,
            state,
            phase="plan",
            thought=str(payload.get("thought", "")),
            action="generate_plan",
            action_input={},
            raw_response=raw_response,
            observation={
                "ok": True,
                "content": {
                    "plan_doc_files": plan_doc_files,
                    "plan": plan,
                    "focused_schema": state.focused_schema,
                },
            },
            ok=True,
        )
        return plan

    def _run_nl2sql_loop(
        self,
        task: PublicTask,
        state: AgentRuntimeState,
        engine: DataEngine,
        catalog: dict[str, Any],
        plan: dict[str, Any],
        engine_schema: dict[str, Any],
    ) -> None:
        tools = create_dataengine_tool_registry(
            engine,
            generated_catalog=catalog,
            sql_result_limit=self.config.sql_result_limit,
            schema_sample_rows=self.config.catalog_sample_rows,
        )
        max_attempts = self.config.max_sql_attempts if self.config.max_sql_attempts > 0 else self.config.max_steps
        last_successful_sql: str | None = None
        last_successful_result: dict[str, Any] | None = None
        for _ in range(max_attempts):
            recent_attempts = state.sql_attempts[-SQL_HISTORY_WINDOW:]
            raw_response, payload = self._complete_json(
                build_nl2sql_prompt(
                    task,
                    plan=_compact_plan(plan),
                    engine_schema=engine_schema,
                    focused_schema=state.focused_schema,
                    recent_attempts=recent_attempts,
                    tool_descriptions=tools.describe_for_prompt(["execute_dataengine_sql"]),
                    sql_result_limit=self.config.sql_result_limit,
                )
            )
            try:
                model_step = parse_model_step(raw_response)
                if model_step.action != "execute_dataengine_sql":
                    raise ValueError("plan2sql stage must call execute_dataengine_sql.")
                tool_result = tools.execute(task, model_step.action, model_step.action_input)
                observation = {
                    "ok": tool_result.ok,
                    "tool": model_step.action,
                    "content": tool_result.content,
                }
                attempt = _summarize_sql_attempt(
                    sql=str(model_step.action_input.get("sql", "")),
                    ok=tool_result.ok,
                    is_final=bool(payload.get("is_final")),
                    result=tool_result.content,
                )
                state.sql_attempts.append(attempt)
                self._append_step(
                    task.task_id,
                    state,
                    phase="plan2sql",
                    thought=model_step.thought,
                    action=model_step.action,
                    action_input=model_step.action_input,
                    raw_response=raw_response,
                    observation=observation,
                    ok=tool_result.ok,
                )
                if tool_result.ok:
                    last_successful_sql = str(model_step.action_input.get("sql", ""))
                    last_successful_result = tool_result.content
                if tool_result.ok and bool(payload.get("is_final")):
                    state.final_sql = last_successful_sql
                    state.final_sql_result = last_successful_result
                    self._emit_trace(task.task_id, state, status="running")
                    return
            except Exception as exc:
                sql_value = None
                if isinstance(payload.get("action_input"), dict):
                    sql_value = payload["action_input"].get("sql")
                observation = {
                    "ok": False,
                    "error": str(exc),
                }
                state.sql_attempts.append(
                    _summarize_sql_attempt(
                        sql=str(sql_value) if sql_value is not None else None,
                        ok=False,
                        is_final=bool(payload.get("is_final")),
                        error_message=str(exc),
                    )
                )
                self._append_step(
                    task.task_id,
                    state,
                    phase="plan2sql",
                    thought=str(payload.get("thought", "")),
                    action="__error__",
                    action_input={},
                    raw_response=raw_response,
                    observation=observation,
                    ok=False,
                )
        if last_successful_sql is not None and last_successful_result is not None:
            state.final_sql = last_successful_sql
            state.final_sql_result = last_successful_result
            self._emit_trace(task.task_id, state, status="running")
            return
        raise RuntimeError("Agent did not produce a successful SQL query within max_sql_attempts.")

    def _generate_answer(
        self,
        task: PublicTask,
        state: AgentRuntimeState,
        engine: DataEngine,
        catalog: dict[str, Any],
        plan: dict[str, Any],
    ) -> None:
        if state.final_sql is None or state.final_sql_result is None:
            raise RuntimeError("Answer stage requires a final SQL result.")

        direct_answer = _build_answer_from_result(state.final_sql_result, plan)
        if direct_answer is not None:
            state.answer = direct_answer
            self._append_step(
                task.task_id,
                state,
                phase="answer",
                thought="Directly converted final SQL result into the answer table.",
                action="answer_direct",
                action_input={
                    "columns": direct_answer.columns,
                    "row_count": len(direct_answer.rows),
                },
                raw_response="",
                observation={
                    "ok": True,
                    "content": {
                        "column_count": len(direct_answer.columns),
                        "row_count": len(direct_answer.rows),
                    },
                },
                ok=True,
            )
            return

        tools = create_dataengine_tool_registry(
            engine,
            generated_catalog=catalog,
            sql_result_limit=self.config.sql_result_limit,
            schema_sample_rows=self.config.catalog_sample_rows,
        )
        raw_response = self.model.complete(
            self._build_messages(
                build_answer_prompt(
                    task,
                    plan=_compact_plan(plan),
                    final_sql=state.final_sql,
                    final_sql_result=state.final_sql_result,
                    tool_descriptions=tools.describe_for_prompt(["answer"]),
                )
            )
        )
        model_step = parse_model_step(raw_response)
        if model_step.action != "answer":
            raise ValueError("Answer stage must call answer.")
        tool_result = tools.execute(task, model_step.action, model_step.action_input)
        observation = {
            "ok": tool_result.ok,
            "tool": model_step.action,
            "content": tool_result.content,
        }
        self._append_step(
            task.task_id,
            state,
            phase="answer",
            thought=model_step.thought,
            action=model_step.action,
            action_input=model_step.action_input,
            raw_response=raw_response,
            observation=observation,
            ok=tool_result.ok,
        )
        if tool_result.is_terminal:
            state.answer = tool_result.answer

    def run(self, task: PublicTask) -> AgentRunResult:
        state = AgentRuntimeState()
        try:
            engine = self._load_data(task, state)
            engine_schema = engine.describe_schema(sample_rows=self.config.catalog_sample_rows)
            state.engine_schema = engine_schema
            catalog = self._generate_catalog(task, state, engine_schema)
            plan = self._generate_plan(task, state, catalog, engine_schema)
            self._run_nl2sql_loop(task, state, engine, catalog, plan, engine_schema)
            self._generate_answer(task, state, engine, catalog, plan)
        except Exception as exc:
            state.failure_reason = str(exc)
            self._append_step(
                task.task_id,
                state,
                phase="error",
                thought="",
                action="__error__",
                action_input={},
                raw_response="",
                observation={
                    "ok": False,
                    "error": str(exc),
                },
                ok=False,
            )

        if state.answer is None and state.failure_reason is None:
            state.failure_reason = "Agent did not submit an answer."
            self._emit_trace(task.task_id, state, status="failed")

        final_status = "failed" if state.failure_reason is not None else "completed"
        self._emit_trace(task.task_id, state, status=final_status)

        return AgentRunResult(
            task_id=task.task_id,
            answer=state.answer,
            steps=list(state.steps),
            failure_reason=state.failure_reason,
            final_sql=state.final_sql,
            status=final_status,
        )
