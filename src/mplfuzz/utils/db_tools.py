import fire
import importlib
from mplfuzz.db.api_parse_record_table import get_api_iter, get_db_cursor
from mplfuzz.db.apicall_solution_record_table import get_solution_by_api_id


def view(library_name=None):
    """View database statistics for the specified library or all libraries"""
    table = {}
    for api in get_api_iter(library_name):
        if not api.library_name in table:
            table[api.library_name] = {}
        if not api.api_name in table[api.library_name]:
            table[api.library_name][api.api_name] = None
        solution = get_solution_by_api_id(api.id).unwrap()
        if solution:
            table[api.library_name][api.api_name] = solution

    res = f"\n|{"Library Name":^20}|{"API Solved":^20}|{"API Total":^20}|{"Solving Rate":^20}|\n"
    for library in table:
        solved_count = 0
        total_count = len(table[library])
        for api_name, solution in table[library].items():
            if solution:
                solved_count += 1
        res += f"|{library:^20}|{solved_count:^20}|{total_count:^20}|{(solved_count / total_count) * 100:^19.2f}%|\n"

    print(res)


def cleanup_invalid_api_records():
    """Remove invalid API records from the database"""
    with get_db_cursor() as cursor:
        # Query all api_name records
        cursor.execute("SELECT id, api_name FROM api")
        records = cursor.fetchall()

        invalid_ids = []

        for record_id, api_name in records:
            if not is_importable(api_name):
                invalid_ids.append(record_id)
                print(f"Invalid API: {api_name} (ID={record_id})")

        # Delete all invalid records
        if invalid_ids:
            placeholders = ",".join("?" * len(invalid_ids))
            cursor.execute(f"DELETE FROM api WHERE id IN ({placeholders})", invalid_ids)
            print(f"Deleted {len(invalid_ids)} invalid records.")
        else:
            print("No invalid records found.")


def is_importable(package_path: str):
    """Check if a module can be imported"""
    try:
        tokens = package_path.split(".")
        obj = importlib.import_module(tokens[0])
        for token in tokens[1:]:
            obj = getattr(obj, token)
        return True
    except Exception:
        return False


def delete_duplicate_api_records(library_name: str):
    """Remove duplicate API records for a specific library"""
    with get_db_cursor() as cur:
        sql = """
        WITH cte AS (
            SELECT 
                id,
                ROW_NUMBER() OVER (
                    PARTITION BY api_name, source
                    ORDER BY id
                ) AS rn
            FROM api
            WHERE library_name = ?
        )
        DELETE FROM api
        WHERE id IN (
            SELECT id FROM cte WHERE rn > 1
        );
        """
        cur.execute(sql, (library_name,))
        cur.connection.commit()
        print(f"Removed duplicate records for library: {library_name}")


def delete_solution_records(library_name: str=None):
    """Remove all solution records for a specific library, or the entire table if no library is specified"""
    with get_db_cursor() as cur:
        if not library_name:
            # Delete all records
            cur.execute("DELETE FROM solution")
            count_after = cur.rowcount
            
            if count_after > 0:
                print(f"Deleted {count_after} solution records from the entire table.")
            else:
                print("No solution records found in the table.")
            return
        else:
            # Delete all records for the specified library
            cur.execute("DELETE FROM solution WHERE library_name = ?", (library_name,))
            count_after = cur.rowcount
            
            if count_after > 0:
                print(f"Deleted {count_after} solution records for library: {library_name}")
            else:
                print(f"No solution records found for library: {library_name}")


def main():
    """Main entry point for the db_tools command-line interface"""
    fire.Fire({
        'view': view,
        'cleanup-invalid': cleanup_invalid_api_records,
        'delete-duplicate': delete_duplicate_api_records,
        'delete-solution': delete_solution_records
    })


if __name__ == "__main__":
    main()
