ParkShare CLI: Person to person Parking Platform

Overview
ParkShare is a Python-based CLI application that solves urban parking problems by connecting drivers with people who have idle parking spaces. It relies on Python fundamentals, core data structures, and Object-Oriented Programming to manage users, parking spots, and bookings.

Features
- Authentication: Register and login with a username, password and role (Host or Driver).
- Password Confirmation: Registration asks for the password twice and rejects mismatches.
- Host Dashboard: List parking spots with a location, price per hour and number of slots.
- Spot Handling: Every listing tracks total slots and slots left, and shows Open/Full status.
- Search & Sort: Filter spots by location keyword and maximum price, then sort by price, slots left or rating.
- Driver Interface: Browse available spots, book a spot for a duration, and see slots left after each booking.
- Slot-Level Tracking: Each booking is assigned an exact slot number, which is freed precisely on checkout or cancellation.
- Billing Engine: Cost is price per hour multiplied by parked hours, with a minimum of 1 hour. A final bill is generated on checkout.
- Planned vs Actual: The checkout warns when parked time exceeds the planned duration and bills the overtime at 1.5x.
- Promo Codes: Drivers can apply a promo code (PARK10, WEEKEND20, FIRST50) at checkout for an instant discount.
- Ratings & Reviews: After checkout, drivers can rate a spot 1-5 stars; averages are shown across all listings.
- Release / Checkout: Drivers release a spot when leaving, which frees a slot and prints the final bill.
- Booking Cancellation: Drivers can cancel an active booking before checkout, which frees the slot with no charge.
- Spot Management: Hosts can edit the location/price of their own spots and remove listings when no vehicle is parked.
- Host Earnings: Hosts see per-spot revenue, ratings and total earnings from completed bookings.
- Live Activity Feed: Every action is timestamped and listed in a real-time activity feed for both roles.
- Live Status: A real-time board shows current occupancy, live parked duration and running cost per active booking.
- Modular Design: Code is separated into specific modules to ensure maintainability.

Modules
- models.py        -> User, ParkingSpot and Booking classes.
- data_store.py    -> In-memory storage, lookup helpers, slot tracking and the activity log.
- utils_module.py  -> Safe input helpers (text, integer, float, yes/no, rating).
- auth_module.py   -> Registration (with password confirmation), login and logout.
- spot_module.py   -> Add, view, edit, remove and search/sort spots.
- booking_module.py-> Book, release, cancel and view bookings, plus host earnings and live status.
- billing_module.py-> Billing, overtime, promo codes and duration logic.
- activity_module.py-> Timestamped live activity feed.
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
3. Log out (option 6) and register/log in as a Driver.
4. As the Driver, select option 1 to view available spots and option 2 to book a spot; note the slots left after booking.
5. Select option 5 to view your bookings, then option 3 to release a spot and see the final bill.
6. As the Driver, select "Search & Sort Spots" to filter by keyword, max price or rating and sort the results.
7. As the Driver, book a spot then use "Cancel a Booking" to cancel it before checkout and confirm the slot is freed.
8. Log back in as the Host and select "My Earnings" to view per-spot revenue, ratings and the total.
9. As the Driver, book a spot then use "Release Spot (Checkout)": try an overtime stay, enter a promo code such as PARK10, and rate the spot 1-5.
10. Open "Live Status" and "Live Activity Feed" from either dashboard to see real-time occupancy, running costs and timestamped events.
11. As the Host, use "Edit My Spot" to change a location or price, and "Remove My Spot" to hide a listing when nothing is parked.
