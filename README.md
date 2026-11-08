# ParkShare

ParkShare connects drivers looking for parking with hosts who have a space to share. It includes both the original terminal experience and a responsive browser-based interface.

## What it can do

- **Browse and book without an account:** Visitors can browse listings and make simulated guest bookings. Guest booking history is available only in the same browser session. Registration is optional for drivers and required to publish and manage real host listings.
- **Real listings by default:** The browser app shows parking published by registered hosts. Built-in sample listings across 25 major Indian cities are disabled by default and can be enabled only for a local demo.
- **TomTom parking discovery:** When configured with licensed TomTom access, nearby parking facilities and their provider-reported availability are shown separately from ParkShare host listings. Provider facilities are discovery-only; ParkShare cannot reserve them.
- **Safer signup:** Password confirmation, password-strength guidance, profile validation, CSRF-protected forms, and hashed passwords.
- **Find a space:** Select an area on an interactive Leaflet map, search a place name, or optionally use browser location. Filter geo-tagged spaces by 1, 3, 5, 10, 25, or 50 km, time, price, amenities, and vehicle. See map pins, distance, compatible vehicles, and a 0–100 recommendation score.
- **Manage bookings:** Reserve start/end times with overlap checks, see booking history, cancel an active booking, or check out with an optional promo code and rating.
- **Host tools:** Pin each parking entrance on the map, add/edit/remove listings, upload JPG/PNG/WebP photos, and see active bookings and earnings/occupancy trends.
- **Account tools:** Edit your profile, delete your account (after active bookings are resolved), and request a time-limited password-reset link by email.
- **Notifications:** Hosts receive in-app updates when drivers book, cancel, or complete a stay.
- **Activity feed:** Review recent ParkShare events.
- **CLI remains available:** The browser interface is an addition; the terminal app still starts with `main.py`.

### Map search and recommendations

1. A host signs in, opens **My listings**, enters the listing's location in India, and presses Enter or selects **Show on map** to center the map. The host can also click the map to place the pin. The pin can be dragged to the parking entrance. A pin is required to publish a new web listing.
2. Anyone can open **Find parking** without signing in, type an Indian city, neighborhood, or landmark and press Enter or **Show on map** to center the map. Click **Search this area** to find parking nearby, or click a point on the map or select **Use my location** and grant browser permission. Pick a radius of 5, 10, 15, or 20 km; the default is 20 km. A driver with a registered vehicle sees compatible spaces by default; visitors can choose a vehicle type when making a guest booking.
3. Matching host listings appear as cards and map markers. The recommended sort calculates a score from distance (35%), available capacity for the selected time window (25%), relative hourly price among the filtered results (20%), and rating (20%). Unrated listings receive a neutral rating component. Use the other sort options to sort by price, rating, or availability instead. This is a ParkShare ranking, not a guarantee of suitability.
4. In the normal mode, only host-published listings are shown as bookable parking. If you explicitly enable demo mode, built-in sample listings appear with a **BUILT-IN SAMPLE · NOT REAL PARKING** warning. Their locations, rates, capacity, and bookings are simulated; do not travel to them or rely on them for parking. The map may also show generated amber **demo coverage pins**, which are never listings and cannot be booked.

ParkShare stores the selected latitude and longitude in SQLite; it does not automatically infer exact coordinates from the typed location until **Show on map** or **Search this area** is used. Place-name search is restricted to India. Map tiles come from OpenStreetMap and place-name search uses Nominatim, so map display and place search need internet access. These services provide map/search functionality, not live parking inventory or availability. Browser geolocation is optional, requires permission, and generally needs a secure context (localhost is allowed).

TomTom Parking Availability supports India and reports off-street availability refreshed approximately every 10 minutes. TomTom requires a separately approved commercial entitlement for its Parking API; it is not included in its free evaluation or Pay As You Grow plans. Request access through the [TomTom Parking API docs](https://docs.tomtom.com/parking-api/documentation/product-information/introduction). The app uses TomTom Search to find nearby facilities and the Parking Availability service when the required API key and entitlement are configured. Availability can be absent for a facility or region; in that case, the app shows that live status is unavailable rather than assuming spaces are free. The integration does not make reservations or process payments.

### Bookings, accounts, and host analytics

- Booking search availability is calculated for the selected start time and duration. A slot cannot be double-booked during overlapping reservation windows. A reservation is capped at 168 hours and can start up to 30 days in advance.
- Guest bookings require a name and vehicle type and can be viewed, cancelled, or checked out only in the browser session that created them. Clearing that session removes access to the guest booking history. Creating an account is recommended for persistent booking history.
- Checkout, ratings, discounts, and earnings are demo features; ParkShare does not process real payments.
- Listing photos are stored locally in `static/uploads` (JPG, PNG, or WebP, maximum 5 MB). Listing coordinates, accounts, ratings, bookings, activity, and notifications are stored in the SQLite database.
- Hosts receive in-app notifications for booking, cancellation, and checkout events. Profile changes and account deletion are available from **Profile**; account deletion requires the current password and no active bookings.
- Password reset uses single-use links that expire after 30 minutes. Email delivery only works after an SMTP provider is configured below.
- Host **Earnings** displays all-time revenue, revenue by listing, the last six calendar months of completed earnings, and estimated 30-day occupancy (reserved space-hours divided by the capacity of live listings).

## Run the browser app

Requires Python and pip.

1. Install the project dependency:

   ```powershell
   python -m pip install -r requirements.txt
   ```

2. Start the local web server (default port 5000). To keep it separate from another app on port 5000, use port 5001:

   ```powershell
   $env:PARKSHARE_PORT = "5001"
   python web_app.py
   ```

3. Open [http://127.0.0.1:5001](http://127.0.0.1:5001) in your browser to browse host listings and try guest bookings, or choose **Create account** to register as a driver or host. If you did not set `PARKSHARE_PORT`, use [http://127.0.0.1:5000](http://127.0.0.1:5000).

Choose another available port if needed:

```powershell
$env:PARKSHARE_PORT = "5002"
python web_app.py
```

`PARKSHARE_HOST` changes the bind address. `PARKSHARE_PUBLIC_URL` sets the public base URL used in password-reset links. `PARKSHARE_DATABASE` selects the SQLite file; by default it is `instance/parkshare.sqlite3`. Set environment variables in the same PowerShell window before starting the server.

Sample inventory is off by default. To enable the clearly labeled, simulated listings for a local demo, set `$env:PARKSHARE_DEMO_MODE = "1"` before starting the server. Set it to `"0"` (or remove it) to return to normal mode; built-in samples will be hidden and cannot be booked. Host-created listings remain available in either mode.

To enable TomTom discovery after TomTom grants the required API access, set `$env:PARKSHARE_TOMTOM_API_KEY` to your key before launching the server. The integration only searches within the selected map pin and radius, requests up to five nearby facilities, and displays provider availability if returned. Do not commit the key. TomTom data is not used as a ParkShare booking or host listing.

Signup lets you choose **Find parking** (Driver) or **Share my space** (Host). Driver profiles ask for a vehicle type so the app can filter out incompatible parking spaces. Passwords must be at least eight characters and include a number.

To use a stable signing key across restarts, set the `PARKSHARE_SECRET_KEY` environment variable before launching the server. Generate a local value with:

```powershell
python -c "import secrets; print(secrets.token_hex(32))"
```

Password-reset email requires a reachable SMTP provider. Set `PARKSHARE_SMTP_HOST` and `PARKSHARE_SMTP_FROM`, plus `PARKSHARE_SMTP_USER` and `PARKSHARE_SMTP_PASSWORD` when the provider requires authentication. `PARKSHARE_SMTP_PORT` defaults to `587` with STARTTLS. Do not commit credentials. Reset links expire after 30 minutes and can only be used once. Without SMTP configuration, reset requests show a generic confirmation, but no email is delivered.

The built-in Flask development server is intended for local demos, not production deployment.

## Run the CLI

```powershell
python main.py
```

CLI registration also requests a name and account type. Drivers provide their vehicle type; email, phone, and vehicle plate are optional.

## Run tests

```powershell
python -m unittest discover -s tests -v
```

## Important limitations

The SQLite database and uploaded photos are local to this installation; back them up together. The built-in Flask server is intended for local demos, not production deployment. Configure a production WSGI server, HTTPS, secure secrets, backups, and a real payment provider before handling real users or payments. Leaflet assets, OpenStreetMap tiles, and Nominatim place search depend on third-party internet services and their availability/usage policies.

## Project layout

- `web_app.py` — Flask routes and browser application.
- `templates/` and `static/` — responsive browser pages and styles.
- `main.py` — terminal application entry point.
- `auth_module.py` — shared account creation and password verification.
- `models.py` — user, parking spot, and booking models.
- `data_store.py` — SQLite-backed application state shared by the CLI and web app.
- `spot_module.py`, `booking_module.py`, `billing_module.py` — CLI spot and booking flows and shared billing rules.
- `tests/test_web_app.py` — browser application flow tests.
