import data_store
import billing_module
from models import Booking
from datetime import datetime
from utils_module import ask_int, ask_text, ask_yes_no, ask_rating, line


def elapsed_text(entry_time):
    seconds = int((datetime.now() - entry_time).total_seconds())
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    return str(hours) + "h " + str(minutes) + "m"


def book_spot():
    user = data_store.current_user
    line()
    print("Book a Parking Spot")

    available = []
    for spot in data_store.spots_db:
        if spot.is_active and spot.has_slots():
            available.append(spot)
    if not available:
        print("Sorry, no spots are available right now.")
        return

    print("Available spots (with slots left):")
    for spot in available:
        print("Spot #" + str(spot.spot_id), "|", spot.location, "| Rs", spot.price_per_hour, "/hour | Slots left:", spot.slots_left())

    spot_id = ask_int("Enter Spot ID to book: ")
    spot = data_store.find_spot(spot_id)
    if spot is None or not spot.is_active:
        print("That spot does not exist.")
        return
    if not spot.has_slots():
        print("That spot is full. Slots left:", spot.slots_left())
        return

    hours = ask_int("Enter duration in hours: ")
    estimate = billing_module.estimate_cost(spot.price_per_hour, hours)

    line()
    print("Price per hour : Rs", spot.price_per_hour)
    print("Duration       :", hours, "hour(s)")
    print("Estimated cost : Rs", estimate, "(final bill is based on actual parked time)")
    line()

    if not ask_yes_no("Confirm booking? (Y/N): "):
        print("Booking cancelled.")
        return

    slot_number = data_store.next_free_slot(spot)
    spot.available_slots -= 1
    booking = Booking(data_store.next_booking_id(), spot.spot_id, user.username, hours, spot.price_per_hour, datetime.now(), slot_number)
    data_store.bookings_db.append(booking)
    data_store.log_event(user.username + " booked spot #" + str(spot.spot_id) + " (slot " + str(slot_number) + ")")

    print("Booking confirmed! Your Booking ID is", booking.booking_id)
    print("Assigned slot  :", slot_number, "at", spot.location)
    print("Slots left at", spot.location + ":", spot.slots_left())


def release_spot():
    user = data_store.current_user
    line()
    print("Release Spot (Checkout)")

    mine = []
    for booking in data_store.active_bookings():
        if booking.driver_username == user.username:
            mine.append(booking)
    if not mine:
        print("You have no active bookings.")
        return

    print("Your active bookings:")
    for booking in mine:
        spot = data_store.find_spot(booking.spot_id)
        print("Booking #" + str(booking.booking_id), "| Spot #" + str(booking.spot_id), "|", spot.location, "| Slot", booking.slot_number, "| Parked:", elapsed_text(booking.entry_time))

    booking_id = ask_int("Enter Booking ID to release: ")
    booking = data_store.find_booking(booking_id)
    if booking is None or booking.status != "Active" or booking.driver_username != user.username:
        print("Invalid booking ID.")
        return

    spot = data_store.find_spot(booking.spot_id)
    exit_time = datetime.now()
    amount, hours, overtime = billing_module.final_cost(spot.price_per_hour, booking.entry_time, exit_time, booking.planned_hours)

    line()
    if overtime > 0:
        print("Note: you parked", overtime, "hour(s) beyond your planned", booking.planned_hours, "hour(s).")
        print("Overtime is billed at", billing_module.OVERTIME_MULTIPLIER, "x the hourly rate.")
        line()

    print("Price per hour : Rs", spot.price_per_hour)
    print("Billed hours   :", hours, "(minimum 1 hour)")
    print("Subtotal       : Rs", amount)

    promo = ask_text("Enter promo code (leave blank for none): ")
    final_amount, discount, rate = billing_module.apply_discount(amount, promo)
    if discount > 0:
        print("Promo applied  :", promo.strip().upper(), "(-" + str(int(rate * 100)) + "%)")
        print("Discount       : -Rs", discount)
    elif promo.strip():
        print("Promo code", promo.strip().upper(), "is not valid.")

    booking.exit_time = exit_time
    booking.billed_hours = hours
    booking.final_cost = final_amount
    booking.status = "Completed"

    spot.available_slots += 1
    data_store.log_event(user.username + " released spot #" + str(spot.spot_id) + " | Rs " + str(final_amount))

    line()
    print("Final Bill")
    print("Spot           : #" + str(spot.spot_id), "-", spot.location, "(slot", str(booking.slot_number) + ")")
    print("Entered at     :", booking.entry_time.strftime("%Y-%m-%d %H:%M:%S"))
    print("Left at        :", exit_time.strftime("%Y-%m-%d %H:%M:%S"))
    print("Billed hours   :", hours, "(minimum 1 hour)")
    print("Total payable  : Rs", final_amount)
    print("Slots left at", spot.location + ":", spot.slots_left())
    line()

    if ask_yes_no("Rate this spot 1-5? (Y/N): "):
        rating = ask_rating("Your rating (1-5): ")
        spot.add_rating(rating)
        data_store.log_event(user.username + " rated spot #" + str(spot.spot_id) + " " + str(rating) + "/5")
        print("Thanks! New average rating:", spot.average_rating(), "stars")


def cancel_booking():
    user = data_store.current_user
    line()
    print("Cancel a Booking")

    mine = []
    for booking in data_store.active_bookings():
        if booking.driver_username == user.username:
            mine.append(booking)
    if not mine:
        print("You have no active bookings to cancel.")
        return

    print("Your active bookings:")
    for booking in mine:
        spot = data_store.find_spot(booking.spot_id)
        print("Booking #" + str(booking.booking_id), "| Spot #" + str(booking.spot_id), "|", spot.location, "| Slot", booking.slot_number, "| Parked:", elapsed_text(booking.entry_time))

    booking_id = ask_int("Enter Booking ID to cancel: ")
    booking = data_store.find_booking(booking_id)
    if booking is None or booking.status != "Active" or booking.driver_username != user.username:
        print("Invalid booking ID.")
        return

    if not ask_yes_no("Cancel this booking? (Y/N): "):
        print("Cancellation aborted.")
        return

    spot = data_store.find_spot(booking.spot_id)
    booking.status = "Cancelled"
    spot.available_slots += 1
    data_store.log_event(user.username + " cancelled booking #" + str(booking.booking_id))

    print("Booking #" + str(booking.booking_id), "cancelled. No charge applied.")
    print("Slots left at", spot.location + ":", spot.slots_left())


def show_my_bookings():
    user = data_store.current_user
    line()
    print("My Bookings")
    mine = []
    for booking in data_store.bookings_db:
        if booking.driver_username == user.username:
            mine.append(booking)
    if not mine:
        print("You have no bookings yet.")
        return
    for booking in mine:
        spot = data_store.find_spot(booking.spot_id)
        if booking.final_cost is None:
            total = "pending"
        else:
            total = "Rs " + str(booking.final_cost)
        extra = ""
        if booking.status == "Active":
            running = billing_module.final_cost(spot.price_per_hour, booking.entry_time, datetime.now(), booking.planned_hours)[0]
            extra = " | Running: Rs " + str(running) + " | Parked: " + elapsed_text(booking.entry_time)
        print("Booking #" + str(booking.booking_id), "| Spot #" + str(booking.spot_id), "|", spot.location, "| Slot", booking.slot_number, "|", booking.status, "| Total:", total, extra)


def show_host_earnings():
    user = data_store.current_user
    line()
    print("My Earnings")

    my_bookings = []
    for booking in data_store.bookings_db:
        spot = data_store.find_spot(booking.spot_id)
        if spot is not None and spot.host_username == user.username:
            my_bookings.append(booking)
    if not my_bookings:
        print("No bookings have been made on your spots yet.")
        return

    totals = {}
    grand_total = 0.0
    completed = 0
    for booking in my_bookings:
        if booking.final_cost is None:
            continue
        totals[booking.spot_id] = totals.get(booking.spot_id, 0.0) + booking.final_cost
        grand_total += booking.final_cost
        completed += 1

    print("Completed bookings:", completed, "| Pending checkouts:", len(my_bookings) - completed)
    line()
    print("Spot ID | Location | Earnings | Rating")
    line()
    for spot_id in totals:
        spot = data_store.find_spot(spot_id)
        average = spot.average_rating()
        if average is None:
            stars = "No ratings"
        else:
            stars = str(average) + " stars"
        print(spot_id, "|", spot.location, "| Rs", round(totals[spot_id], 2), "|", stars)
    line()
    print("Total earnings: Rs", round(grand_total, 2))


def show_live_status():
    line()
    print("Live Status -", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    line()
    occupied = data_store.total_slots() - data_store.slots_left()
    print("Occupancy  :", occupied, "occupied |", data_store.slots_left(), "free of", data_store.total_slots(), "slots")
    line()
    active = data_store.active_bookings()
    if not active:
        print("No vehicles are currently parked.")
        return
    print("Booking # | Spot | Driver | Slot | Parked | Running Cost")
    line()
    for booking in active:
        spot = data_store.find_spot(booking.spot_id)
        if spot is None:
            continue
        running = billing_module.final_cost(spot.price_per_hour, booking.entry_time, datetime.now(), booking.planned_hours)[0]
        print("#" + str(booking.booking_id), "|", spot.location, "|", booking.driver_username, "|", booking.slot_number, "|", elapsed_text(booking.entry_time), "| Rs", running)
    line()
    print("Live total:", len(active), "active booking(s)")
