print("\n Initialize Application Data ")


print("\n[User Configuration]")
user_username = input("Enter a username: ")
user_role = input("Enter role (Host or Driver): ")

current_user = {
    "username": user_username,
    "role": user_role
}

print("\n[Parking Spot Configuration]")
spot_id_input = input("Enter a unique Spot ID (integer): ")
spot_location = input("Enter location description: ")
spot_price_input = input("Enter price per hour: ")

parking_spot = {
    "spot_id": int(spot_id_input),
    "location": spot_location,
    "price_per_hour": float(spot_price_input),
    "host_username": user_username,
    "is_available": True
}

print("\n[Booking Configuration]")
driver_name = input("Enter driver's username booking this spot: ")
hours_input = input("Enter number of hours needed: ")
booking_hours = int(hours_input)

calculated_total_cost = parking_spot["price_per_hour"] * booking_hours

booking_record = {
    "spot_id": parking_spot["spot_id"],
    "driver_username": driver_name,
    "hours": booking_hours,
    "total_cost": calculated_total_cost
}

print(" Summary of Captured Data ")
print("User Model:", current_user)
print("Spot Model:", parking_spot)
print("Booking Model:", booking_record)