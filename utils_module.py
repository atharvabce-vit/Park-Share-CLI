def line(length=58):
    print("-" * length)


def ask_text(prompt):
    return input(prompt).strip()


def ask_int(prompt, minimum=1):
    while True:
        raw = input(prompt).strip()
        try:
            value = int(raw)
        except ValueError:
            print("Please enter a whole number.")
            continue
        if value < minimum:
            print("Value must be at least", minimum)
            continue
        return value


def ask_float(prompt, minimum=0.0):
    while True:
        raw = input(prompt).strip()
        try:
            value = float(raw)
        except ValueError:
            print("Please enter a valid number.")
            continue
        if value < minimum:
            print("Value must be at least", minimum)
            continue
        return value


def ask_yes_no(prompt):
    while True:
        raw = input(prompt).strip().lower()
        if raw in ("y", "yes"):
            return True
        if raw in ("n", "no"):
            return False
        print("Please answer Y or N.")


def ask_rating(prompt):
    while True:
        value = ask_int(prompt, minimum=1)
        if value > 5:
            print("Rating must be between 1 and 5.")
            continue
        return value
