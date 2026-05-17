import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

import duckdb
import sqlglot
from sqlglot import exp


DEFAULT_MAX_LOAD_FILE_BYTES = 512 * 1024 * 1024


class DataEngine:

    def __init__(self, db_path=":memory:", max_load_file_bytes=DEFAULT_MAX_LOAD_FILE_BYTES):
        self.conn = duckdb.connect(db_path)
        self.db_path = db_path
        self.max_load_file_bytes = max_load_file_bytes

        self.tables = set()
        self.sqlite_load = False
        self.catalog = {}

    def register(self, file_path):
        try:
            file_path = os.path.abspath(file_path)
            file_size_bytes = self._validate_load_file_size(file_path)
            file_type = self._detect_type(file_path)

            if file_type == "csv":
                table_name = self._reserve_table_name(self._make_table_name(file_path))
                self._register_csv(table_name, file_path)
            elif file_type == "json":
                table_name = self._reserve_table_name(self._make_table_name(file_path))
                self._register_json(table_name, file_path)
            else:
                table_name = self._make_table_name(file_path)
                self._register_sqlite(file_path)
            
            return {
                "success": True,
                "table": table_name,
                "source_path": file_path,
                "source_type": file_type,
                "file_size_bytes": file_size_bytes,
            }

        except FileNotFoundError:
            print(f"[ERROR] File not found: {file_path}")
            return {
                "success": False,
                "source_path": str(file_path),
                "error": "FileNotFoundError",
            }

        except Exception as e:
            print(f"[ERROR] register failed: {e}")
            return {
                "success": False,
                "source_path": str(file_path),
                "error": str(e),
            }

    def register_context_dir(self, context_dir: str | Path) -> dict[str, Any]:
        context_root = Path(context_dir).resolve()
        supported_suffixes = {".csv", ".json", ".db", ".sqlite"}
        metadata_filenames = {"task.json"}
        loaded_files = []
        failed_files = []
        for path in sorted(context_root.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in supported_suffixes:
                continue
            if path.name.lower() in metadata_filenames:
                continue
            result = self.register(path)
            relative_path = path.relative_to(context_root).as_posix()
            if result.get("success"):
                source_tables = self._tables_for_source_path(path)
                loaded_files.append(
                    {
                        "path": relative_path,
                        "table": result.get("table"),
                        "tables": source_tables,
                        "source_type": result.get("source_type"),
                        "file_size_bytes": result.get("file_size_bytes"),
                    }
                )
            else:
                failed_files.append(
                    {
                        "path": relative_path,
                        "error": result.get("error"),
                    }
                )
        return {
            "success": not failed_files,
            "context_dir": str(context_root),
            "loaded_files": loaded_files,
            "failed_files": failed_files,
            "table_count": len(self.tables),
            "max_load_file_bytes": self.max_load_file_bytes,
        }

    def query(self, sql, limit: int = 200):
        try:
            self._validate_read_only_sql(sql)
            limited_sql = self._apply_limit(sql, limit + 1)
            df = self.conn.execute(limited_sql).fetchdf()
            truncated = len(df.index) > limit
            if truncated:
                df = df.head(limit)
            return {
                "success": True,
                "data": {
                    "columns": df.columns.tolist(),
                    "rows": [
                        [self._json_safe_value(value) for value in row]
                        for row in df.values.tolist()
                    ],
                },
                "row_count": len(df.index),
                "truncated": truncated,
                "sql": sql,
                "rewritten_sql": sql,
                "error": None,
            }
        except Exception as e:
            return {
                "success": False,
                "data": None,
                "row_count": 0,
                "truncated": False,
                "sql": sql,
                "rewritten_sql": None,
                "error": str(e),
            }

    def show_tables(self):
        return {
            "count": len(self.tables),
            "tables": sorted(list(self.tables)),
        }

    def describe_schema(self, sample_rows: int = 3) -> dict[str, Any]:
        tables: dict[str, Any] = {}
        for table_name in sorted(self.catalog):
            entry = self.catalog[table_name]
            sample = self._sample_rows(table_name, sample_rows)
            tables[table_name] = {
                "source_type": entry["source_type"],
                "columns": list(entry["columns"]),
                "sample_rows": sample,
                "_meta": dict(entry.get("_meta", {})),
            }
        return {
            "table_count": len(tables),
            "tables": tables,
        }

    def _detect_type(self, file_path):
        # get extension
        ext = file_path.split(".")[-1].lower()

        match ext:
            case "csv":
                return "csv"
            case "json":
                return "json"
            case "db":
                return "sqlite"
            case "sqlite":
                return "sqlite"
            case _:
                raise ValueError(f"Unsupported file type: {ext}")

    def _validate_load_file_size(self, file_path):
        file_size_bytes = os.path.getsize(file_path)
        if (
            self.max_load_file_bytes is not None
            and file_size_bytes > self.max_load_file_bytes
        ):
            size_mb = file_size_bytes / 1024 / 1024
            limit_mb = self.max_load_file_bytes / 1024 / 1024
            raise ValueError(
                f"File exceeds DataEngine load limit: {size_mb:.2f} MiB > "
                f"{limit_mb:.2f} MiB"
            )
        return file_size_bytes

    def _register_csv(self, table_name, file_path):
        try:
            sql = f"""
            CREATE OR REPLACE VIEW {table_name} AS
            SELECT * FROM read_csv_auto({self._quote_string(file_path)});
            """
            self.conn.execute(sql)
            self.tables.add(table_name)
            columns = self._get_columns(table_name)

            self.catalog[table_name] = {
                "source_type": "csv",
                "columns": columns,
                "_meta": {
                    "file_path": file_path,
                },
            }

        except Exception as e:
            print(f"[CSV REGISTER ERROR] {e}")
            raise

    def _register_json(self, table_name, file_path):
        try:
            wrapper_meta = self._detect_records_wrapper(file_path)
            read_options = self._json_read_options()
            if wrapper_meta is not None:
                sql = f"""
                CREATE OR REPLACE VIEW {table_name} AS
                SELECT r.*
                FROM read_json_auto(
                    {self._quote_string(file_path)}{read_options}
                ) AS src,
                     UNNEST(src.records) AS t(r);
                """
            else:
                sql = f"""
                CREATE OR REPLACE VIEW {table_name} AS
                SELECT * FROM read_json_auto(
                    {self._quote_string(file_path)}{read_options}
                );
                """
            self.conn.execute(sql)
            self.tables.add(table_name)
            columns = self._get_columns(table_name)

            self.catalog[table_name] = {
                "source_type": "json",
                "columns": columns,
                "_meta": {
                    "file_path": file_path,
                    **(wrapper_meta or {}),
                },
            }

        except Exception as e:
            print(f"[JSON REGISTER ERROR] {e}")
            raise

    def _json_read_options(self):
        if self.max_load_file_bytes is None:
            return ""
        return f", maximum_object_size={int(self.max_load_file_bytes)}"

    def _register_sqlite(self, file_path):
        try:
            if not self.sqlite_load:
                try:
                    self.conn.execute("LOAD sqlite;")
                except Exception:
                    self.conn.execute("INSTALL sqlite;")
                    self.conn.execute("LOAD sqlite;")
                self.sqlite_load = True

            alias_seed = hashlib.md5(str(file_path).encode()).hexdigest()[:6]
            alias_base = self._sanitize_identifier(os.path.splitext(os.path.basename(file_path))[0])
            alias = f"db_{alias_base}_{alias_seed}"

            self.conn.execute(
                f"""
                ATTACH IF NOT EXISTS DATABASE {self._quote_string(file_path)} AS {alias} (TYPE SQLITE);
                """
            )

            dbtables = self.conn.execute(
                f"""
                SELECT table_name
                FROM information_schema.tables
                WHERE table_catalog = {self._quote_string(alias)}
                  AND table_schema = 'main'
                ORDER BY table_name;
                """
            ).fetchall()

            for (source_table_name_raw,) in dbtables:
                source_table_name = self._sanitize_identifier(str(source_table_name_raw))
                table_name = self._reserve_table_name(source_table_name)
                sql = f"""
                CREATE OR REPLACE VIEW {table_name} AS
                SELECT * FROM {alias}.main.{self._quote_identifier(str(source_table_name_raw))};
                """
                self.conn.execute(sql)
                self.tables.add(table_name)
                columns = self._get_columns(table_name)
                self.catalog[table_name] = {
                    "source_type": "sqlite",
                    "columns": columns,
                    "_meta": {
                        "file_path": file_path,
                        "source_table_name": str(source_table_name_raw),
                    },
                }

        except Exception as e:
            print(f"[SQLITE REGISTER ERROR] {e}")
            raise

    def _make_table_name(self, file_path):
        base = os.path.splitext(os.path.basename(file_path))[0]
        return self._sanitize_identifier(base)

    def _detect_records_wrapper(self, file_path: str) -> dict[str, Any] | None:
        try:
            with open(file_path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except Exception:
            return None

        if not isinstance(payload, dict):
            return None

        records = payload.get("records")
        if not isinstance(records, list) or not records:
            return None
        if not all(isinstance(item, dict) for item in records):
            return None

        source_table_name = payload.get("table")
        return {
            "json_wrapper": "records",
            "source_table_name": str(source_table_name) if source_table_name is not None else None,
        }

    def _get_columns(self, table_name: str):
        try:
            result = self.conn.execute(f"DESCRIBE {table_name}").fetchall()

            columns = [row[0] for row in result]
            return columns

        except Exception as e:
            print(f"[SCHEMA ERROR] {e}")
            return []

    def _sample_rows(self, table_name: str, sample_rows: int) -> list[dict[str, Any]]:
        if sample_rows <= 0:
            return []
        try:
            df = self.conn.execute(f"SELECT * FROM {table_name} LIMIT {sample_rows}").fetchdf()
            rows = []
            for row in df.to_dict(orient="records"):
                rows.append({key: self._json_safe_value(value) for key, value in row.items()})
            return rows
        except Exception as e:
            print(f"[SAMPLE ERROR] {e}")
            return []

    def _validate_read_only_sql(self, sql: str) -> None:
        tree = sqlglot.parse_one(sql, read="duckdb")
        if not isinstance(tree, (exp.Select, exp.Union, exp.Except, exp.Intersect)):
            raise ValueError("Only read-only SELECT queries are allowed.")

    def _apply_limit(self, sql: str, limit: int) -> str:
        normalized_sql = sql.strip().rstrip(";")
        safe_limit = max(int(limit), 0)
        return f"SELECT * FROM ({normalized_sql}) AS _dataengine_limited LIMIT {safe_limit}"

    def _sanitize_identifier(self, raw_name: str) -> str:
        normalized = re.sub(r"\W+", "_", raw_name).strip("_").lower()
        if not normalized:
            normalized = "table"
        if normalized[0].isdigit():
            normalized = f"t_{normalized}"
        return normalized

    def _reserve_table_name(self, base_name: str) -> str:
        if base_name not in self.catalog:
            return base_name

        suffix = 2
        while f"{base_name}_{suffix}" in self.catalog:
            suffix += 1
        return f"{base_name}_{suffix}"

    def _quote_string(self, value: str) -> str:
        return "'" + value.replace("'", "''") + "'"

    def _quote_identifier(self, value: str) -> str:
        return '"' + value.replace('"', '""') + '"'

    def _json_safe_value(self, value: Any) -> Any:
        if value is None:
            return None
        if hasattr(value, "item"):
            try:
                value = value.item()
            except Exception:
                pass
        if isinstance(value, float) and value != value:
            return None
        if hasattr(value, "isoformat"):
            try:
                return value.isoformat()
            except Exception:
                pass
        if isinstance(value, (str, int, float, bool)):
            return value
        return str(value)

    def _tables_for_source_path(self, path: Path) -> list[str]:
        resolved_path = str(path.resolve())
        tables = []
        for table_name, entry in self.catalog.items():
            if entry.get("_meta", {}).get("file_path") == resolved_path:
                tables.append(table_name)
        return sorted(tables)
