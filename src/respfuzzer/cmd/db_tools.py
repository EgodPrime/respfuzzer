import fire
from respfuzzer.utils.db_tools import (
    view,
)
from respfuzzer.utils.export_dyfuzz import sample_dyfuzz_format


def main():
    """Main entry point for the db_tools command-line interface"""
    fire.Fire(
        {
            "view": view,
            "export-dyfuzz": sample_dyfuzz_format,
        }
    )


if __name__ == "__main__":
    main()
