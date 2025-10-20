import fire

from tracefuzz.lib.fuzz.fuzz_dataset import fuzz_dataset, fuzz_dataset_infinite
from tracefuzz.lib.fuzz.fuzz_library import fuzz_one_library


def main():
    fire.Fire(
        {
            "fuzz_dataset": fuzz_dataset,
            "fuzz_dataset_infinite": fuzz_dataset_infinite,
            "fuzz_library": fuzz_one_library,
        }
    )


if __name__ == "__main__":
    main()
