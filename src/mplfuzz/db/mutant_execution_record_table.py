# mutant_execution_record_table.py
from typing import List
from mplfuzz.db.base import get_db_cursor
from mplfuzz.models import MutantExecution
from mplfuzz.utils.result import Err, Ok, Result, resultify


# 创建 mutant_execution 表
with get_db_cursor() as cur:
    cur.execute(
        """CREATE TABLE IF NOT EXISTS mutant_execution (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mutant_id TEXT,
            library_name TEXT,
            api_name TEXT,
            result_type INTEGER,
            ret_code INTEGER,
            stdout TEXT,
            stderr TEXT
        )"""
    )

@resultify
def create_mutant_execution(mutant_execution: MutantExecution) -> Result[None, Exception]:
    with get_db_cursor() as cur:
        cur.execute(
            """INSERT INTO mutant_execution (
                mutant_id,
                library_name,
                api_name,
                result_type,
                ret_code,
                stdout,
                stderr
            ) VALUES (?, ?, ?, ?, ?)""",
            (
                mutant_execution.mutant_id,
                mutant_execution.library_name,
                mutant_execution.api_name,
                mutant_execution.result_type,
                mutant_execution.ret_code,
                mutant_execution.stdout,
                mutant_execution.stderr,
            ),
        )
    return Ok(None)


@resultify
def get_mutant_execution(mutant_id: str) -> Result[MutantExecution | None, Exception]:
    with get_db_cursor() as cur:
        cur.execute(
            "SELECT * FROM mutant_execution WHERE mutant_id = ?", (mutant_id,)
        )
        row = cur.fetchone()
        if row:
            return Ok(
                MutantExecution(
                    mutant_id=row[0],
                    library_name=row[1],
                    api_name=row[2],
                    result_type=row[3],
                    ret_code=row[4],
                    stdout=row[5],
                    stderr=row[6]
                )
            )
        else:
            return Ok(None)


@resultify
def get_mutant_executions() -> Result[List[MutantExecution], Exception]:
    with get_db_cursor() as cur:
        cur.execute("SELECT * FROM mutant_execution")
        rows = cur.fetchall()
        return Ok(
            [
                MutantExecution(
                    mutant_id=row[0],
                    library_name=row[1],
                    api_name=row[2],
                    result_type=row[3],
                    ret_code=row[4],
                    stdout=row[5],
                    stderr=row[6]
                )
                for row in rows
            ]
        )