#!/bin/bash

libraries=("ast" "re" "difflib" "locale" "numpy" "scipy" "dask" "nltk" "torch" "pandas")

# # extract api
# for library in "${libraries[@]}"
# do
#     echo "library_visitor $library"
#     library_visitor "$library"
# done

# # generate mcp
# for library in "${libraries[@]}"
# do
#     echo library_mcp_generator "$library"
#     library_mcp_generator "$library"
# done

# # generate apicall
# for library in "${libraries[@]}"
# do
#     echo "library_api_resolver $library ."
#     library_api_resolver "$library" .
# done

# # mutation
for library in "${libraries[@]}"
do
    echo "library_apicall_mutator $library"
    library_apicall_mutator "$library"
done

# execution
for library in "${libraries[@]}"
do
    echo "apicall_mutants_executor $library"
    apicall_mutants_executor "$library"
done