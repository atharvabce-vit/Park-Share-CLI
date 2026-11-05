class User:
    def __init__(self, username, password, role):
        self.username = username
        self.password = password
        self.role = role


class ParkingSpot:
    def __init__(self, spot_id, location, price_per_hour, host_username, total_slots):
        self.spot_id = spot_id
        self.location = location
        self.price_per_hour = price_per_hour
        self.host_username = host_username
        self.total_slots = total_slots
        self.available_slots = total_slots
        self.ratings = []
        self.is_active = True

    def has_slots(self):
        return self.available_slots > 0

    def slots_left(self):
        return str(self.available_slots) + "/" + str(self.total_slots)

    def status(self):
        if self.has_slots():
            return "Open"
        return "Full"

    def add_rating(self, value):
        self.ratings.append(value)

    def average_rating(self):
        if not self.ratings:
            return None
        return round(sum(self.ratings) / len(self.ratings), 1)


class Booking:
    def __init__(self, booking_id, spot_id, driver_username, planned_hours, price_per_hour, entry_time, slot_number=None):
        self.booking_id = booking_id
        self.spot_id = spot_id
        self.driver_username = driver_username
        self.planned_hours = planned_hours
        self.price_per_hour = price_per_hour
        self.entry_time = entry_time
        self.slot_number = slot_number
        self.exit_time = None
        self.billed_hours = None
        self.final_cost = None
        self.status = "Active"
