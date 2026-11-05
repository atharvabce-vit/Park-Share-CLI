import math

MINIMUM_BILLED_HOURS = 1
OVERTIME_MULTIPLIER = 1.5

PROMO_CODES = {
    "PARK10": 0.10,
    "WEEKEND20": 0.20,
    "FIRST50": 0.50,
}


def estimate_cost(price_per_hour, hours):
    return round(price_per_hour * hours, 2)


def billed_hours(entry_time, exit_time):
    elapsed_seconds = (exit_time - entry_time).total_seconds()
    parked_hours = elapsed_seconds / 3600
    if parked_hours < MINIMUM_BILLED_HOURS:
        return MINIMUM_BILLED_HOURS
    return math.ceil(parked_hours)


def final_cost(price_per_hour, entry_time, exit_time, planned_hours=None):
    hours = billed_hours(entry_time, exit_time)
    overtime = 0
    base_hours = hours
    if planned_hours is not None and hours > planned_hours:
        overtime = hours - planned_hours
        base_hours = planned_hours
    amount = round(price_per_hour * base_hours + price_per_hour * overtime * OVERTIME_MULTIPLIER, 2)
    return amount, hours, overtime


def apply_discount(amount, code):
    code = code.strip().upper()
    if code in PROMO_CODES:
        rate = PROMO_CODES[code]
        discount = round(amount * rate, 2)
        return round(amount - discount, 2), discount, rate
    return amount, 0.0, 0.0
