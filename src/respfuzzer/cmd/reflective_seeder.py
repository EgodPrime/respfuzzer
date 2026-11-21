import fire
from respfuzzer.lib.agentic_function_resolver import main as _generate_seeds
from respfuzzer.lib.library_visitor import extract_functions_from_library as _extract


def main():
    fire.Fire(
        {
            "extract_functions": _extract,
            "generate_seeds": _generate_seeds,
        }
    )


if __name__ == "__main__":
    main()
