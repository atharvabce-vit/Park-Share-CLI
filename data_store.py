users_db = {}
spots_db = []
bookings_db = []

current_user = None

_spot_counter = 0
_booking_counter = 0


def next_spot_id():
    global _spot_counter
    _spot_counter += 1
    return _spot_counter


def next_booking_id():
    global _booking_counter
    _booking_counter += 1
    return _booking_counter


def find_spot(spot_id):
    for spot in spots_db:
        if spot.spot_id == spot_id:
            return spot
    return None


def find_booking(booking_id):
    for booking in bookings_db:
        if booking.booking_id == booking_id:
            return booking
    return None


def active_bookings():
    result = []
    for booking in bookings_db:
        if booking.status == "Active":
            result.append(booking)
    return result


def total_slots():
    count = 0
    for spot in spots_db:
        count += spot.total_slots
    return count


def slots_left():
    count = 0
    for spot in spots_db:
        count += spot.available_slots
    return count
