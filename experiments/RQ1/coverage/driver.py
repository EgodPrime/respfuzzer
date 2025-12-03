import importlib.util
import dcov
import sys
import importlib
def main():
    if len(sys.argv) != 3:
        print("Usage: python py_driver.py <library_name> <test_case_path>", file=sys.__stderr__)
        return

    lib_name = sys.argv[1]
    tc_path = sys.argv[2]
    dcov.open_bitmap_py()


    spec = importlib.util.find_spec(lib_name)
    if spec is None:
        print(f"Library {lib_name} not found.", file=sys.__stderr__)
        return
    origin = spec.origin
    if origin is None:
        print(f"Library {lib_name} does not have an origin.", file=sys.__stderr__)
        return

    code = open(tc_path, 'r').read()
    with dcov.LoaderWrapper() as lw:
        lw.add_source(origin)
        exec(code)

if __name__ == "__main__":
    main()