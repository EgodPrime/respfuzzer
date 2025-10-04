import fire
import importlib
from tracefuzz.db.function_table import get_function_iter, get_db_cursor
from tracefuzz.db.seed_table import get_seed_by_function_id


def view(library_name=None):
    """View database statistics for the specified library or all libraries"""
    table = {}
    for function in get_function_iter(library_name):
        if not function.library_name in table:
            table[function.library_name] = {}
        if not function.func_name in table[function.library_name]:
            table[function.library_name][function.func_name] = None
        seed = get_seed_by_function_id(function.id)
        if seed:
            table[function.library_name][function.func_name] = seed

    res = f"\n|{"Library Name":^20}|{"function Solved":^20}|{"function Total":^20}|{"Solving Rate":^20}|\n"
    for library in table:
        solved_count = 0
        total_count = len(table[library])
        for func_name, seed in table[library].items():
            if seed:
                solved_count += 1
        res += f"|{library:^20}|{solved_count:^20}|{total_count:^20}|{(solved_count / total_count) * 100:^19.2f}%|\n"

    print(res)


def cleanup_invalid_function_records():
    """Remove invalid function records from the database"""
    with get_db_cursor() as cursor:
        # Query all func_name records
        cursor.execute("SELECT id, func_name FROM function")
        records = cursor.fetchall()

        invalid_ids = []

        for record_id, func_name in records:
            if not is_importable(func_name):
                invalid_ids.append(record_id)
                print(f"Invalid function: {func_name} (ID={record_id})")

        # Delete all invalid records
        if invalid_ids:
            placeholders = ",".join("?" * len(invalid_ids))
            cursor.execute(f"DELETE FROM function WHERE id IN ({placeholders})", invalid_ids)
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


def delete_duplicate_function_records(library_name: str):
    """Remove duplicate function records for a specific library"""
    with get_db_cursor() as cur:
        sql = """
        WITH cte AS (
            SELECT 
                id,
                ROW_NUMBER() OVER (
                    PARTITION BY func_name, source
                    ORDER BY id
                ) AS rn
            FROM function
            WHERE library_name = ?
        )
        DELETE FROM function
        WHERE id IN (
            SELECT id FROM cte WHERE rn > 1
        );
        """
        cur.execute(sql, (library_name,))
        cur.connection.commit()
        print(f"Removed duplicate records for library: {library_name}")


def delete_seed_records(library_name: str=None):
    """Remove all seed records for a specific library, or the entire table if no library is specified"""
    with get_db_cursor() as cur:
        if not library_name:
            # Delete all records
            cur.execute("DELETE FROM seed")
            count_after = cur.rowcount
            
            if count_after > 0:
                print(f"Deleted {count_after} seed records from the entire table.")
            else:
                print("No seed records found in the table.")
            return
        else:
            # Delete all records for the specified library
            cur.execute("DELETE FROM seed WHERE library_name = ?", (library_name,))
            count_after = cur.rowcount
            
            if count_after > 0:
                print(f"Deleted {count_after} seed records for library: {library_name}")
            else:
                print(f"No seed records found for library: {library_name}")


def main():
    """Main entry point for the db_tools command-line interface"""
    fire.Fire({
        'view': view,
        'cleanup-invalid': cleanup_invalid_function_records,
        'delete-duplicate': delete_duplicate_function_records,
        'delete-seed': delete_seed_records
    })


if __name__ == "__main__":
    main()
