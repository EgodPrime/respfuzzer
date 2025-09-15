def judge_level_1(name, obj, mod):
    # Skip if the attribute is a private attribute.
    if name.startswith("_"):
        continue