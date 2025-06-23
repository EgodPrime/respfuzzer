from typing import List, Optional
from mplfuzz.db.base import get_db_cursor
from mplfuzz.utils.result import Err, Ok, Result, resultify
from mplfuzz.models import MutantExecution


# 创建表
with get_db_cursor() as cur:
    cur.execute(
        """CREATE TABLE IF NOT EXISTS mutant_execution (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mutant_id INTEGER,
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
            """INSERT INTO mutant_execution 
               (mutant_id, library_name, api_name, result_type, ret_code, stdout, stderr) 
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
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
def get_mutant_execution(execution_id: int) -> Result[MutantExecution | None, Exception]:
    with get_db_cursor() as cur:
        cur.execute(
            "SELECT * FROM mutant_execution WHERE id = ?", (execution_id,)
        )
        row = cur.fetchone()
        if row:
            return MutantExecution(
                mutant_id=row[1],
                library_name=row[2],
                api_name=row[3],
                result_type=row[4],
                ret_code=row[5],
                stdout=row[6],
                stderr=row[7],
            )
        else:
            return None


@resultify
def get_mutant_executions(mutant_id: Optional[int] = None) -> Result[List[MutantExecution], Exception]:
    with get_db_cursor() as cur:
        if mutant_id is not None:
            cur.execute(
                "SELECT * FROM mutant_execution WHERE mutant_id = ?", (mutant_id,)
            )
        else:
            cur.execute("SELECT * FROM mutant_execution")
        rows = cur.fetchall()
        executions = [
            MutantExecution(
                mutant_id=row[1],
                library_name=row[2],
                api_name=row[3],
                result_type=row[4],
                ret_code=row[5],
                stdout=row[6],
                stderr=row[7],
            )
            for row in rows
        ]
    return Ok(executions)