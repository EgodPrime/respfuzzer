import fire
from mplfuzz.db.api_parse_record_table import get_api_iter
from mplfuzz.db.apicall_solution_record_table import get_solution_by_api_id

def generate_whole_table(library_name=None):
    table = {}
    for api in get_api_iter(library_name):
        # print(f"reading {api.library_name}-{api.api_name}...")
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

def main():
    fire.Fire(generate_whole_table)

if __name__ == "__main__":
    main()
