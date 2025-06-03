from mplfuzz.utils.db import get_all_library_names, get_all_unsolved_apis, get_status_of_library
import pytest

def test_get_all_library_names():
    print(get_all_library_names())

@pytest.mark.skip(reason="numpy may not exists")
def test_get_status_of_library():
    status = get_status_of_library("numpy").value
    a = len([x for x in status.values() if x > 0])
    b = len(status.values())
    print(f"numpy's solving rate is {a/b*100:.2f}%")

def test_get_status_of_all():
    library_names = get_all_library_names().value
    res = f"\n|{"Library Name":^20}|{"API Solved":^20}|{"API Total":^20}|{"Solving Rate":^20}|\n"
    for library_name in library_names:
        try:
            status = get_status_of_library(library_name)
            if status.is_err:
                print(f"Error getting status of library {library_name}: {status.error}")
                continue
            status = status.value
            a = len([x for x in status.values() if x > 0])
            b = len(status.values())
            res += f"|{library_name:^20}|{a:^20}|{b:^20}|{f"{a/b*100:.2f}%":^20}|\n"
        except Exception as e:
            print(f"Error getting status of library {library_name}: {str(e)}")
    print(res)

def test_get_all_unmcped_apis():
    from mplfuzz.utils.db import get_all_unmcped_apis
    res = get_all_unmcped_apis().value
    print(f"Number of unmcped APIs: {len(res)}")

def test_get_all_unsolved_apis():
    res = get_all_library_names().value
    for library_name in res:
        unsolved_apis = get_all_unsolved_apis(library_name)
        if unsolved_apis.is_err:
            assert False, f"Error getting unsolved APIs for library {library_name}: {unsolved_apis.error}"
            continue
        unsolved_apis = unsolved_apis.value
        print(f"Library {library_name} contains {len(unsolved_apis)} unsolved APIs.")


            