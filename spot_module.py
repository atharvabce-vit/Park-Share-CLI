# Demonstrates Module 6: Type Conversion and Module 7: Core Data Structures[cite: 2]
import data_store

print("\n--- Parking Spot Management ---")
print("[Add a Spot]")

host_name = input("Enter Host username adding the spot: ")
spot_id_input = input("Enter a unique Spot ID (integer): ")
location_input = input("Enter parking spot location: ")
price_input = input("Enter price per hour: ")

spot_id = int(spot_id_input)
price_per_hour = float(price_input)

new_spot = {
    "spot_id": spot_id,
    "location": location_input,
    "price_per_hour": price_per_hour,
    "host_username": host_name,
    "is_available": True
}
data_store.spots_db.append(new_spot)
print("Spot added successfully!")

print("\n[Available Spots]")
for spot in data_store.spots_db:
    if spot["is_available"]:
        print("Spot ID:", spot["spot_id"], "| Location:", spot["location"], "| Price: Rs", spot["price_per_hour"])