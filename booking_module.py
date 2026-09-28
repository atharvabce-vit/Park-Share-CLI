import data_store

print("Booking Engine")
driver_name = input("Enter Driver username: ")
book_spot_id_input = input("Enter Spot ID to book: ")
hours_input = input("Enter duration in hours: ")

book_spot_id = int(book_spot_id_input)
hours = int(hours_input)

selected_spot = {}

for spot in data_store.spots_db:
    if spot["spot_id"] == book_spot_id and spot["is_available"]:
        selected_spot = spot
        break

if selected_spot:
    total_cost = selected_spot["price_per_hour"] * hours
    print("Total Cost for", hours, "hours: Rs", total_cost)
    
    confirm = input("Confirm booking? (Y/N): ")
    if confirm == 'Y' or confirm == 'y':
        selected_spot["is_available"] = False  
        
        new_booking = {
            "spot_id": book_spot_id,
            "driver_username": driver_name,
            "hours": hours,
            "total_cost": total_cost
        }
        data_store.bookings_db.append(new_booking)
        print("Booking confirmed! Total billed: Rs", total_cost)
    else:
        print("Booking cancelled.")
else:
    print("Spot is unavailable or does not exist.")