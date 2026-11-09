from datetime import timedelta


class User:
    def __init__(self, username, password, role, full_name="", email="", phone="", vehicle_type="", vehicle_plate="", is_suspended=False, suspended_reason="", verification_status="unverified", verification_note="", verified_at=None):
        self.username = username
        self.password = password
        self.role = role
        self.full_name = full_name
        self.email = email
        self.phone = phone
        self.vehicle_type = vehicle_type
        self.vehicle_plate = vehicle_plate
        self.is_suspended = bool(is_suspended)
        self.suspended_reason = suspended_reason or ""
        self.verification_status = verification_status or "unverified"
        self.verification_note = verification_note or ""
        self.verified_at = verified_at


class ParkingSpot:
    def __init__(self, spot_id, location, price_per_hour, host_username, total_slots,
                 description="", covered=False, ev_charging=False, vehicle_types=None,
                 photo="", latitude=None, longitude=None, is_demo=False):
        self.spot_id = spot_id
        self.location = location
        self.price_per_hour = price_per_hour
        self.host_username = host_username
        self.total_slots = total_slots
        self.description = description
        self.covered = covered
        self.ev_charging = ev_charging
        self.vehicle_types = vehicle_types or ["Car", "Motorcycle", "SUV", "Van"]
        self.available_slots = total_slots
        self.ratings = []
        self.is_active = True
        self.photo = photo
        self.latitude = latitude
        self.longitude = longitude
        self.is_demo = is_demo

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
    def __init__(self, booking_id, spot_id, driver_username, planned_hours, price_per_hour,
                 entry_time, slot_number=None, start_time=None, end_time=None,
                 guest_name="", guest_id="", checkin_time=None):
        self.booking_id = booking_id
        self.spot_id = spot_id
        self.driver_username = driver_username
        self.planned_hours = planned_hours
        self.price_per_hour = price_per_hour
        self.entry_time = entry_time
        self.start_time = start_time or entry_time
        self.end_time = end_time or (self.start_time + timedelta(hours=planned_hours))
        self.slot_number = slot_number
        self.guest_name = guest_name
        self.guest_id = guest_id
        self.checkin_time = checkin_time
        self.exit_time = None
        self.billed_hours = None
        self.final_cost = None
        self.status = "Active"
        self.payment_status = "unpaid"
        self.payment_order_id = ""
        self.payment_id = ""
        self.payment_amount = None
