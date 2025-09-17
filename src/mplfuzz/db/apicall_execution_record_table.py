import json
from typing import List, Optional

from mplfuzz.db.base import get_db_cursor
from mplfuzz.models import APICallExecution
from mplfuzz.utils.result import Err, Ok, Result, resultify

with get_db_cursor() as cur:
    cur.execute(
        f"""CREATE TABLE IF NOT EXISTS apicall_execution (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            api_id INTEGER,
            library_name TEXT,
            api_name TEXT,
            code TEXT,
            result_type INTEGER,
            ret_code INTEGER,
            stdout TEXT,
            stderr TEXT
        )"""
    )


@resultify
def create_apicall_execution(apicall_execution: APICallExecution) -> Result[None, Exception]:
    with get_db_cursor() as cur:
        cur.execute(
            """INSERT INTO apicall_execution 
               (api_id, library_name, api_name, code, result_type, ret_code, stdout, stderr) 
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                apicall_execution.api_id,
                apicall_execution.library_name,
                apicall_execution.api_name,
                apicall_execution.code,
                apicall_execution.result_type,
                apicall_execution.ret_code,
                apicall_execution.stdout,
                apicall_execution.stderr,
            ),
        )
    return Ok(None)


@resultify
def update_apical_execution(apicall_execution: APICallExecution) -> Result[None, Exception]:
    """根据api_id字段，更新result_type, ret_code, stdout, stderr字段"""
    with get_db_cursor() as cur:
        cur.execute(
            """UPDATE apicall_execution
               SET result_type = ?,
                   ret_code = ?,
                   stdout = ?,
                   stderr = ?
               WHERE api_id = ?""",
            (
                apicall_execution.result_type,
                apicall_execution.ret_code,
                apicall_execution.stdout,
                apicall_execution.stderr,
                apicall_execution.api_id,
            ),
        )
    return Ok(None)


@resultify
def get_apicall_execution(execution_id: int) -> Result[APICallExecution | None, Exception]:
    with get_db_cursor() as cur:
        cur.execute("SELECT * FROM apicall_execution WHERE id = ?", (execution_id,))
        row = cur.fetchone()
        if row:
            return APICallExecution(
                id=row[0],
                api_id=row[1],
                api_name=row[2],
                library_name=row[3],
                code=row[4],
                result_type=row[5],
                ret_code=row[6],
                stdout=row[7],
                stderr=row[8],
            )
        else:
            return None


@resultify
def get_apicall_executions(api_id: Optional[int] = None) -> Result[List[APICallExecution], Exception]:
    with get_db_cursor() as cur:
        if api_id is not None:
            cur.execute("SELECT * FROM apicall_execution WHERE api_id = ?", (api_id,))
        else:
            cur.execute("SELECT * FROM apicall_execution")
        rows = cur.fetchall()
        executions = [
            APICallExecution(
                api_id=row[1],
                api_name=row[2],
                library_name=row[3],
                code=row[4],
                result_type=row[5],
                ret_code=row[6],
                stdout=row[7],
                stderr=row[8],
            )
            for row in rows
        ]
    return Ok(executions)
