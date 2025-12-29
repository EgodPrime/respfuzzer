import fire
from respfuzzer.lib.fuzz.fuzz_exp import fuzz_dataset, fuzz_one_library
from respfuzzer.lib.fuzz.llm_mutator import random_llm_mutate


def toy_batch_random_llm_mutate(
    seed_file_path: str, full_function_name: str, num_mutations: int
):
    from respfuzzer.models import Seed

    with open(seed_file_path, "r") as f:
        seed_code = f.read()
    library_name = full_function_name.split(".")[0]
    seed = Seed(
        id=0,
        func_id=0,
        library_name=library_name,
        func_name=full_function_name,
        args=[],
        function_call=seed_code,
    )
    for i in range(1, num_mutations + 1):
        print(f"--- Mutated Code {i} ---")
        mutated_code = random_llm_mutate(seed)
        print(mutated_code)
        seed.function_call = mutated_code  # Update seed for next mutation
        print("\n")


def main():
    fire.Fire(
        {
            "fuzz_dataset": fuzz_dataset,
            "fuzz_library": fuzz_one_library,
            "toy_batch_random_llm_mutate": toy_batch_random_llm_mutate,
        }
    )


if __name__ == "__main__":
    main()
