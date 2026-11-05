ParkShare CLI: Person to person Parking Platform

Overview
ParkShare is a Python-based CLI application that solves urban parking problems by connecting drivers with people who have idle parking spaces. It relies on Python fundamentals, core data structures, and Object-Oriented Programming to manage users, parking spots, and bookings.

Features
- Authentication: Register and login with a username, password and role (Host or Driver).
- Host Dashboard: List parking spots with a location, price per hour and number of slots.
- Spot Handling: Every listing tracks total slots and slots left, and shows Open/Full status.
- Driver Interface: Browse available spots, book a spot for a duration, and see slots left after each booking.
- Billing Engine: Cost is price per hour multiplied by parked hours, with a minimum of 1 hour. A final bill is generated on checkout.
- Release / Checkout: Drivers release a spot when leaving, which frees a slot and prints the final bill.
- Modular Design: Code is separated into specific modules to ensure maintainability.

Modules
- models.py        -> User, ParkingSpot and Booking classes.
- data_store.py    -> In-memory storage plus lookup and slot helper functions.
- utils_module.py  -> Safe input helpers (text, integer, float, yes/no).
- auth_module.py   -> Registration, login and logout.
- spot_module.py   -> Add spots and view listings.
- booking_module.py-> Book, release and view bookings.
- billing_module.py-> Price-per-hour and duration billing logic.
- main.py          -> Menu-driven entry point.

Technologies/Tools Used
- Language: Python 3.x
- Concepts Applied: Control Flow, Functions, OOP, Core Data Structures (Dictionaries, Lists), Type Conversion, I/O Operations.

Steps to Run the Project
1. Open your terminal or command prompt.
2. Navigate to the project directory.
3. Run the application using the command: python main.py

Instructions for Testing
1. Launch the app and select option 1 to register a Host account, then option 2 to log in.
2. As the Host, select option 1 to add a parking spot (provide location, price per hour and number of slots).
3. Log out (option 4) and register/log in as a Driver.
4. As the Driver, select option 1 to view available spots and option 2 to book a spot; note the slots left after booking.
5. Select option 4 to view your bookings, then option 3 to release a spot and see the final bill.
