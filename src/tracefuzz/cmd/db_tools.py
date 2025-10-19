import fire

from tracefuzz.utils.db_tools import (
    cleanup_invalid_function_records,
    delete_duplicate_function_records,
    delete_seed_records,
    view,
)
from tracefuzz.utils.export_dyfuzz import sample_dyfuzz_format


def main():
    """Main entry point for the db_tools command-line interface"""
    fire.Fire(
        {
            "view": view,
            "cleanup-invalid": cleanup_invalid_function_records,
            "delete-duplicate": delete_duplicate_function_records,
            "delete-seed": delete_seed_records,
            "export-dyfuzz": sample_dyfuzz_format,
        }
    )


if __name__ == "__main__":
    main()
