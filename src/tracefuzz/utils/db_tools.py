import importlib

from tracefuzz.models import Function, Seed
from tracefuzz.repos.function_table import get_db_cursor, get_function_iter
from tracefuzz.repos.seed_table import get_seed_by_function_id


def view(library_name=None):
    """View database statistics for the specified library or all libraries"""
    function_table: dict[str, dict[str, Function]] = {}
    seed_table: dict[str, dict[str, Seed]] = {}
    for function in get_function_iter(library_name):
        if not function.library_name in function_table:
            function_table[function.library_name] = {}
        function_table[function.library_name][function.func_name] = function

        if not function.library_name in seed_table:
            seed_table[function.library_name] = {}
        if not function.func_name in seed_table[function.library_name]:
            seed_table[function.library_name][function.func_name] = None
        seed = get_seed_by_function_id(function.id)
        if seed:
            seed_table[function.library_name][function.func_name] = seed

    # | Library Name | UDF Count | UDF Solved | BF Count | BF Solved | TF Count | TF Solved |
    # |a | 200 | 199 (99.50%) | 100 | 88 (88.00%) | 300 | 287 (95.67%) |
    # ...
    # UDF: User-Defined Function
    # BF: Built-in Function
    # TF: Total Function
    res = f"\n|{"Library Name":^20}|{"UDF Count":^20}|{"UDF Solved":^20}|{"BF Count":^20}|{"BF Solved":^20}|{"TF Count":^20}|{"TF Solved":^20}|\n"
    res += f"|{"-"*20}|{"-"*20}|{"-"*20}|{"-"*20}|{"-"*20}|{"-"*20}|{"-"*20}|\n"
    for lib_name in function_table:
        udf_count = 0
        udf_solved = 0
        bf_count = 0
        bf_solved = 0
        for func_name in function_table[lib_name]:
            function = function_table[lib_name][func_name]
            seed = seed_table[lib_name][func_name]
            is_builtin = function.is_builtin
            if is_builtin:
                bf_count += 1
                if seed and seed.function_call:
                    bf_solved += 1
            else:
                udf_count += 1
                if seed and seed.function_call:
                    udf_solved += 1
        tf_count = udf_count + bf_count
        tf_solved = udf_solved + bf_solved

        udf_percent = (
            f"{(udf_solved / udf_count * 100):.2f}%" if udf_count > 0 else "N/A"
        )
        udf_solved_str = f"{udf_solved} ({udf_percent})"
        bf_percent = f"{(bf_solved / bf_count * 100):.2f}%" if bf_count > 0 else "N/A"
        bf_solved_str = f"{bf_solved} ({bf_percent})"
        tf_percent = f"{(tf_solved / tf_count * 100):.2f}%" if tf_count > 0 else "N/A"
        tf_solved_str = f"{tf_solved} ({tf_percent})"

        res += f"|{lib_name:^20}|{udf_count:^20}|{udf_solved_str:^20}|{bf_count:^20}|{bf_solved_str:^20}|{tf_count:^20}|{tf_solved_str:^20}|\n"

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
            cursor.execute(
                f"DELETE FROM function WHERE id IN ({placeholders})", invalid_ids
            )
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


def delete_seed_records(library_name: str = None):
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
