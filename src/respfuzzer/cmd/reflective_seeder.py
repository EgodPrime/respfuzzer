import fire
from respfuzzer.lib.agentic_function_resolver import main as solve
from respfuzzer.lib.library_visitor import extract_functions_from_library as extract


def main():
    fire.Fire(
        {
            "extract_functions": extract,
            "generate_seeds": solve,
        }
    )


if __name__ == "__main__":
    main()
