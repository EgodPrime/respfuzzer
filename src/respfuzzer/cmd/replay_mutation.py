import fire
from respfuzzer.lib.fuzz.replay_mutation import replay_mutation_one


def main():
    fire.Fire(
        {
            "single_shot": replay_mutation_one,
        }
    )


if __name__ == "__main__":
    main()
