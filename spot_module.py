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


def search_spots(only_available=False):
    line()
    print("Search & Sort Spots")
    if not data_store.spots_db:
        print("No parking spots have been listed yet.")
        return

    keyword = ask_text("Enter location keyword (leave blank for all): ").lower()
    max_price_raw = ask_text("Enter maximum price per hour (leave blank for any): ")
    max_price = None
    if max_price_raw:
        try:
            max_price = float(max_price_raw)
        except ValueError:
            print("Invalid price entered, ignoring the price filter.")

    print("Sort by: 1. Default  2. Price (low to high)  3. Slots left (high to low)")
    sort_choice = ask_int("Choose a sort option: ", minimum=1)

    results = []
    for spot in data_store.spots_db:
        if only_available and not spot.has_slots():
            continue
        if keyword and keyword not in spot.location.lower():
            continue
        if max_price is not None and spot.price_per_hour > max_price:
            continue
        results.append(spot)

    if sort_choice == 2:
        results.sort(key=lambda s: s.price_per_hour)
    elif sort_choice == 3:
        results.sort(key=lambda s: s.available_slots, reverse=True)

    line()
    print("ID | Location | Rs/hour | Slots Left | Status")
    line()
    if not results:
        print("No spots match your search.")
        line()
        return
    for spot in results:
        print(spot.spot_id, "|", spot.location, "| Rs", spot.price_per_hour, "|", spot.slots_left(), "|", spot.status())
    line()
    print("Matches found:", len(results))
