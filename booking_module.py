import data_store
import billing_module
from models import Booking
from datetime import datetime
from utils_module import ask_int, ask_yes_no, line


def book_spot():
    user = data_store.current_user
    line()
    print("Book a Parking Spot")

    available = []
    for spot in data_store.spots_db:
        if spot.has_slots():
            available.append(spot)
    if not available:
        print("Sorry, no spots are available right now.")
        return

    print("Available spots (with slots left):")
    for spot in available:
        print("Spot #" + str(spot.spot_id), "|", spot.location, "| Rs", spot.price_per_hour, "/hour | Slots left:", spot.slots_left())

    spot_id = ask_int("Enter Spot ID to book: ")
    spot = data_store.find_spot(spot_id)
    if spot is None:
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

    spot.available_slots -= 1
    booking = Booking(data_store.next_booking_id(), spot.spot_id, user.username, hours, spot.price_per_hour, datetime.now())
    data_store.bookings_db.append(booking)

    print("Booking confirmed! Your Booking ID is", booking.booking_id)
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
        entered = booking.entry_time.strftime("%Y-%m-%d %H:%M:%S")
        print("Booking #" + str(booking.booking_id), "| Spot #" + str(booking.spot_id), "|", spot.location, "| Entered:", entered)

    booking_id = ask_int("Enter Booking ID to release: ")
    booking = data_store.find_booking(booking_id)
    if booking is None or booking.status != "Active" or booking.driver_username != user.username:
        print("Invalid booking ID.")
        return

    spot = data_store.find_spot(booking.spot_id)
    exit_time = datetime.now()
    amount, hours = billing_module.final_cost(spot.price_per_hour, booking.entry_time, exit_time)

    booking.exit_time = exit_time
    booking.billed_hours = hours
    booking.final_cost = amount
    booking.status = "Completed"

    spot.available_slots += 1

    line()
    print("Final Bill")
    print("Spot           : #" + str(spot.spot_id), "-", spot.location)
    print("Entered at     :", booking.entry_time.strftime("%Y-%m-%d %H:%M:%S"))
    print("Left at        :", exit_time.strftime("%Y-%m-%d %H:%M:%S"))
    print("Billed hours   :", hours, "(minimum 1 hour)")
    print("Price per hour : Rs", spot.price_per_hour)
    print("Total payable  : Rs", amount)
    print("Slots left at", spot.location + ":", spot.slots_left())
    line()


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
        print("Booking #" + str(booking.booking_id), "| Spot #" + str(booking.spot_id), "|", spot.location, "|", booking.status, "| Total:", total)
