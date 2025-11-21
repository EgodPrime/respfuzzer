import fire

from respfuzzer.lib.fuzz.replay_mutation import replay_from_log, replay_mutation_one


def main():
    fire.Fire(
        {
            "single_shot": replay_mutation_one,
            "from_log": replay_from_log,
        }
    )


if __name__ == "__main__":
    main()
