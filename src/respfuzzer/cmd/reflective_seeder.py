import fire
from respfuzzer.lib.agentic_function_resolver import solve_library_functions
from respfuzzer.lib.library_visitor import extract_functions_from_library


def main():
    fire.Fire(
        {
            "extract_functions": extract_functions_from_library,
            "generate_seeds": solve_library_functions,
        }
    )


if __name__ == "__main__":
    main()
