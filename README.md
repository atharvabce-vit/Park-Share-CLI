# ParkShare

ParkShare connects drivers looking for parking with hosts who have a space to share. It includes both the original terminal experience and a responsive browser-based interface.

## What it can do

- **Driver and host accounts:** Choose an account type and create a profile. Drivers can add their vehicle type and optional plate; hosts can publish and manage parking listings.
- **Safer signup:** Password confirmation, password-strength guidance, profile validation, CSRF-protected forms, and hashed passwords.
- **Find a space:** Search by location, maximum hourly price, supported vehicle, covered parking, or EV charging; sort by price, rating, or availability.
- **Manage bookings:** Assign an available slot, see booking history, cancel an active booking, or check out with an optional promo code and rating.
- **Host tools:** Add, edit, and remove listings; see active vehicles and completed-booking earnings.
- **Activity feed:** Review recent ParkShare events.
- **CLI remains available:** The browser interface is an addition; the terminal app still starts with `main.py`.

Bookings and promo codes simulate a transaction; the project does not process real payments.

## Run the browser app

Requires Python and pip.

1. Install the project dependency:

   ```powershell
   python -m pip install -r requirements.txt
   ```

2. Start the local web server:

   ```powershell
   python web_app.py
   ```

3. Open [http://127.0.0.1:5000](http://127.0.0.1:5000) in your browser and choose **Create account**.

Signup lets you choose **Find parking** (Driver) or **Share my space** (Host). Driver profiles ask for a vehicle type so the app can filter out incompatible parking spaces. Passwords must be at least eight characters and include a number.

To use a stable signing key across restarts, set the `PARKSHARE_SECRET_KEY` environment variable before launching the server. Generate a local value with:

```powershell
python -c "import secrets; print(secrets.token_hex(32))"
```

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

## Important limitation

Users, listings, bookings, activity, and ratings are stored in memory. They are cleared when the Python process stops, so this version is a local/demo application rather than a persistent multi-user service. A database and production deployment setup would be the next steps before real-world use.

## Project layout

- `web_app.py` — Flask routes and browser application.
- `templates/` and `static/` — responsive browser pages and styles.
- `main.py` — terminal application entry point.
- `auth_module.py` — shared account creation and password verification.
- `models.py` — user, parking spot, and booking models.
- `data_store.py` — in-memory application state.
- `spot_module.py`, `booking_module.py`, `billing_module.py` — CLI spot and booking flows and shared billing rules.
- `tests/test_web_app.py` — browser application flow tests.
