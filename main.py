import data_store
import auth_module
import spot_module
import booking_module
from utils_module import ask_int, line

print("=== Welcome to ParkShare CLI ===")


def signed_out_menu():
    line()
    print("1. Register")
    print("2. Login")
    print("3. Exit")
    choice = ask_int("Choose an option: ", minimum=1)
    if choice == 1:
        auth_module.register_user()
    elif choice == 2:
        auth_module.login()
    elif choice == 3:
        return False
    else:
        print("Invalid option.")
    return True


def host_menu():
    line()
    print("Host Dashboard -", data_store.current_user.username)
    print("1. Add a Parking Spot")
    print("2. View My Listings")
    print("3. View All Spots")
    print("4. Logout")
    choice = ask_int("Choose an option: ", minimum=1)
    if choice == 1:
        spot_module.add_spot()
    elif choice == 2:
        spot_module.show_host_spots()
    elif choice == 3:
        spot_module.show_spots()
    elif choice == 4:
        auth_module.logout()
    else:
        print("Invalid option.")


def driver_menu():
    line()
    print("Driver Dashboard -", data_store.current_user.username)
    print("1. View Available Spots")
    print("2. Book a Spot")
    print("3. Release Spot (Checkout)")
    print("4. My Bookings")
    print("5. Logout")
    choice = ask_int("Choose an option: ", minimum=1)
    if choice == 1:
        spot_module.show_spots(only_available=True)
    elif choice == 2:
        booking_module.book_spot()
    elif choice == 3:
        booking_module.release_spot()
    elif choice == 4:
        booking_module.show_my_bookings()
    elif choice == 5:
        auth_module.logout()
    else:
        print("Invalid option.")


running = True
while running:
    if data_store.current_user is None:
        running = signed_out_menu()
    elif data_store.current_user.role == "Host":
        host_menu()
    else:
        driver_menu()

print("\n=== Application Execution Complete ===")
print("\n[Final Database States]")
print("Users in DataBase   :", list(data_store.users_db.keys()))
print("Spots in DataBase   :", data_store.total_slots(), "total slots |", data_store.slots_left(), "left")
print("Bookings in DataBase:", len(data_store.bookings_db))
