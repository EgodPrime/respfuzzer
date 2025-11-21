import json
from typing import Iterator, List, Optional

from respfuzzer.models import Function
from respfuzzer.repos.base import get_db_cursor

with get_db_cursor() as cur:
    cur.execute(
        """CREATE TABLE IF NOT EXISTS function (
            id SERIAL PRIMARY KEY,
            func_name TEXT, 
            library_name TEXT,
            source TEXT, 
            args TEXT, 
            ret_type TEXT,
            is_builtin INTEGER DEFAULT 0
        )"""
    )


def create_function(function: Function) -> Optional[int]:
    args_text = json.dumps([arg.model_dump() for arg in function.args])
    with get_db_cursor() as cur:
        cur.execute(
            """INSERT INTO function (func_name, library_name, source, args, ret_type, is_builtin)
               VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
            (
                function.func_name,
                function.library_name,
                function.source,
                args_text,
                function.ret_type,
                function.is_builtin,
            ),
        )
        row = cur.fetchone()
        return row[0] if row is not None else None


def get_function(func_name: str) -> Optional[Function]:
    with get_db_cursor() as cur:
        cur.execute("SELECT * FROM function WHERE func_name = %s", (func_name,))
        row = cur.fetchone()
        if row:
            return Function(
                id=row[0],
                func_name=row[1],
                library_name=row[2],
                source=row[3],
                args=json.loads(row[4]),
                ret_type=row[5],
                is_builtin=row[6],
            )
        else:
            return None


def get_functions(library_name: str | None) -> List[Function]:
    if library_name:
        sql = "SELECT * FROM function WHERE func_name LIKE %s"
        params = (f"{library_name}.%",)
    else:
        sql = "SELECT * FROM function"
        params = ()
    with get_db_cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
        function_list = [
            Function(
                id=row[0],
                func_name=row[1],
                library_name=row[2],
                source=row[3],
                args=json.loads(row[4]),
                ret_type=row[5],
                is_builtin=row[6],
            )
            for row in rows
        ]
        return function_list


def get_function_iter(library_name: Optional[str]) -> Iterator[Function]:
    if library_name:
        sql = "SELECT * FROM function WHERE func_name LIKE %s"
        params = (f"{library_name}.%",)
    else:
        sql = "SELECT * FROM function"
        params = ()
    with get_db_cursor() as cur:
        cur.execute(sql, params)
        for row in cur:
            yield Function(
                id=row[0],
                func_name=row[1],
                library_name=row[2],
                source=row[3],
                args=json.loads(row[4]),
                ret_type=row[5],
                is_builtin=row[6],
            )
