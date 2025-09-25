#!/bin/bash

libraries=("ast" "re" "difflib" "locale" 
        "numpy" "scipy" "dask" "inspect"
        "nltk" "torch" "pandas" "sklearn")

# extract api
# for library in "${libraries[@]}"
# do
#     echo "library_visitor $library"
#     library_visitor "$library"
# done


# for library in "${libraries[@]}"
# do
#     echo "agentic_api_resolver $library"
#     agentic_api_resolver "$library"
# done

for library in "${libraries[@]}"
do
    echo "fuzz_library $library"
    fuzz_library "$library"
done