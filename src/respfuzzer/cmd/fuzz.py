import fire
from respfuzzer.lib.fuzz.fuzz_exp import fuzz_dataset, fuzz_one_library




def main():
    fire.Fire(
        {
            "fuzz_dataset": fuzz_dataset,
            "fuzz_library": fuzz_one_library,
        }
    )


if __name__ == "__main__":
    main()
