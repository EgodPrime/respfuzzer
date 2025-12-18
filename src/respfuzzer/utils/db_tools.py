from typing import Optional

from respfuzzer.models import Function, Seed
from respfuzzer.repos import get_functions, get_seed_by_function_name
from respfuzzer.utils.paths import DATA_DIR, PROJECT_DIR


def get_data_for_view(
    data_dir_name: Optional[str] = None,
) -> dict[str, dict[str, int | float | str]]:
    function_table: dict[str, dict[str, Function]] = {}
    seed_table: dict[str, dict[str, Seed]] = {}

    library_names = []
    if data_dir_name:
        data_dir = PROJECT_DIR / data_dir_name
    else:
        data_dir = DATA_DIR
    for file in data_dir.glob("*_functions.json"):
        library_name = file.name[: -len("_functions.json")]
        library_names.append(library_name)

    for library_name in library_names:
        if not library_name in function_table:
            function_table[library_name] = {}
            seed_table[library_name] = {}
        for function in get_functions(library_name):
            function_table[library_name][function.func_name] = function
            seed = get_seed_by_function_name(function.func_name)
            seed_table[library_name][function.func_name] = seed

    # UDF: User-Defined Function
    # BF: Built-in Function
    # TF: Total Function
    res: dict[str, dict[str, int | float | str]] = {}
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
        res[lib_name] = {
            "udf_count": udf_count,
            "udf_solved": udf_solved,
            "udf_solved_percent": udf_percent,
            "udf_solved_str": udf_solved_str,
            "bf_count": bf_count,
            "bf_solved": bf_solved,
            "bf_solved_percent": bf_percent,
            "bf_solved_str": bf_solved_str,
            "tf_count": tf_count,
            "tf_solved": tf_solved,
            "tf_solved_percent": tf_percent,
            "tf_solved_str": tf_solved_str,
        }
    return res


def view():
    """View database statistics for the specified library or all libraries"""
    data = get_data_for_view()
    res = "UDF: User-Defined Function, BF: Built-in Function, TF: Total Function\n"
    res += f"\n|{"Library Name":^20}|{"UDF Count":^20}|{"UDF Solved":^20}|{"BF Count":^20}|{"BF Solved":^20}|{"TF Count":^20}|{"TF Solved":^20}|\n"
    res += f"|{"-"*20}|{"-"*20}|{"-"*20}|{"-"*20}|{"-"*20}|{"-"*20}|{"-"*20}|\n"
    for lib_name, entry in data.items():
        udf_count = entry["udf_count"]
        udf_solved_str = entry["udf_solved_str"]
        bf_count = entry["bf_count"]
        bf_solved_str = entry["bf_solved_str"]
        tf_count = entry["tf_count"]
        tf_solved_str = entry["tf_solved_str"]
        res += f"|{lib_name:^20}|{udf_count:^20}|{udf_solved_str:^20}|{bf_count:^20}|{bf_solved_str:^20}|{tf_count:^20}|{tf_solved_str:^20}|\n"
    print(res)
