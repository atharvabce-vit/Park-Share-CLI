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

    def has_slots(self):
        return self.available_slots > 0

    def slots_left(self):
        return str(self.available_slots) + "/" + str(self.total_slots)

    def status(self):
        if self.has_slots():
            return "Open"
        return "Full"


class Booking:
    def __init__(self, booking_id, spot_id, driver_username, planned_hours, price_per_hour, entry_time):
        self.booking_id = booking_id
        self.spot_id = spot_id
        self.driver_username = driver_username
        self.planned_hours = planned_hours
        self.price_per_hour = price_per_hour
        self.entry_time = entry_time
        self.exit_time = None
        self.billed_hours = None
        self.final_cost = None
        self.status = "Active"
