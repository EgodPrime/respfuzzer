import subprocess
import dcov
import os
from pathlib import Path
from Logger import Logger
def main():
    dcov.open_bitmap_py()
    dcov.clear_bitmap_py()
    libraries=[ 
        "nltk",
        "dask",
        "yaml", # PyYAML
        "prophet",
        "numpy",
        "pandas",
        "sklearn", # Scikit-learn
        "scipy",
        "requests",
        "spacy",
        "torch",
        "paddle", # PaddlePaddle
        ]
    
    # workspace-relative defaults (repo root -> experiments/RQ1/...)
    repo_root = Path(__file__).resolve().parents[3]
    base_dir = str(repo_root / 'experiments' / 'RQ1' / 'LLM_testcases' / 'generated_by_api')
    logger = Logger(str(repo_root / 'experiments' / 'RQ1'), 'cov_log.txt')

    for i in range(12):
        lib = libraries[i]
        lib_dir = os.path.join(base_dir, lib)
        for file in os.listdir(lib_dir): 
            if file.endswith('.py'):
                file_abs_path = os.path.join(lib_dir, file)
                full_cmd = [
                    "python",
                    str(repo_root / 'experiments' / 'RQ1' / 'coverage' / 'driver.py'),
                    lib,
                    file_abs_path,
                ]
                
                try:
                    subprocess.run(
                                full_cmd,
                                check=True,
                                capture_output=True,
                                text=True,
                                timeout=10,
                            )
                except subprocess.TimeoutExpired:
                    print("Process timed out")
                    continue
                except Exception as e:
                    continue
                finally:
                    logger.info(f'{lib} {file} has been processed')
                    logger.info(f'Coverage now: {dcov.count_bitmap_py()}')
                    
        
if __name__ == "__main__":
    main()