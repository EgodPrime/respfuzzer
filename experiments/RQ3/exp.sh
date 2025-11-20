
# 修改config.toml, llm_fuzz_per_seed和data_fuzz_per_seed都设为10
# sed -i 's/llm_fuzz_per_seed = 100/llm_fuzz_per_seed = 10/' /home/lisy/tracefuzz/config.toml
# sed -i 's/data_fuzz_per_seed = 100/data_fuzz_per_seed = 10/' /home/lisy/tracefuzz/config.toml
# for i in {1..5}
# do
#     timestamp=$(date +%Y%m%d%H%M)
#     fuzz fuzz_dataset /home/lisy/tracefuzz/experiments/RQ3/tracefuzz_seeds.json  > ../experiments/RQ3/RQ3-respfuzzer-10-10-$timestamp.log 2>&1
# done

# for i in {1..4}
# do
#     timestamp=$(date +%Y%m%d%H%M)
#     fuzz fuzz_dataset /home/lisy/tracefuzz/experiments/RQ3/tracefuzz_seeds.json True False > ../experiments/RQ3/RQ3-respfuzzer-llm-only-10-$timestamp.log 2>&1
# done

# 修改config.toml, llm_fuzz_per_seed和data_fuzz_per_seed都设为100
sed -i 's/llm_fuzz_per_seed = 10/llm_fuzz_per_seed = 100/' /home/lisy/tracefuzz/config.toml
sed -i 's/data_fuzz_per_seed = 10/data_fuzz_per_seed = 100/' /home/lisy/tracefuzz/config.toml

for i in {1..5}
do
    timestamp=$(date +%Y%m%d%H%M)
    fuzz fuzz_dataset /home/lisy/tracefuzz/experiments/RQ3/tracefuzz_seeds.json True False > ../experiments/RQ3/RQ3-respfuzzer-llm-only-100-$timestamp.log 2>&1
done

for i in {1..5}
do
    timestamp=$(date +%Y%m%d%H%M)
    fuzz fuzz_dataset /home/lisy/tracefuzz/experiments/RQ3/tracefuzz_seeds.json False True > ../experiments/RQ3/RQ3-respfuzzer-parameter-only-100-$timestamp.log 2>&1
done

# for i in {1..4}
# do
#     timestamp=$(date +%Y%m%d%H%M)
#     fuzz fuzz_dataset /home/lisy/tracefuzz/experiments/RQ3/tracefuzz_seeds.json  > ../experiments/RQ3/RQ3-respfuzzer-100-100-$timestamp.log 2>&1
# done

# 修改config.toml, llm_fuzz_per_seed和data_fuzz_per_seed都设为10
sed -i 's/llm_fuzz_per_seed = 100/llm_fuzz_per_seed = 10/' /home/lisy/tracefuzz/config.toml
sed -i 's/data_fuzz_per_seed = 100/data_fuzz_per_seed = 10/' /home/lisy/tracefuzz/config.toml