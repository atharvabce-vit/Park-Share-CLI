import data_store
from models import ParkingSpot
from utils_module import ask_text, ask_int, ask_float, line


def add_spot():
    user = data_store.current_user
    line()
    print("Add a Parking Spot")
    location = ask_text("Enter parking spot location: ")
    if not location:
        print("Location cannot be empty.")
        return
    price_per_hour = ask_float("Enter price per hour (Rs): ", minimum=1.0)
    total_slots = ask_int("Enter number of parking slots at this location: ", minimum=1)

    spot = ParkingSpot(data_store.next_spot_id(), location, price_per_hour, user.username, total_slots)
    data_store.spots_db.append(spot)

    print("Spot added successfully!")
    print("Assigned Spot ID:", spot.spot_id)
    print("Slots available:", spot.slots_left())


def show_spots(only_available=False):
    line()
    print("Parking Spots")
    if not data_store.spots_db:
        print("No parking spots have been listed yet.")
        return

    print("ID | Location | Rs/hour | Slots Left | Status")
    line()
    shown = 0
    for spot in data_store.spots_db:
        if only_available and not spot.has_slots():
            continue
        shown += 1
        print(spot.spot_id, "|", spot.location, "| Rs", spot.price_per_hour, "|", spot.slots_left(), "|", spot.status())
    if shown == 0:
        print("No spots are currently available.")
    line()
    print("Total slots:", data_store.total_slots(), "| Slots left:", data_store.slots_left())


def show_host_spots():
    user = data_store.current_user
    line()
    print("My Listed Spots")
    mine = []
    for spot in data_store.spots_db:
        if spot.host_username == user.username:
            mine.append(spot)
    if not mine:
        print("You have not listed any spots yet.")
        return
    for spot in mine:
        print("Spot #" + str(spot.spot_id), "|", spot.location, "| Rs", spot.price_per_hour, "/hour | Slots left:", spot.slots_left(), "|", spot.status())
