import json
import os
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path

from models import Booking, ParkingSpot, User


users_db = {}
spots_db = []
bookings_db = []
activity_log = []
notifications = []
password_reset_tokens = {}
current_user = None

_spot_counter = 0
_booking_counter = 0
_storage_lock = threading.RLock()
_database_path = os.environ.get(
    "PARKSHARE_DATABASE",
    str(Path(__file__).resolve().parent / "instance" / "parkshare.sqlite3"),
)


def _connect():
    Path(_database_path).parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(_database_path, timeout=10)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY, password TEXT NOT NULL, role TEXT NOT NULL,
            full_name TEXT NOT NULL, email TEXT NOT NULL, phone TEXT NOT NULL,
            vehicle_type TEXT NOT NULL, vehicle_plate TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS spots (
            spot_id INTEGER PRIMARY KEY, location TEXT NOT NULL, price REAL NOT NULL,
            host_username TEXT NOT NULL, total_slots INTEGER NOT NULL,
            description TEXT NOT NULL, covered INTEGER NOT NULL, ev_charging INTEGER NOT NULL,
            vehicle_types TEXT NOT NULL, available_slots INTEGER NOT NULL,
            is_active INTEGER NOT NULL, photo TEXT NOT NULL,
            latitude REAL, longitude REAL
        );
        CREATE TABLE IF NOT EXISTS ratings (
            spot_id INTEGER NOT NULL, rating INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS bookings (
            booking_id INTEGER PRIMARY KEY, spot_id INTEGER NOT NULL,
            driver_username TEXT NOT NULL, planned_hours INTEGER NOT NULL,
            price_per_hour REAL NOT NULL, entry_time TEXT NOT NULL, slot_number INTEGER,
            exit_time TEXT, billed_hours INTEGER, final_cost REAL, status TEXT NOT NULL,
            start_time TEXT, end_time TEXT
        );
        CREATE TABLE IF NOT EXISTS activity_events (
            event_id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, text TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS notifications (
            notification_id INTEGER PRIMARY KEY, username TEXT NOT NULL,
            text TEXT NOT NULL, created_at TEXT NOT NULL, is_read INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS password_reset_tokens (
            token_hash TEXT PRIMARY KEY, username TEXT NOT NULL, expires_at TEXT NOT NULL
        );
        """
    )
    columns = {row[1] for row in connection.execute("PRAGMA table_info(spots)")}
    if "photo" not in columns:
        connection.execute("ALTER TABLE spots ADD COLUMN photo TEXT NOT NULL DEFAULT ''")
    if "latitude" not in columns:
        connection.execute("ALTER TABLE spots ADD COLUMN latitude REAL")
    if "longitude" not in columns:
        connection.execute("ALTER TABLE spots ADD COLUMN longitude REAL")
    booking_columns = {row[1] for row in connection.execute("PRAGMA table_info(bookings)")}
    if "start_time" not in booking_columns:
        connection.execute("ALTER TABLE bookings ADD COLUMN start_time TEXT")
    if "end_time" not in booking_columns:
        connection.execute("ALTER TABLE bookings ADD COLUMN end_time TEXT")
    return connection


def _parse_time(value):
    return datetime.fromisoformat(value) if value else None


def _load():
    global _spot_counter, _booking_counter, current_user
    with _storage_lock:
        connection = _connect()
        try:
            current_user = None
            users_db.clear()
            for row in connection.execute(
                "SELECT username, password, role, full_name, email, phone, vehicle_type, vehicle_plate FROM users"
            ):
                user = User(*row)
                users_db[user.username] = user

            spots_db[:] = []
            ratings = {}
            for spot_id, rating in connection.execute("SELECT spot_id, rating FROM ratings"):
                ratings.setdefault(spot_id, []).append(rating)
            for row in connection.execute(
                "SELECT spot_id, location, price, host_username, total_slots, description, "
                "covered, ev_charging, vehicle_types, available_slots, is_active, photo, "
                "latitude, longitude FROM spots"
            ):
                spot = ParkingSpot(
                    row[0], row[1], row[2], row[3], row[4], row[5],
                    bool(row[6]), bool(row[7]), json.loads(row[8]),
                )
                spot.available_slots = row[9]
                spot.is_active = bool(row[10])
                spot.photo = row[11]
                spot.latitude = row[12]
                spot.longitude = row[13]
                spot.ratings = ratings.get(spot.spot_id, [])
                spots_db.append(spot)

            bookings_db[:] = []
            for row in connection.execute(
                "SELECT booking_id, spot_id, driver_username, planned_hours, price_per_hour, "
                "entry_time, slot_number, exit_time, billed_hours, final_cost, status, start_time, end_time "
                "FROM bookings ORDER BY booking_id"
            ):
                booking = Booking(
                    row[0], row[1], row[2], row[3], row[4], _parse_time(row[5]), row[6],
                    _parse_time(row[11]), _parse_time(row[12]),
                )
                booking.exit_time = _parse_time(row[7])
                booking.billed_hours = row[8]
                booking.final_cost = row[9]
                booking.status = row[10]
                bookings_db.append(booking)
            for spot in spots_db:
                spot.available_slots = available_slots_for_window(
                    spot, datetime.now(), datetime.now() + timedelta(hours=1)
                )

            activity_log[:] = [
                (_parse_time(row[0]), row[1])
                for row in connection.execute(
                    "SELECT created_at, text FROM activity_events ORDER BY event_id"
                )
            ]
            notifications[:] = [
                {"id": row[0], "username": row[1], "text": row[2],
                 "created_at": _parse_time(row[3]), "read": bool(row[4])}
                for row in connection.execute(
                    "SELECT notification_id, username, text, created_at, is_read "
                    "FROM notifications ORDER BY notification_id DESC"
                )
            ]
            password_reset_tokens.clear()
            for token_hash, username, expires_at in connection.execute(
                "SELECT token_hash, username, expires_at FROM password_reset_tokens"
            ):
                password_reset_tokens[token_hash] = (username, _parse_time(expires_at))
            _spot_counter = max((spot.spot_id for spot in spots_db), default=0)
            _booking_counter = max((booking.booking_id for booking in bookings_db), default=0)
        finally:
            connection.close()


def configure_storage(path):
    """Switch storage, primarily for isolated tests, and load that database."""
    global _database_path
    with _storage_lock:
        _database_path = str(path)
        _load()


def persist():
    with _storage_lock:
        connection = _connect()
        try:
            with connection:
                connection.execute("DELETE FROM users")
                connection.executemany(
                    "INSERT INTO users VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    [(user.username, user.password, user.role, user.full_name, user.email,
                      user.phone, user.vehicle_type, user.vehicle_plate)
                     for user in users_db.values()],
                )
                connection.execute("DELETE FROM spots")
                connection.executemany(
                    "INSERT INTO spots VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [(spot.spot_id, spot.location, spot.price_per_hour, spot.host_username,
                      spot.total_slots, spot.description, int(spot.covered), int(spot.ev_charging),
                      json.dumps(spot.vehicle_types), spot.available_slots, int(spot.is_active),
                      getattr(spot, "photo", ""), getattr(spot, "latitude", None),
                      getattr(spot, "longitude", None))
                     for spot in spots_db],
                )
                connection.execute("DELETE FROM ratings")
                connection.executemany(
                    "INSERT INTO ratings VALUES (?, ?)",
                    [(spot.spot_id, rating) for spot in spots_db for rating in spot.ratings],
                )
                connection.execute("DELETE FROM bookings")
                connection.executemany(
                    "INSERT INTO bookings VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [(booking.booking_id, booking.spot_id, booking.driver_username,
                      booking.planned_hours, booking.price_per_hour,
                      booking.entry_time.isoformat(), booking.slot_number,
                      booking.exit_time.isoformat() if booking.exit_time else None,
                      booking.billed_hours, booking.final_cost, booking.status,
                      booking.start_time.isoformat() if booking.start_time else None,
                      booking.end_time.isoformat() if booking.end_time else None)
                     for booking in bookings_db],
                )
                connection.execute("DELETE FROM activity_events")
                connection.executemany(
                    "INSERT INTO activity_events (created_at, text) VALUES (?, ?)",
                    [(timestamp.isoformat(), text) for timestamp, text in activity_log],
                )
                connection.execute("DELETE FROM notifications")
                connection.executemany(
                    "INSERT INTO notifications VALUES (?, ?, ?, ?, ?)",
                    [(item["id"], item["username"], item["text"],
                      item["created_at"].isoformat(), int(item["read"]))
                     for item in notifications],
                )
                connection.execute("DELETE FROM password_reset_tokens")
                connection.executemany(
                    "INSERT INTO password_reset_tokens VALUES (?, ?, ?)",
                    [(token_hash, username, expires_at.isoformat())
                     for token_hash, (username, expires_at) in password_reset_tokens.items()],
                )
        finally:
            connection.close()


def reset_data():
    global _spot_counter, _booking_counter, current_user
    users_db.clear()
    spots_db[:] = []
    bookings_db[:] = []
    activity_log[:] = []
    notifications[:] = []
    password_reset_tokens.clear()
    current_user = None
    _spot_counter = 0
    _booking_counter = 0
    persist()


def next_spot_id():
    global _spot_counter
    _spot_counter += 1
    return _spot_counter


def next_booking_id():
    global _booking_counter
    _booking_counter += 1
    return _booking_counter


def find_spot(spot_id):
    for spot in spots_db:
        if spot.spot_id == spot_id:
            return spot
    return None


def find_booking(booking_id):
    for booking in bookings_db:
        if booking.booking_id == booking_id:
            return booking
    return None


def active_bookings():
    return [booking for booking in bookings_db if booking.status == "Active"]


def total_slots():
    return sum(spot.total_slots for spot in spots_db if spot.is_active)


def slots_left():
    return sum(spot.available_slots for spot in spots_db if spot.is_active)


def log_event(text):
    activity_log.append((datetime.now(), text))
    persist()


def add_notification(username, text):
    next_id = max((item["id"] for item in notifications), default=0) + 1
    notifications.insert(0, {
        "id": next_id, "username": username, "text": text,
        "created_at": datetime.now(), "read": False,
    })


def overlap(booking, start_time, end_time):
    if booking.status != "Active":
        return False
    start = booking.start_time or booking.entry_time
    end = booking.end_time or (start + timedelta(hours=booking.planned_hours))
    if start <= datetime.now():
        end = datetime.max
    return start < end_time and start_time < end


def occupied_slots(spot_id, start_time=None, end_time=None):
    used = set()
    for booking in bookings_db:
        if booking.spot_id != spot_id or booking.slot_number is None:
            continue
        if start_time is None or end_time is None:
            if booking.status == "Active":
                used.add(booking.slot_number)
        elif overlap(booking, start_time, end_time):
            used.add(booking.slot_number)
    return used


def free_slot_for_window(spot, start_time, end_time):
    used = occupied_slots(spot.spot_id, start_time, end_time)
    for number in range(1, spot.total_slots + 1):
        if number not in used:
            return number
    return None


def next_free_slot(spot, start_time=None, end_time=None):
    start = start_time or datetime.now()
    end = end_time or (start + timedelta(hours=1))
    return free_slot_for_window(spot, start, end)


def available_slots_for_window(spot, start_time, end_time):
    return max(0, spot.total_slots - len(occupied_slots(spot.spot_id, start_time, end_time)))


def refresh_availability(spot):
    from datetime import timedelta
    now = datetime.now()
    spot.available_slots = available_slots_for_window(spot, now, now + timedelta(hours=1))


_load()
