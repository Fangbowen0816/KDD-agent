import duckdb
import os
import pandas as pd
import hashlib
import sqlglot
from sqlglot import exp

class DataEngine:
    
    def __init__(self, db_path=":memory:"):
        self.conn = duckdb.connect(db_path)
        self.db_path = db_path
        
        self.tables = set()
        self.sqlite_load = False
        
        self.type_mapping = {
            "csv": self._register_csv,
            "json": self._register_json,
            "db": self._register_sqlite,
            "sqlite": self._register_sqlite
        }
        
        self.catalog = {}
        
        
    def register(self, file_path):
        
        try:
            file_path = os.path.abspath(file_path)
            file_type = self._detect_type(file_path)
            
            semantic = self._make_semantic_name(file_path)
            namespace = self._make_namespace(file_path)
            
            table_name = self._make_internal_name(semantic, namespace)
          
            match file_type:
                case "csv":
                    self._register_csv(semantic, table_name, file_path)
                case "json":
                    self._register_json(semantic, table_name, file_path)
                case "sqlite":
                    self._register_sqlite(semantic, table_name, file_path, namespace)
                case _:
                    raise ValueError(f"Unsupported file type: {file_type}")
            
            return {
                "success": True,
                "table": semantic
            }
                
        except FileNotFoundError:
            print(f"[ERROR] File not found: {file_path}")
            return {
                "success": False,
                "error": f"FileNotFoundError"
            }

        except Exception as e:
            print(f"[ERROR] register failed: {e}")
            return {
                "success": False,
                "error": e
            }
            
        
        
    
    def query(self, sql):
        try:
            sql = self.rewrite_sql(sql)
            df = self.conn.execute(sql).fetchdf().head(1000)
            return {
                "success": True,
                "data": {
                    "columns": df.columns.tolist(),
                    "rows": df.values.tolist()
                },
                "error": None
            }
        except Exception as e:
            return {
                "success": False,
                "data": None,
                "error": str(e)
            }


    
    def show_tables(self):
        return {
            "count": len(self.tables),
            "tables": sorted(list(self.tables))
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
        
    def _register_csv(self, semantic, table_name, file_path):
        try:
            sql = f"""
            CREATE OR REPLACE VIEW {table_name} AS
            SELECT * FROM read_csv_auto('{file_path}');
            """
            self.conn.execute(sql)
            self.tables.add(semantic)
            columns = self._get_columns(table_name)
            
            self.catalog[semantic.lower()] = {
                "internal_name": table_name,
                "source_type": "csv",
                "columns": columns,
                "_meta": {
                    "file_path": file_path
                }
            }
        
        except Exception as e:
            print(f"[CSV REGISTER ERROR] {e}")
        
    def _register_json(self, semantic, table_name, file_path):
        try:
            sql = f"""
            CREATE OR REPLACE VIEW {table_name} AS
            SELECT * FROM read_json_auto('{file_path}');
            """
            self.conn.execute(sql)
            self.tables.add(semantic)
            columns = self._get_columns(table_name)
            
            self.catalog[semantic.lower()] = {
                "internal_name": table_name,
                "source_type": "json",
                "columns": columns,
                "_meta": {
                    "file_path": file_path
                }
            }
        
        except Exception as e:
            print(f"[JSON REGISTER ERROR] {e}")
        
    def _register_sqlite(self, semantic, table_name, file_path, namespace):
        try:
            if not self.sqlite_load:
                self.conn.execute("INSTALL sqlite;")
                self.conn.execute("LOAD sqlite;")
                self.sqlite_load = True
            
            alias = f"db_{namespace}"
                
            self.conn.execute(
                f"""
                ATTACH IF NOT EXISTS DATABASE '{file_path}' AS {alias} (TYPE SQLITE);
                """
            )
            
            dbtables = self.conn.execute(
                f"""
                SELECT name FROM {alias}.sqlite_master WHERE type='table';
                """
            ).fetchall()
            
            for (t,) in dbtables:
                view_name = f"{semantic}_{t}__{namespace}"
                sql = f"""
                CREATE OR REPLACE VIEW {view_name} AS
                SELECT * FROM {alias}.{t};
                """
                self.conn.execute(sql)
                self.tables.add(f"{semantic}_{t}")
                columns = self._get_columns(view_name)
                self.catalog[f"{semantic}_{t}".lower()] = {
                    "source_type": "sqlite",
                    "internal_name": view_name,
                    "columns": columns,
                    "_meta": {
                        "file_path": file_path
                    }
                }
                
        
        except Exception as e:
            print(f"[SQLITE REGISTER ERROR] {e}")    
            
        
    
    def _make_semantic_name(self, file_path):
        base = os.path.splitext(os.path.basename(file_path))[0]
        return base.replace("-", "_").replace(" ", "_")
    
    def _make_namespace(self, file_path):
        h = hashlib.md5(file_path.encode()).hexdigest()[:6]
        return h
    
    def _make_internal_name(self, semantic, namespace):
        return f"{semantic}__{namespace}"
    

    def rewrite_sql(self, sql: str) -> str:
        try:
            tree = sqlglot.parse_one(sql, read="duckdb")

        except Exception as e:
            raise ValueError(f"SQL parse failed: {e}")

        catalog_lower = {
            k.lower(): v for k, v in self.catalog.items()
        }
        cte_names = set()
        for cte in tree.find_all(exp.CTE):
            if cte.alias:
                cte_names.add(cte.alias.lower())

        for table in tree.find_all(exp.Table):
            raw_name = table.name.lower()
            if raw_name in cte_names:
                continue
            if isinstance(table.parent, exp.Subquery):
                continue


            # catalog match
            if raw_name in catalog_lower:
                internal_name = catalog_lower[raw_name]["internal_name"]
                table.set("this", exp.to_identifier(internal_name))
                table.set("db", None)
                
        return tree.sql(dialect="duckdb")
    
    
    def _get_columns(self, table_name: str):
        try:
            result = self.conn.execute(f"DESCRIBE {table_name}").fetchall()
                    
            columns = [row[0] for row in result]
            return columns

        except Exception as e:
            print(f"[SCHEMA ERROR] {e}")
            return []       