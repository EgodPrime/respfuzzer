
from respfuzzer.lib.fuzz.replay_mutation import replay_mutation_one
from loguru import logger
import json


def validate_one(crash: dict[str,int|str]) -> dict[str, list]:
    seed_id = crash['seed_id']
    random_state = crash['random_state']
    logger.info(
        f"Validating seed {seed_id} with random state {random_state}"
    )
    replay_mutation_one(seed_id, random_state)
    

if __name__ == '__main__':
    with open('filtered_replay_results.json', 'r', encoding='utf-8') as f:
        filtered_data: dict[str, list[dict[str, int|str]]] = json.load(f)
    
    for library_name, crash_list in filtered_data.items():
        for crash in crash_list:
            if crash.get('validated'):
                logger.info(f"Seed {crash['seed_id']} with random state {crash['random_state']} already validated, skipping")
                continue
            try:
                validate_one(crash)
                # 能够正常运行则说明不是true crash
                crash["is_true"] = False
            except Exception as e:
                logger.error(f"Error validating crash {crash['seed_id']}: {e}")
            except KeyboardInterrupt:
                logger.error("Validation interrupted by user")
                crash["is_true"] = True
            finally:
                crash['validated'] = True
            json.dump(filtered_data, open('filtered_replay_results.json', 'w', encoding='utf-8'), indent=2, ensure_ascii=False)