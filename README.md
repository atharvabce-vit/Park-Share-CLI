# ParkShare

ParkShare connects drivers looking for parking with hosts who have a space to share. It includes both the original terminal experience and a responsive browser-based interface.

## What it can do

- **Driver and host accounts:** Choose an account type and create a profile. Drivers can add their vehicle type and optional plate; hosts can publish and manage parking listings.
- **Safer signup:** Password confirmation, password-strength guidance, profile validation, CSRF-protected forms, and hashed passwords.
- **Find a space:** Choose a search point on an interactive Leaflet map, find nearby listings by radius, and see compatible vehicles, distance, and a 0–100 recommendation score based on distance (35%), availability (25%), relative price (20%), and rating (20%). The registered driver vehicle is selected by default; listings that do not support it are excluded.
- **Manage bookings:** Reserve start/end times with overlap checks, see booking history, cancel an active booking, or check out with an optional promo code and rating.
- **Host tools:** Add, edit, and remove listings; upload JPG, PNG, or WebP photos; open map directions; see active vehicles and earnings/occupancy trends.
- **Account tools:** Edit your profile, delete your account, and request a time-limited password-reset link by email.
- **Notifications:** Hosts receive in-app updates when drivers book, cancel, or complete a stay.
- **Activity feed:** Review recent ParkShare events.
- **CLI remains available:** The browser interface is an addition; the terminal app still starts with `main.py`.

Bookings and promo codes simulate a transaction; the project does not process real payments.
Data is stored in SQLite. Listing photos are stored locally under `static/uploads`.
New web listings need a map pin so drivers can find them nearby. Leaflet map tiles use OpenStreetMap; place search uses Nominatim. Those map services require an internet connection. Browser location is optional and requires permission.

## Run the browser app

Requires Python and pip.

1. Install the project dependency:

   ```powershell
   python -m pip install -r requirements.txt
   ```

2. Start the local web server (default port 5000):

   ```powershell
   python web_app.py
   ```

3. Open [http://127.0.0.1:5000](http://127.0.0.1:5000) in your browser and choose **Create account**.

Choose another local port if another app already uses 5000:

```powershell
$env:PARKSHARE_PORT = "5001"
python web_app.py
```

`PARKSHARE_HOST` can also change the bind address. Set `PARKSHARE_PUBLIC_URL` to the browser-facing base URL if password reset links should use a different host or port. `PARKSHARE_DATABASE` selects a different SQLite file; by default the database is `instance/parkshare.sqlite3`.

Signup lets you choose **Find parking** (Driver) or **Share my space** (Host). Driver profiles ask for a vehicle type so the app can filter out incompatible parking spaces. Passwords must be at least eight characters and include a number.

To use a stable signing key across restarts, set the `PARKSHARE_SECRET_KEY` environment variable before launching the server. Generate a local value with:

```powershell
python -c "import secrets; print(secrets.token_hex(32))"
```

Password-reset email requires an SMTP provider. Set `PARKSHARE_SMTP_HOST`, `PARKSHARE_SMTP_FROM`, and optionally `PARKSHARE_SMTP_PORT` (default `587`), `PARKSHARE_SMTP_USER`, and `PARKSHARE_SMTP_PASSWORD`. Reset links expire after 30 minutes and can only be used once.

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

The SQLite database and uploaded photos are local to this installation; back them up together. The built-in Flask server is intended for local demos, not production deployment. Configure a production WSGI server, HTTPS, secure secrets, backups, and a real payment provider before handling real users or payments.

## Project layout

- `web_app.py` — Flask routes and browser application.
- `templates/` and `static/` — responsive browser pages and styles.
- `main.py` — terminal application entry point.
- `auth_module.py` — shared account creation and password verification.
- `models.py` — user, parking spot, and booking models.
- `data_store.py` — SQLite-backed application state shared by the CLI and web app.
- `spot_module.py`, `booking_module.py`, `billing_module.py` — CLI spot and booking flows and shared billing rules.
- `tests/test_web_app.py` — browser application flow tests.
