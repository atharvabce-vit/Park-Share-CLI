import math

MINIMUM_BILLED_HOURS = 1


def estimate_cost(price_per_hour, hours):
    return round(price_per_hour * hours, 2)


def billed_hours(entry_time, exit_time):
    elapsed_seconds = (exit_time - entry_time).total_seconds()
    parked_hours = elapsed_seconds / 3600
    if parked_hours < MINIMUM_BILLED_HOURS:
        return MINIMUM_BILLED_HOURS
    return math.ceil(parked_hours)


def final_cost(price_per_hour, entry_time, exit_time):
    hours = billed_hours(entry_time, exit_time)
    amount = round(price_per_hour * hours, 2)
    return amount, hours
