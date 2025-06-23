from typing import Optional, List

from mplfuzz.db.base import get_db_cursor
from mplfuzz.utils.result import Err, Ok, Result, resultify
from mplfuzz.models import MCPCode


# Create the table if it doesn't exist
with get_db_cursor() as cur:
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS mcpcode (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            api_id INTEGER,
            api_name TEXT,
            library_name TEXT,
            mcpcode TEXT
        )
        """
    )


@resultify
def create_mcpcode(mcpcode: MCPCode) -> Result[None, Exception]:
    """
    Inserts a new MCPCode record into the database.
    """
    with get_db_cursor() as cur:
        cur.execute(
            """
            INSERT OR REPLACE INTO mcpcode (api_id, api_name, library_name, mcpcode)
            VALUES (?, ?, ?, ?)
            """,
            (mcpcode.api_id, mcpcode.api_name, mcpcode.library_name, mcpcode.mcpcode),
        )
    return Ok(None)


@resultify
def get_mcpcode(api_name: str) -> Result[Optional[MCPCode], Exception]:
    """
    Retrieves an MCPCode record by api_name.
    """
    with get_db_cursor() as cur:
        cur.execute("SELECT * FROM mcpcode WHERE api_name = ?", (api_name,))
        row = cur.fetchone()
        if row:
            return Ok(
                MCPCode(
                    id=row[0],
                    api_id=row[1],
                    api_name=row[2],
                    library_name=row[3],
                    mcpcode=row[4],
                )
            )
        else:
            return Ok(None)

@resultify
def get_mcpcode_by_api_id(api_id: int) -> Result[Optional[MCPCode], Exception]:
    with get_db_cursor() as cur:
        cur.execute("SELECT * FROM mcpcode WHERE api_id = ?", (api_id,))
        row = cur.fetchone()
        if row:
            return Ok(
                MCPCode(
                    id=row[0],
                    api_id=row[1],
                    api_name=row[2],
                    library_name=row[3],
                    mcpcode=row[4],
                )
            )
        else:
            return Ok(None)


@resultify
def get_mcpcodes(library_name: Optional[str] = None) -> Result[List[MCPCode], Exception]:
    """
    Retrieves all MCPCode records, optionally filtered by library_name.
    """
    with get_db_cursor() as cur:
        if library_name:
            cur.execute(
                "SELECT * FROM mcpcode WHERE library_name = ?", (library_name,)
            )
        else:
            cur.execute("SELECT * FROM mcpcode")
        rows = cur.fetchall()
        mcpcode_list = [
            MCPCode(
                id=row[0],
                api_id=row[1],
                api_name=row[2],
                library_name=row[3],
                mcpcode=row[4],
            )
            for row in rows
        ]
    return Ok(mcpcode_list)