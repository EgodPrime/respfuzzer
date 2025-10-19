import json
from typing import List, Optional

from tracefuzz.models import Function
from tracefuzz.repos.base import get_db_cursor

# Create the solve history table
with get_db_cursor() as cur:
    cur.execute(
        """CREATE TABLE IF NOT EXISTS solve_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            func_id INTEGER NOT NULL,
            library_name TEXT NOT NULL,
            func_name TEXT NOT NULL,
            history TEXT NOT NULL
        )"""
    )


def create_solve_history(function: Function, history: list[dict]) -> Optional[int]:
    """
    Store solve history for an function in the database.

    Args:
        function: The function object containing library_name and func_name
        history: List of dictionaries with 'role' and 'content' keys
    """
    # Convert history to a string format
    history_str = json.dumps(history, ensure_ascii=False, indent=2)

    with get_db_cursor() as cur:
        cur.execute(
            """INSERT INTO solve_history (func_id, library_name, func_name, history) VALUES (?, ?, ?, ?)""",
            (function.id, function.library_name, function.func_name, history_str),
        )
        return cur.lastrowid


def get_solve_history(solve_history_id: int) -> Optional[List[dict]]:
    """
    Retrieve solve history for an function from the database.

    Args:
        solve_history_id: The ID of the solve history record

    Returns:
        List of dictionaries with 'role' and 'content' keys
    """
    with get_db_cursor() as cur:
        cur.execute(
            """SELECT history FROM solve_history WHERE id = ? """, (solve_history_id,)
        )
        row = cur.fetchone()
        if not row:
            return None

        history_str = row[0]
        history = json.loads(history_str)
        return history


def delete_solve_history(solve_history_id: int) -> None:
    """
    Delete solve history for an function from the database.

    Args:
        solve_history_id: The ID of the solve history record
    """
    with get_db_cursor() as cur:
        cur.execute("""DELETE FROM solve_history WHERE id = ?""", (solve_history_id,))
