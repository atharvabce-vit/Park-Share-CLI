import hmac
import hashlib
import json
import math
import os
import re
import secrets
import smtplib
import threading
import uuid
from datetime import datetime, timedelta
from email.message import EmailMessage
from functools import wraps
from pathlib import Path
from urllib.parse import quote

import click
from flask import Flask, abort, current_app, flash, jsonify, redirect, render_template, request, session, url_for

import auth_module
import billing_module
import data_store
import payment_module
import tomtom_parking
from models import ParkingSpot, Booking
from werkzeug.security import generate_password_hash


VEHICLE_TYPES = ("Car", "Motorcycle", "SUV", "Van")
booking_lock = threading.RLock()
reminder_dispatch_lock = threading.Lock()
MAX_PHOTO_BYTES = 5 * 1024 * 1024


def _default_start_time():
    candidate = datetime.now() + timedelta(minutes=5)
    minute = ((candidate.minute + 4) // 5) * 5
    if minute == 60:
        candidate = candidate.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    else:
        candidate = candidate.replace(minute=minute, second=0, microsecond=0)
    return candidate


def _parse_start_time(raw, default=None):
    if not raw:
        return default or datetime.now()
    try:
        return datetime.strptime(raw, "%Y-%m-%dT%H:%M")
    except ValueError:
        raise ValueError("Choose a valid booking start date and time.")


def _valid_password(password):
    return len(password) >= 8 and any(character.isdigit() for character in password)


def _coordinates(latitude_raw, longitude_raw):
    if not latitude_raw and not longitude_raw:
        return None, None
    try:
        latitude = float(latitude_raw)
        longitude = float(longitude_raw)
    except (TypeError, ValueError):
        raise ValueError("Choose a valid point on the map.")
    if not math.isfinite(latitude) or not math.isfinite(longitude):
        raise ValueError("Choose a valid point on the map.")
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        raise ValueError("The selected map coordinates are outside valid bounds.")
    return latitude, longitude


def _distance_km(latitude, longitude, other_latitude, other_longitude):
    earth_radius_km = 6371.0088
    lat1, lat2 = math.radians(latitude), math.radians(other_latitude)
    delta_lat = lat2 - lat1
    delta_lon = math.radians(other_longitude - longitude)
    value = (math.sin(delta_lat / 2) ** 2
             + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2)
    value = min(1.0, max(0.0, value))
    return earth_radius_km * 2 * math.atan2(math.sqrt(value), math.sqrt(1 - value))


def _demo_area_markers(latitude, longitude, radius_km):
    markers = []
    directions = (
        ("N", 0), ("NE", 45), ("E", 90), ("SE", 135),
        ("S", 180), ("SW", 225), ("W", 270), ("NW", 315),
    )
    earth_radius_km = 6371.0088
    for distance in range(5, int(radius_km) + 1, 5):
        angular_distance = distance / earth_radius_km
        lat1 = math.radians(latitude)
        lon1 = math.radians(longitude)
        for direction, bearing_degrees in directions:
            bearing = math.radians(bearing_degrees)
            lat2 = math.asin(
                math.sin(lat1) * math.cos(angular_distance)
                + math.cos(lat1) * math.sin(angular_distance) * math.cos(bearing)
            )
            lon2 = lon1 + math.atan2(
                math.sin(bearing) * math.sin(angular_distance) * math.cos(lat1),
                math.cos(angular_distance) - math.sin(lat1) * math.sin(lat2),
            )
            markers.append({
                "latitude": math.degrees(lat2),
                "longitude": (math.degrees(lon2) + 540) % 360 - 180,
                "label": "DEMO AREA PIN · " + str(distance) + " km " + direction,
                "distance_km": distance,
                "direction": direction,
                "demo": True,
            })
    return markers


def _clamp_score(value):
    return min(1.0, max(0.0, value))


def _save_photo(upload):
    if upload is None or not upload.filename:
        return ""
    original_name = upload.filename.lower()
    extension = Path(original_name).suffix
    contents = upload.stream.read(MAX_PHOTO_BYTES + 1)
    upload.stream.seek(0)
    if len(contents) > MAX_PHOTO_BYTES:
        raise ValueError("Listing photos must be 5 MB or smaller.")
    valid_signatures = {
        ".jpg": contents.startswith(b"\xff\xd8\xff"),
        ".jpeg": contents.startswith(b"\xff\xd8\xff"),
        ".png": contents.startswith(b"\x89PNG\r\n\x1a\n"),
        ".webp": contents.startswith(b"RIFF") and contents[8:12] == b"WEBP",
    }
    if not valid_signatures.get(extension, False):
        raise ValueError("Upload a valid JPG, PNG, or WebP image.")
    filename = uuid.uuid4().hex + extension
    directory = Path(current_app.config["UPLOAD_FOLDER"])
    directory.mkdir(parents=True, exist_ok=True)
    upload.save(str(directory / filename))
    return "uploads/" + filename


def _delete_photo(photo):
    if not photo or not photo.startswith("uploads/"):
        return
    path = (Path(current_app.static_folder) / photo).resolve()
    uploads = Path(current_app.config["UPLOAD_FOLDER"]).resolve()
    if path.parent == uploads and path.is_file():
        path.unlink()


def _send_password_reset(recipient, reset_url):
    host = os.environ.get("PARKSHARE_SMTP_HOST", "")
    sender = os.environ.get("PARKSHARE_SMTP_FROM", "")
    if not host or not sender:
        raise RuntimeError("Password-reset email is not configured.")
    message = EmailMessage()
    message["Subject"] = "Reset your ParkShare password"
    message["From"] = sender
    message["To"] = recipient
    message.set_content(
        "Use this link within 30 minutes to reset your ParkShare password:\n\n"
        + reset_url + "\n\nIf you did not request this, you can ignore this message."
    )
    port = int(os.environ.get("PARKSHARE_SMTP_PORT", "587"))
    with smtplib.SMTP(host, port, timeout=10) as smtp:
        smtp.starttls()
        username = os.environ.get("PARKSHARE_SMTP_USER", "")
        password = os.environ.get("PARKSHARE_SMTP_PASSWORD", "")
        if username:
            smtp.login(username, password)
        smtp.send_message(message)


def _send_email(recipient, subject, body):
    if not recipient:
        return False
    config = current_app.config
    if not config["SMTP_HOST"] or not config["SMTP_FROM"]:
        current_app.logger.info("Email not sent because SMTP is not configured.")
        return False
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = config["SMTP_FROM"]
    message["To"] = recipient
    message.set_content(body)
    try:
        with smtplib.SMTP(config["SMTP_HOST"], config["SMTP_PORT"], timeout=10) as smtp:
            smtp.starttls()
            if config["SMTP_USER"]:
                smtp.login(config["SMTP_USER"], config["SMTP_PASSWORD"])
            smtp.send_message(message)
    except (OSError, smtplib.SMTPException, ValueError):
        current_app.logger.exception("Unable to send ParkShare email to %s", recipient)
        return False
    return True


def _notify_booking_parties(booking, spot, subject, update):
    recipients = []
    host = data_store.users_db.get(spot.host_username)
    if host and host.email:
        recipients.append(host.email)
    driver = (
        data_store.users_db.get(booking.driver_username)
        if booking.driver_username != "Guest" else None
    )
    if driver and driver.email:
        recipients.append(driver.email)
    if not recipients:
        return
    details = (
        "Booking #" + str(booking.booking_id) + "\n"
        + "Parking: " + spot.location + "\n"
        + "Reserved from: " + booking.start_time.strftime("%d %b %Y, %I:%M %p") + "\n"
        + "Reserved until: " + booking.end_time.strftime("%d %b %Y, %I:%M %p") + "\n"
        + "Booking status: " + booking.status + "\n"
        + "Payment status: " + booking.payment_status + "\n\n"
        + update
    )
    for recipient in dict.fromkeys(recipients):
        _send_email(recipient, subject, details)


def send_due_booking_reminders(now=None):
    now = now or datetime.now()
    reminder_cutoff = now + timedelta(hours=24)
    sent = 0
    if not reminder_dispatch_lock.acquire(blocking=False):
        return 0
    try:
        with booking_lock:
            due_bookings = [
                booking for booking in data_store.bookings_db
                if booking.status == "Active" and booking.reminder_sent_at is None
                and now < booking.start_time <= reminder_cutoff
            ]
        for booking in due_bookings:
            with booking_lock:
                if booking.status != "Active" or booking.reminder_sent_at is not None:
                    continue
                driver = (
                    data_store.users_db.get(booking.driver_username)
                    if booking.driver_username != "Guest" else None
                )
                spot = data_store.find_spot(booking.spot_id)
                if driver is None or not driver.email or spot is None:
                    continue
                body = (
                    "Your ParkShare reservation starts within the next 24 hours.\n\n"
                    + "Booking #" + str(booking.booking_id) + "\n"
                    + "Parking: " + spot.location + "\n"
                    + "Start: " + booking.start_time.strftime("%d %b %Y, %I:%M %p") + "\n"
                    + "End: " + booking.end_time.strftime("%d %b %Y, %I:%M %p") + "\n"
                    + "Booking status: " + booking.status + "\n"
                    + "Payment status: " + booking.payment_status
                )
                recipient = driver.email
            if _send_email(recipient, "Upcoming ParkShare booking", body):
                with booking_lock:
                    if booking.status == "Active" and booking.reminder_sent_at is None:
                        booking.reminder_sent_at = now
                        data_store.persist()
                        sent += 1
    finally:
        reminder_dispatch_lock.release()
    return sent


def create_app(test_config=None):
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=os.environ.get("PARKSHARE_SECRET_KEY", secrets.token_hex(32)),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        MAX_CONTENT_LENGTH=MAX_PHOTO_BYTES + 1024 * 1024,
        UPLOAD_FOLDER=str(Path(app.static_folder) / "uploads"),
        DEMO_MODE=os.environ.get("PARKSHARE_DEMO_MODE", "").strip().lower()
        in ("1", "true", "yes", "on"),
        TOMTOM_API_KEY=os.environ.get("PARKSHARE_TOMTOM_API_KEY", "").strip(),
        PUBLIC_URL=os.environ.get(
            "PARKSHARE_PUBLIC_URL",
            "http://127.0.0.1:" + os.environ.get("PARKSHARE_PORT", "5000"),
        ),
        SMTP_HOST=os.environ.get("PARKSHARE_SMTP_HOST", "").strip(),
        SMTP_FROM=os.environ.get("PARKSHARE_SMTP_FROM", "").strip(),
        SMTP_PORT=int(os.environ.get("PARKSHARE_SMTP_PORT", "587")),
        SMTP_USER=os.environ.get("PARKSHARE_SMTP_USER", ""),
        SMTP_PASSWORD=os.environ.get("PARKSHARE_SMTP_PASSWORD", ""),
        RAZORPAY_KEY_ID=os.environ.get("PARKSHARE_RAZORPAY_KEY_ID", "").strip(),
        RAZORPAY_KEY_SECRET=os.environ.get("PARKSHARE_RAZORPAY_KEY_SECRET", "").strip(),
        RAZORPAY_WEBHOOK_SECRET=os.environ.get("PARKSHARE_RAZORPAY_WEBHOOK_SECRET", "").strip(),
        PAYMENT_MODE=os.environ.get("PARKSHARE_PAYMENT_MODE", "disabled").strip().lower(),
        ADMIN_USERNAMES=tuple(
            name.strip().casefold()
            for name in os.environ.get("PARKSHARE_ADMIN_USERNAMES", "").split(",")
            if name.strip()
        ),
    )
    if test_config:
        app.config.update(test_config)
    if app.config["PAYMENT_MODE"] not in ("disabled", "test", "live"):
        raise ValueError("PARKSHARE_PAYMENT_MODE must be disabled, test, or live.")
    if app.config["PAYMENT_MODE"] != "disabled":
        if not app.config["RAZORPAY_KEY_ID"] or not app.config["RAZORPAY_KEY_SECRET"]:
            raise RuntimeError("Razorpay key ID and secret are required when payments are enabled.")
        expected_prefix = "rzp_test_" if app.config["PAYMENT_MODE"] == "test" else "rzp_live_"
        if not app.config["RAZORPAY_KEY_ID"].startswith(expected_prefix):
            raise ValueError("Razorpay key ID does not match the configured payment mode.")
    app.config["PAYMENT_ENABLED"] = app.config["PAYMENT_MODE"] != "disabled"
    data_store.configure_builtin_listings(app.config["DEMO_MODE"])

    @app.cli.command("send-booking-reminders")
    def send_booking_reminders_command():
        if not app.config["SMTP_HOST"] or not app.config["SMTP_FROM"]:
            raise click.ClickException(
                "Configure PARKSHARE_SMTP_HOST and PARKSHARE_SMTP_FROM before sending reminders."
            )
        sent = send_due_booking_reminders()
        click.echo("Sent " + str(sent) + " booking reminder(s).")

    def current_user():
        username = session.get("username")
        if username is None:
            return None
        return data_store.users_db.get(username)

    def login_required(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if current_user() is None:
                session.clear()
                flash("Please sign in to continue.", "error")
                return redirect(url_for("login"))
            return view(*args, **kwargs)
        return wrapped

    def role_required(role):
        def decorate(view):
            @wraps(view)
            @login_required
            def wrapped(*args, **kwargs):
                if current_user().role != role:
                    flash("That page is not available for your account type.", "error")
                    return redirect(url_for("dashboard"))
                return view(*args, **kwargs)
            return wrapped
        return decorate

    def owns_booking(booking):
        user = current_user()
        if user is not None:
            return user.role == "Driver" and booking.driver_username == user.username
        guest_id = session.get("guest_id")
        return bool(guest_id and booking.guest_id == guest_id)

    def complete_checkout(booking, actor, spot, exit_time, hours, final_amount,
                          rating_raw="", payment_id=""):
        booking.exit_time = exit_time
        booking.billed_hours = hours
        booking.final_cost = final_amount
        booking.status = "Completed"
        if payment_id:
            booking.payment_id = payment_id
            booking.payment_status = "captured"
        data_store.refresh_availability(spot)
        if rating_raw:
            try:
                rating = int(rating_raw)
                if rating < 1 or rating > 5:
                    raise ValueError()
            except ValueError:
                flash("Checkout completed, but the rating must be from 1 to 5.", "error")
            else:
                spot.add_rating(rating)
                data_store.log_event(actor + " rated spot #" + str(spot.spot_id)
                                     + " " + str(rating) + "/5")
        data_store.log_event(actor + " released spot #" + str(spot.spot_id)
                             + " | Rs " + str(final_amount))
        data_store.add_notification(
            spot.host_username,
            actor + " completed booking #" + str(booking.booking_id) + ".",
        )
        data_store.persist()

    def notify_checkout_completion(booking, spot):
        payment_update = (
            "Payment was captured successfully."
            if booking.payment_status == "captured"
            else "Checkout is complete; ParkShare did not process an online payment."
        )
        _notify_booking_parties(
            booking, spot, "ParkShare checkout completed", payment_update
        )

    @app.before_request
    def protect_forms():
        if request.method == "POST":
            if request.endpoint == "razorpay_webhook":
                return None
            submitted = request.form.get("csrf_token", "")
            expected = session.get("_csrf_token", "")
            if not submitted or not expected or not hmac.compare_digest(submitted, expected):
                abort(400, description="Form expired or invalid. Reload the page and try again.")

    @app.before_request
    def enforce_login_rate_limit():
        if request.path != url_for("login"):
            return None
        if request.method != "POST":
            return None
        remote_ip = request.headers.get("X-Forwarded-For", request.remote_addr or "unknown")
        key = remote_ip.split(",")[0].strip() or "unknown"
        attempts = data_store.login_attempts.setdefault(key, [])
        now = datetime.now()
        attempts[:] = [stamp for stamp in attempts if now - stamp < timedelta(minutes=15)]
        if len(attempts) >= 5:
            flash("Too many sign-in attempts. Please wait 15 minutes and try again.", "error")
            abort(429, description="Too many login attempts.")
        return None

    @app.context_processor
    def template_context():
        def csrf_token():
            if "_csrf_token" not in session:
                session["_csrf_token"] = secrets.token_hex(32)
            return session["_csrf_token"]
        user = current_user()
        return {
            "csrf_token": csrf_token,
            "find_spot": data_store.find_spot,
            "find_user": lambda username: data_store.users_db.get(username),
            "signed_in_user": user,
            "guest_name": session.get("guest_name", ""),
            "has_guest_bookings": bool(session.get("guest_id")),
            "demo_mode": current_app.config["DEMO_MODE"],
            "tomtom_enabled": bool(current_app.config["TOMTOM_API_KEY"]),
            "payment_enabled": current_app.config["PAYMENT_ENABLED"],
            "razorpay_key_id": current_app.config["RAZORPAY_KEY_ID"],
            "admin_access": bool(
                user and user.username.casefold() in current_app.config["ADMIN_USERNAMES"]
            ),
            "unread_notifications": sum(
                1 for item in data_store.notifications
                if user and item["username"] == user.username and not item["read"]
            ),
            "map_url": lambda location: (
                "https://www.openstreetmap.org/search?query=" + quote(location)
            ),
        }

    @app.route("/")
    def index():
        if current_user():
            return redirect(url_for("dashboard"))
        active_spots = [spot for spot in data_store.spots_db if spot.is_active]
        available_slots = sum(spot.available_slots for spot in active_spots)
        return render_template(
            "landing.html", spot_count=len(active_spots), available_slots=available_slots
        )

    @app.route("/register", methods=["GET", "POST"])
    def register():
        if request.method == "POST":
            form_data = request.form
            role = request.form.get("role", "")
            if role not in ("Host", "Driver"):
                flash("Choose whether you are joining as a host or driver.", "error")
                return render_template("auth.html", mode="register", vehicle_types=VEHICLE_TYPES,
                                       form_data=form_data)
            if request.form.get("password") != request.form.get("confirm_password"):
                flash("Your passwords do not match.", "error")
                return render_template("auth.html", mode="register", vehicle_types=VEHICLE_TYPES,
                                       form_data=form_data)
            if request.form.get("terms") != "accepted":
                flash("Please accept the platform terms to create an account.", "error")
                return render_template("auth.html", mode="register", vehicle_types=VEHICLE_TYPES,
                                       form_data=form_data)
            try:
                user = auth_module.create_user(
                    request.form.get("username", ""),
                    request.form.get("password", ""),
                    role,
                    request.form.get("full_name", ""),
                    request.form.get("email", ""),
                    request.form.get("phone", ""),
                    request.form.get("vehicle_type", ""),
                    request.form.get("vehicle_plate", ""),
                )
            except ValueError as error:
                flash(str(error), "error")
                return render_template("auth.html", mode="register", vehicle_types=VEHICLE_TYPES,
                                       form_data=form_data)
            session.clear()
            session["username"] = user.username
            flash("Welcome to ParkShare, " + (user.full_name or user.username) + "!", "success")
            return redirect(url_for("dashboard"))
        return render_template("auth.html", mode="register", vehicle_types=VEHICLE_TYPES,
                               form_data={})

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            remote_ip = request.headers.get("X-Forwarded-For", request.remote_addr or "unknown")
            key = remote_ip.split(",")[0].strip() or "unknown"
            attempts = data_store.login_attempts.setdefault(key, [])
            now = datetime.now()
            attempts[:] = [stamp for stamp in attempts if now - stamp < timedelta(minutes=15)]
            user = auth_module.authenticate(username, password)
            if user is None:
                attempts.append(now)
                flash("We couldn't match that username and password.", "error")
            else:
                attempts.clear()
                session.clear()
                session["username"] = user.username
                data_store.log_event(user.username + " logged in")
                flash("Good to see you again, " + (user.full_name or user.username) + ".", "success")
                return redirect(url_for("dashboard"))
        return render_template("auth.html", mode="login", vehicle_types=VEHICLE_TYPES)

    @app.route("/logout", methods=["POST"])
    @login_required
    def logout():
        user = current_user()
        data_store.log_event(user.username + " logged out")
        session.clear()
        flash("You have been signed out.", "success")
        return redirect(url_for("index"))

    @app.route("/forgot-password", methods=["GET", "POST"])
    def forgot_password():
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            user = next((candidate for candidate in data_store.users_db.values()
                         if candidate.email.lower() == email and email), None)
            if user:
                token = secrets.token_urlsafe(32)
                token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
                for old_hash, (username, unused_expiry) in list(data_store.password_reset_tokens.items()):
                    if username == user.username:
                        del data_store.password_reset_tokens[old_hash]
                data_store.password_reset_tokens[token_hash] = (
                    user.username, datetime.now() + timedelta(minutes=30)
                )
                data_store.persist()
                reset_url = current_app.config["PUBLIC_URL"].rstrip("/") + url_for(
                    "reset_password", token=token
                )
                sender = app.config.get("PASSWORD_RESET_SENDER", _send_password_reset)
                try:
                    sender(user.email, reset_url)
                    data_store.log_event(user.username + " requested a password reset")
                except (OSError, RuntimeError, smtplib.SMTPException, ValueError):
                    data_store.password_reset_tokens.pop(token_hash, None)
                    data_store.persist()
                    app.logger.exception("Unable to send a password-reset email")
            flash("If an account uses that email, password-reset instructions will be sent.", "success")
            return redirect(url_for("forgot_password"))
        return render_template("forgot_password.html")

    @app.route("/reset-password/<token>", methods=["GET", "POST"])
    def reset_password(token):
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        token_data = data_store.password_reset_tokens.get(token_hash)
        if not token_data or token_data[1] <= datetime.now():
            data_store.password_reset_tokens.pop(token_hash, None)
            data_store.persist()
            flash("That password-reset link is invalid or expired.", "error")
            return redirect(url_for("forgot_password"))
        if request.method == "POST":
            password = request.form.get("password", "")
            if not _valid_password(password):
                flash("Password must be at least 8 characters and include a number.", "error")
            elif password != request.form.get("confirm_password", ""):
                flash("Your passwords do not match.", "error")
            else:
                username = token_data[0]
                data_store.users_db[username].password = generate_password_hash(password)
                del data_store.password_reset_tokens[token_hash]
                data_store.log_event(username + " reset their password")
                flash("Your password has been updated. Please sign in.", "success")
                return redirect(url_for("login"))
        return render_template("reset_password.html")

    @app.route("/profile", methods=["GET", "POST"])
    @login_required
    def profile():
        user = current_user()
        if user.is_suspended:
            flash("This account is suspended: " + (user.suspended_reason or "Account suspended by an administrator."), "error")
            session.clear()
            return redirect(url_for("login"))
        if request.method == "POST":
            full_name = request.form.get("full_name", "").strip()
            email = request.form.get("email", "").strip().lower()
            phone = request.form.get("phone", "").strip()
            vehicle_type = request.form.get("vehicle_type", "")
            vehicle_plate = request.form.get("vehicle_plate", "").strip().upper()
            if not full_name or len(full_name) > 80 or len(phone) > 30 or len(vehicle_plate) > 20:
                flash("Enter a name and keep profile fields within their length limits.", "error")
            elif email and (len(email) > 254 or not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email)):
                flash("Enter a valid email address.", "error")
            elif any(candidate.username != user.username and candidate.email.lower() == email
                     for candidate in data_store.users_db.values() if email):
                flash("An account already uses that email address.", "error")
            elif user.role == "Driver" and vehicle_type not in VEHICLE_TYPES:
                flash("Choose a supported vehicle type.", "error")
            else:
                user.full_name = full_name
                user.email = email
                user.phone = phone
                if user.role == "Driver":
                    user.vehicle_type = vehicle_type
                    user.vehicle_plate = vehicle_plate
                data_store.log_event(user.username + " updated their profile")
                flash("Your profile has been updated.", "success")
                return redirect(url_for("profile"))
        return render_template("profile.html", vehicle_types=VEHICLE_TYPES)

    @app.route("/account/delete", methods=["POST"])
    @login_required
    def delete_account():
        user = current_user()
        if auth_module.authenticate(user.username, request.form.get("current_password", "")) is None:
            flash("Enter your current password to confirm account deletion.", "error")
            return redirect(url_for("profile"))
        active = [booking for booking in data_store.active_bookings()
                  if booking.driver_username == user.username
                  or (data_store.find_spot(booking.spot_id)
                      and data_store.find_spot(booking.spot_id).host_username == user.username)]
        if active:
            flash("Complete or cancel your active bookings before deleting your account.", "error")
            return redirect(url_for("profile"))
        for spot in data_store.spots_db:
            if spot.host_username == user.username:
                data_store.remove_spot_favorites(spot.spot_id)
                spot.is_active = False
                _delete_photo(getattr(spot, "photo", ""))
                spot.photo = ""
                spot.host_username = "Deleted host"
        for booking in data_store.bookings_db:
            if booking.driver_username == user.username:
                booking.driver_username = "Deleted driver"
        for token_hash, (username, unused_expiry) in list(data_store.password_reset_tokens.items()):
            if username == user.username:
                del data_store.password_reset_tokens[token_hash]
        data_store.reviews[:] = [
            review for review in data_store.reviews
            if review["reviewer"] != user.username and review["target"] != user.username
        ]
        for report in data_store.reports:
            if report["reporter"] == user.username:
                report["reporter"] = "Deleted user"
            if report["target_type"] == "user" and report["target_id"] == user.username:
                report["target_id"] = "Deleted user"
        data_store.notifications[:] = [
            item for item in data_store.notifications if item["username"] != user.username
        ]
        data_store.saved_spots.difference_update(
            {favorite for favorite in data_store.saved_spots
             if favorite[0] == user.username}
        )
        data_store.activity_log[:] = [
            (timestamp, text.replace(user.username, "Deleted user"))
            for timestamp, text in data_store.activity_log
        ]
        del data_store.users_db[user.username]
        data_store.log_event("An account was deleted")
        session.clear()
        flash("Your account has been deleted.", "success")
        return redirect(url_for("index"))

    @app.route("/notifications")
    @login_required
    def notifications_page():
        user = current_user()
        mine = [item for item in data_store.notifications if item["username"] == user.username]
        return render_template("notifications.html", notifications=mine)

    @app.route("/notifications/<int:notification_id>/read", methods=["POST"])
    @login_required
    def mark_notification_read(notification_id):
        user = current_user()
        item = next((notification for notification in data_store.notifications
                     if notification["id"] == notification_id
                     and notification["username"] == user.username), None)
        if item is None:
            abort(404)
        item["read"] = True
        data_store.persist()
        return redirect(url_for("notifications_page"))

    @app.route("/dashboard")
    @login_required
    def dashboard():
        user = current_user()
        if user.is_suspended:
            flash("This account is suspended: " + (user.suspended_reason or "Account suspended by an administrator."), "error")
            session.clear()
            return redirect(url_for("login"))
        active = [booking for booking in data_store.active_bookings()
                  if user.role == "Host"
                  and data_store.find_spot(booking.spot_id).host_username == user.username]
        if user.role == "Driver":
            active = [booking for booking in data_store.active_bookings()
                      if booking.driver_username == user.username]
        my_spots = [spot for spot in data_store.spots_db
                    if spot.host_username == user.username and spot.is_active]
        return render_template(
            "dashboard.html",
            active_bookings=active,
            my_spots=my_spots,
            available_spots=[spot for spot in data_store.spots_db
                             if spot.is_active and spot.has_slots()],
        )

    @app.route("/spots")
    def spots():
        user = current_user()
        keyword = request.args.get("location", "").strip().lower()
        max_price_raw = request.args.get("max_price", "").strip()
        covered = request.args.get("covered") == "1"
        ev_charging = request.args.get("ev_charging") == "1"
        requested_vehicle = request.args.get("vehicle_type")
        vehicle_type = requested_vehicle if requested_vehicle is not None else (
            user.vehicle_type or "Any" if user else "Any"
        )
        if vehicle_type not in VEHICLE_TYPES and vehicle_type != "Any":
            vehicle_type = user.vehicle_type or "Any" if user else "Any"
        sort_by = request.args.get("sort", "recommended")
        requested_hours = request.args.get("hours", "1")
        try:
            requested_hours = int(requested_hours)
            if requested_hours < 1 or requested_hours > 168:
                raise ValueError()
        except ValueError:
            requested_hours = 1
            flash("Booking duration must be between 1 and 168 hours.", "error")
        try:
            start_time = _parse_start_time(
                request.args.get("start_time", ""),
                _default_start_time(),
            )
            if start_time < datetime.now() - timedelta(minutes=1) or start_time > datetime.now() + timedelta(days=30):
                raise ValueError("Choose a start time within the next 30 days.")
        except ValueError as error:
            flash(str(error), "error")
            start_time = _default_start_time()
        end_time = start_time + timedelta(hours=requested_hours)
        availability = {}
        distances = {}
        recommendation_scores = {}
        near_latitude = None
        near_longitude = None
        nearby_raw = request.args.get("near_lat", "").strip()
        nearby_lon_raw = request.args.get("near_lon", "").strip()
        try:
            near_latitude, near_longitude = _coordinates(nearby_raw, nearby_lon_raw)
        except ValueError as error:
            flash(str(error), "error")
        try:
            radius_km = float(request.args.get("radius_km", "20"))
            if not math.isfinite(radius_km) or radius_km not in (5, 10, 15, 20):
                raise ValueError()
        except ValueError:
            radius_km = 20
            flash("Choose a search radius from 5, 10, 15, or 20 km.", "error")
        max_price = None
        if max_price_raw:
            try:
                max_price = float(max_price_raw)
                if not math.isfinite(max_price) or max_price < 0:
                    raise ValueError()
            except ValueError:
                flash("Maximum price must be a positive number.", "error")
                max_price_raw = ""

        results = []
        if near_latitude is not None:
            keyword = ""
        for spot in data_store.spots_db:
            available = data_store.available_slots_for_window(spot, start_time, end_time)
            availability[spot.spot_id] = available
            if not spot.is_active or not available:
                continue
            if vehicle_type != "Any" and vehicle_type not in spot.vehicle_types:
                continue
            if keyword and keyword not in spot.location.lower():
                continue
            if near_latitude is not None:
                if spot.latitude is None or spot.longitude is None:
                    continue
                distance = _distance_km(
                    near_latitude, near_longitude, spot.latitude, spot.longitude
                )
                if distance > radius_km:
                    continue
                distances[spot.spot_id] = distance
            elif spot.latitude is not None and spot.longitude is not None:
                distances[spot.spot_id] = None
            if max_price is not None and spot.price_per_hour > max_price:
                continue
            if covered and not spot.covered:
                continue
            if ev_charging and not spot.ev_charging:
                continue
            results.append(spot)
        lowest_price = min((spot.price_per_hour for spot in results), default=0)
        highest_price = max((spot.price_per_hour for spot in results), default=0)
        for spot in results:
            distance_score = (
                _clamp_score(1 - distances[spot.spot_id] / radius_km)
                if near_latitude is not None else 0.5
            )
            availability_score = availability[spot.spot_id] / spot.total_slots
            price_score = (
                1 - (spot.price_per_hour - lowest_price) / (highest_price - lowest_price)
                if highest_price > lowest_price else 1.0
            )
            rating = spot.average_rating()
            rating_score = rating / 5 if rating is not None else 0.5
            recommendation_scores[spot.spot_id] = round(
                (distance_score * 0.35 + availability_score * 0.25
                 + price_score * 0.20 + rating_score * 0.20) * 100
            )
        map_markers = [
            {
                "latitude": spot.latitude,
                "longitude": spot.longitude,
                "label": spot.location,
                "price": format(spot.price_per_hour, ".2f"),
                "vehicle_types": spot.vehicle_types,
                "url": url_for("spots", location=spot.location),
                "demo": False,
                "sample": spot.is_demo,
            }
            for spot in results if spot.latitude is not None and spot.longitude is not None
        ]
        demo_markers = (
            _demo_area_markers(near_latitude, near_longitude, radius_km)
            if near_latitude is not None else []
        )
        map_markers.extend(demo_markers)
        provider_facilities = []
        if current_app.config["TOMTOM_API_KEY"] and near_latitude is not None:
            try:
                provider_facilities = tomtom_parking.search_nearby(
                    near_latitude,
                    near_longitude,
                    radius_km,
                    current_app.config["TOMTOM_API_KEY"],
                )
            except tomtom_parking.TomTomParkingError as error:
                app.logger.warning("TomTom parking search failed: %s", error)
                flash(
                    "Live parking data could not be loaded. Host listings are still available.",
                    "error",
                )
            map_markers.extend(
                {
                    "latitude": facility["latitude"],
                    "longitude": facility["longitude"],
                    "label": facility["name"],
                    "provider": "TomTom",
                    "external": True,
                }
                for facility in provider_facilities
            )
        if sort_by == "price":
            results.sort(key=lambda item: item.price_per_hour)
        elif sort_by == "rating":
            results.sort(key=lambda item: item.average_rating() or 0, reverse=True)
        elif sort_by == "availability":
            results.sort(key=lambda item: availability[item.spot_id], reverse=True)
        else:
            results.sort(key=lambda item: recommendation_scores[item.spot_id], reverse=True)
        host_reviews = {}
        for review in data_store.reviews:
            if not review["visible"]:
                continue
            summary = host_reviews.setdefault(review["target"], {"ratings": [], "reviews": []})
            summary["ratings"].append(review["rating"])
            summary["reviews"].append(review)
        for summary in host_reviews.values():
            summary["average"] = round(
                sum(summary["ratings"]) / len(summary["ratings"]), 1
            )
            summary["reviews"] = sorted(
                summary["reviews"], key=lambda item: item["created_at"], reverse=True
            )[:2]
        return render_template(
            "spots.html",
            spots=results,
            availability=availability,
            distances=distances,
            recommendation_scores=recommendation_scores,
            map_markers=map_markers,
            provider_facilities=provider_facilities,
            demo_markers=demo_markers,
            vehicle_types=VEHICLE_TYPES,
            active_spot_count=sum(1 for spot in data_store.spots_db if spot.is_active),
            host_reviews=host_reviews,
            saved_spot_ids={
                spot_id for username, spot_id in data_store.saved_spots
                if user and username == user.username
            },
            filters={"location": request.args.get("location", ""),
                     "max_price": max_price_raw,
                     "covered": covered,
                     "ev_charging": ev_charging,
                     "vehicle_type": vehicle_type,
                     "sort": sort_by,
                     "start_time": start_time.strftime("%Y-%m-%dT%H:%M"),
                     "hours": requested_hours,
                     "minimum_start": datetime.now().strftime("%Y-%m-%dT%H:%M"),
                     "near_lat": "" if near_latitude is None else format(near_latitude, ".6f"),
                     "near_lon": "" if near_longitude is None else format(near_longitude, ".6f"),
                     "radius_km": int(radius_km)},
        )

    @app.route("/favorites")
    @role_required("Driver")
    def favorites():
        username = current_user().username
        saved = [
            spot for spot in data_store.spots_db
            if (username, spot.spot_id) in data_store.saved_spots
            and spot.is_active and not spot.is_demo
        ]
        saved.sort(key=lambda spot: spot.location.casefold())
        return render_template("favorites.html", spots=saved)

    @app.route("/favorites/<int:spot_id>/toggle", methods=["POST"])
    @role_required("Driver")
    def toggle_favorite(spot_id):
        spot = data_store.find_spot(spot_id)
        if spot is None or not spot.is_active or spot.is_demo:
            abort(404)
        favorite = (current_user().username, spot_id)
        if favorite in data_store.saved_spots:
            data_store.saved_spots.remove(favorite)
            flash("Listing removed from your saved spots.", "success")
        else:
            data_store.saved_spots.add(favorite)
            flash("Listing saved for later.", "success")
        data_store.persist()
        destination = request.form.get("return_to")
        if destination == "favorites":
            return redirect(url_for("favorites"))
        return redirect(url_for("spots"))

    @app.route("/host/spots", methods=["GET", "POST"])
    @role_required("Host")
    def host_spots():
        user = current_user()
        if request.method == "POST":
            location = request.form.get("location", "").strip()
            description = request.form.get("description", "").strip()
            try:
                price = float(request.form.get("price_per_hour", ""))
                slots = int(request.form.get("total_slots", ""))
                latitude, longitude = _coordinates(
                    request.form.get("latitude", ""), request.form.get("longitude", "")
                )
            except ValueError:
                flash("Enter a valid price, number of spaces, and map location.", "error")
                return redirect(url_for("host_spots"))
            if latitude is None or longitude is None:
                flash("Pin the parking entrance on the map before publishing.", "error")
                return redirect(url_for("host_spots"))
            if not location or not math.isfinite(price) or price < 1 or slots < 1 or slots > 100:
                flash("Location is required, price must be at least ₹1, and spaces must be between 1 and 100.", "error")
                return redirect(url_for("host_spots"))
            types = [item for item in VEHICLE_TYPES if item in request.form.getlist("vehicle_types")]
            if not types:
                flash("Select at least one vehicle type for this listing.", "error")
                return redirect(url_for("host_spots"))
            try:
                photo = _save_photo(request.files.get("photo"))
            except ValueError as error:
                flash(str(error), "error")
                return redirect(url_for("host_spots"))
            spot = ParkingSpot(
                data_store.next_spot_id(), location, price, user.username, slots,
                description, request.form.get("covered") == "on",
                request.form.get("ev_charging") == "on", types, photo, latitude, longitude,
            )
            data_store.spots_db.append(spot)
            data_store.log_event(user.username + " listed spot #" + str(spot.spot_id) + " at " + location)
            flash("Your parking spot is now live.", "success")
            return redirect(url_for("host_spots"))
        mine = [spot for spot in data_store.spots_db
                if spot.host_username == user.username and spot.is_active]
        return render_template("host_spots.html", spots=mine, vehicle_types=VEHICLE_TYPES)

    @app.route("/host/spots/<int:spot_id>/remove", methods=["POST"])
    @role_required("Host")
    def remove_spot(spot_id):
        spot = data_store.find_spot(spot_id)
        if spot is None or spot.host_username != current_user().username or not spot.is_active:
            abort(404)
        if data_store.occupied_slots(spot_id):
            flash("A spot with an active vehicle cannot be removed.", "error")
        else:
            spot.is_active = False
            data_store.remove_spot_favorites(spot_id)
            _delete_photo(getattr(spot, "photo", ""))
            spot.photo = ""
            data_store.log_event(current_user().username + " removed spot #" + str(spot_id))
            flash("The listing has been removed.", "success")
        return redirect(url_for("host_spots"))

    @app.route("/host/spots/<int:spot_id>/edit", methods=["POST"])
    @role_required("Host")
    def edit_spot(spot_id):
        spot = data_store.find_spot(spot_id)
        if spot is None or spot.host_username != current_user().username or not spot.is_active:
            abort(404)
        location = request.form.get("location", "").strip()
        description = request.form.get("description", "").strip()
        try:
            price = float(request.form.get("price_per_hour", ""))
            latitude, longitude = _coordinates(
                request.form.get("latitude", ""), request.form.get("longitude", "")
            )
        except ValueError:
            flash("Enter a valid hourly price and map location.", "error")
            return redirect(url_for("host_spots"))
        types = [item for item in VEHICLE_TYPES if item in request.form.getlist("vehicle_types")]
        if not location or not math.isfinite(price) or price < 1 or not types:
            flash("A location, price of at least ₹1, and one vehicle type are required.", "error")
            return redirect(url_for("host_spots"))
        upload = request.files.get("photo")
        try:
            photo = _save_photo(upload) if upload and upload.filename else None
        except ValueError as error:
            flash(str(error), "error")
            return redirect(url_for("host_spots"))
        spot.location = location
        spot.description = description
        spot.price_per_hour = price
        spot.vehicle_types = types
        spot.covered = request.form.get("covered") == "on"
        spot.ev_charging = request.form.get("ev_charging") == "on"
        if photo:
            _delete_photo(getattr(spot, "photo", ""))
            spot.photo = photo
        spot.latitude = latitude
        spot.longitude = longitude
        data_store.log_event(current_user().username + " updated spot #" + str(spot_id))
        flash("Listing details updated.", "success")
        return redirect(url_for("host_spots"))

    @app.route("/bookings")
    def bookings():
        user = current_user()
        if user is None:
            guest_id = session.get("guest_id", "")
            mine = [booking for booking in data_store.bookings_db
                    if guest_id and booking.guest_id == guest_id]
        elif user.role == "Driver":
            mine = [booking for booking in data_store.bookings_db
                    if booking.driver_username == user.username]
        else:
            mine = [booking for booking in data_store.bookings_db
                    if data_store.find_spot(booking.spot_id).host_username == user.username]
        now = datetime.now()
        checkin_allowed = {
            booking.booking_id for booking in mine
            if booking.status == "Active"
            and booking.start_time <= now <= booking.end_time
        }
        reviewed_bookings = {
            review["booking_id"] for review in data_store.reviews
            if user and review["reviewer"] == user.username
        }
        return render_template(
            "bookings.html", bookings=mine, checkin_allowed=checkin_allowed,
            reviewed_bookings=reviewed_bookings,
        )

    @app.route("/bookings/create", methods=["POST"])
    def create_booking():
        user = current_user()
        if user and user.role != "Driver":
            flash("Hosts cannot book parking spaces from a host account.", "error")
            return redirect(url_for("spots"))
        try:
            spot_id = int(request.form.get("spot_id", ""))
            hours = int(request.form.get("hours", ""))
            start_time = _parse_start_time(request.form.get("start_time", ""))
        except ValueError:
            flash("Choose a spot, valid start time, and whole-number duration.", "error")
            return redirect(url_for("spots"))
        if hours < 1 or hours > 168:
            flash("Booking duration must be between 1 and 168 hours.", "error")
            return redirect(url_for("spots"))
        if start_time < datetime.now() - timedelta(minutes=1) or start_time > datetime.now() + timedelta(days=30):
            flash("Choose a start time within the next 30 days.", "error")
            return redirect(url_for("spots"))
        end_time = start_time + timedelta(hours=hours)
        guest_name = ""
        guest_id = ""
        if user is None:
            guest_name = request.form.get("guest_name", "").strip()
            if not guest_name or len(guest_name) > 80:
                flash("Enter your name (up to 80 characters) to make a guest booking.", "error")
                return redirect(url_for("spots"))
            vehicle_type = request.form.get("vehicle_type", "")
            if vehicle_type not in VEHICLE_TYPES:
                flash("Choose a supported vehicle type for your guest booking.", "error")
                return redirect(url_for("spots"))
            guest_id = session.setdefault("guest_id", uuid.uuid4().hex)
            session["guest_name"] = guest_name
        else:
            vehicle_type = user.vehicle_type
        with booking_lock:
            spot = data_store.find_spot(spot_id)
            if spot is None or not spot.is_active or (
                spot.is_demo and not current_app.config["DEMO_MODE"]
            ):
                flash("That spot is no longer available.", "error")
                return redirect(url_for("spots"))
            if vehicle_type and vehicle_type not in spot.vehicle_types:
                flash("This spot does not support your registered vehicle type.", "error")
                return redirect(url_for("spots"))
            slot_number = data_store.free_slot_for_window(spot, start_time, end_time)
            if slot_number is None:
                flash("No space is available for that time range.", "error")
                return redirect(url_for("spots"))
            booking = Booking(
                data_store.next_booking_id(), spot.spot_id,
                user.username if user else "Guest", hours,
                spot.price_per_hour, start_time, slot_number, start_time, end_time,
                guest_name, guest_id,
            )
            data_store.bookings_db.append(booking)
            data_store.refresh_availability(spot)
            data_store.add_notification(
                spot.host_username,
                (user.username if user else "A guest") + " booked " + spot.location + " from "
                + start_time.strftime("%d %b %Y %I:%M %p") + ".",
            )
            data_store.log_event((user.username if user else "A guest") + " booked spot #" + str(spot.spot_id)
                                 + " (slot " + str(slot_number) + ")")
        _notify_booking_parties(
            booking, spot, "ParkShare booking confirmed",
            "Your parking reservation is confirmed.",
        )
        flash("Booking confirmed — your assigned space is #" + str(slot_number) + ".", "success")
        return redirect(url_for("bookings"))

    @app.route("/bookings/<int:booking_id>/cancel", methods=["POST"])
    def cancel_booking(booking_id):
        with booking_lock:
            booking = data_store.find_booking(booking_id)
            if booking is None or booking.status != "Active" or not owns_booking(booking):
                abort(404)
            if booking.payment_status != "unpaid":
                flash("Finish or retry the pending payment before cancelling this booking.", "error")
                return redirect(url_for("bookings"))
            if datetime.now() >= booking.start_time:
                flash("A booking cannot be cancelled after its start time.", "error")
                return redirect(url_for("bookings"))
            user = current_user()
            actor = user.username if user else "Guest"
            booking.status = "Cancelled"
            spot = data_store.find_spot(booking.spot_id)
            data_store.refresh_availability(spot)
            data_store.add_notification(
                spot.host_username,
                actor + " cancelled booking #" + str(booking_id) + ".",
            )
            data_store.log_event(actor + " cancelled booking #" + str(booking_id))
        _notify_booking_parties(
            booking, spot, "ParkShare booking cancelled",
            "This reservation was cancelled. No charge was made.",
        )
        flash("Booking cancelled without charge.", "success")
        return redirect(url_for("bookings"))

    @app.route("/bookings/<int:booking_id>/check-in", methods=["POST"])
    def check_in(booking_id):
        with booking_lock:
            booking = data_store.find_booking(booking_id)
            if booking is None or booking.status != "Active" or not owns_booking(booking):
                abort(404)
            now = datetime.now()
            if now < booking.start_time or now > booking.end_time:
                flash("Check-in is available only during the reserved time window.", "error")
                return redirect(url_for("bookings"))
            booking.status = "Checked In"
            booking.checkin_time = now
            booking.entry_time = now
            spot = data_store.find_spot(booking.spot_id)
            actor = current_user().username if current_user() else booking.guest_name or "A guest"
            data_store.log_event(actor + " checked in for booking #" + str(booking_id))
            data_store.add_notification(
                spot.host_username, actor + " checked in for booking #" + str(booking_id) + "."
            )
            data_store.refresh_availability(spot)
            data_store.persist()
        _notify_booking_parties(
            booking, spot, "ParkShare check-in recorded",
            "Check-in has been recorded for this reservation.",
        )
        flash("You are checked in. Enjoy your stay.", "success")
        return redirect(url_for("bookings"))

    @app.route("/bookings/<int:booking_id>/checkout", methods=["POST"])
    def checkout(booking_id):
        with booking_lock:
            booking = data_store.find_booking(booking_id)
            if (booking is None or booking.status not in ("Active", "Checked In")
                    or not owns_booking(booking)):
                abort(404)
            user = current_user()
            actor = user.username if user else "Guest"
            if booking.start_time and datetime.now() < booking.start_time:
                flash("You cannot check out before your booking starts.", "error")
                return redirect(url_for("bookings"))
            spot = data_store.find_spot(booking.spot_id)
            if current_app.config["PAYMENT_ENABLED"]:
                if booking.payment_status == "created":
                    amount_paise = booking.payment_amount
                    exit_time = booking.exit_time
                    hours = booking.billed_hours
                    final_amount = booking.final_cost
                    order_id = booking.payment_order_id
                else:
                    exit_time = datetime.now()
                    amount, hours, unused_overtime = billing_module.final_cost(
                        booking.price_per_hour, booking.entry_time, exit_time,
                        booking.planned_hours,
                    )
                    promo = request.form.get("promo_code", "").strip()
                    final_amount, unused_discount, unused_rate = billing_module.apply_discount(
                        amount, promo
                    )
                    amount_paise = int(round(final_amount * 100))
                    if amount_paise < 100:
                        return jsonify({
                            "error": "Razorpay requires a minimum charge of ₹1. Remove the promo code and retry."
                        }), 400
                    try:
                        order = payment_module.create_order(
                            current_app.config["RAZORPAY_KEY_ID"],
                            current_app.config["RAZORPAY_KEY_SECRET"],
                            booking.booking_id,
                            amount_paise,
                        )
                    except payment_module.PaymentError:
                        app.logger.exception("Unable to create Razorpay order for booking %s",
                                             booking.booking_id)
                        return jsonify({"error": "Payment service is unavailable. Try again shortly."}), 502
                    order_id = order.get("id", "")
                    if (not order_id or order.get("amount") != amount_paise
                            or order.get("currency") != "INR"):
                        app.logger.error("Razorpay returned an invalid order for booking %s",
                                         booking.booking_id)
                        return jsonify({"error": "Payment service returned an invalid order."}), 502
                    booking.exit_time = exit_time
                    booking.billed_hours = hours
                    booking.final_cost = final_amount
                    booking.payment_amount = amount_paise
                    booking.payment_order_id = order_id
                    booking.payment_status = "created"
                    data_store.persist()
                return jsonify({
                    "key_id": current_app.config["RAZORPAY_KEY_ID"],
                    "order_id": order_id,
                    "amount": amount_paise,
                    "currency": "INR",
                    "verify_url": url_for("verify_checkout_payment", booking_id=booking_id),
                })

            exit_time = datetime.now()
            amount, hours, unused_overtime = billing_module.final_cost(
                booking.price_per_hour, booking.entry_time, exit_time, booking.planned_hours,
            )
            promo = request.form.get("promo_code", "").strip()
            final_amount, discount, unused_rate = billing_module.apply_discount(amount, promo)
            rating_raw = request.form.get("rating", "")
            complete_checkout(booking, actor, spot, exit_time, hours, final_amount, rating_raw)
        notify_checkout_completion(booking, spot)
        if promo and not discount:
            flash("Checkout complete. That promo code was not recognized.", "error")
        else:
            flash("Checkout complete — final total: ₹" + format(final_amount, ".2f") + ".", "success")
        return redirect(url_for("bookings"))

    @app.route("/bookings/<int:booking_id>/payment/verify", methods=["POST"])
    def verify_checkout_payment(booking_id):
        with booking_lock:
            booking = data_store.find_booking(booking_id)
            if booking is None or not owns_booking(booking):
                abort(404)
            order_id = request.form.get("razorpay_order_id", "")
            payment_id = request.form.get("razorpay_payment_id", "")
            signature = request.form.get("razorpay_signature", "")
            if (booking.status == "Completed" and booking.payment_status == "captured"
                    and order_id == booking.payment_order_id and payment_id == booking.payment_id):
                flash("Payment received and checkout complete.", "success")
                return redirect(url_for("bookings"))
            if booking.status not in ("Active", "Checked In"):
                abort(404)
            if (booking.payment_status != "created"
                    or order_id != booking.payment_order_id
                    or not payment_module.verify_checkout_signature(
                        current_app.config["RAZORPAY_KEY_ID"],
                        current_app.config["RAZORPAY_KEY_SECRET"],
                        order_id,
                        payment_id,
                        signature,
                    )):
                abort(400, description="Payment verification failed.")
            try:
                payment = payment_module.fetch_payment(
                    current_app.config["RAZORPAY_KEY_ID"],
                    current_app.config["RAZORPAY_KEY_SECRET"],
                    payment_id,
                )
            except payment_module.PaymentError:
                app.logger.exception("Unable to verify Razorpay payment %s", payment_id)
                return jsonify({"error": "Payment verification is temporarily unavailable."}), 502
            if (payment.get("order_id") != order_id
                    or payment.get("amount") != booking.payment_amount
                    or payment.get("currency") != "INR"
                    or payment.get("status") != "captured"):
                abort(400, description="Payment has not been captured for this booking.")
            spot = data_store.find_spot(booking.spot_id)
            actor = current_user().username if current_user() else booking.guest_name or "A guest"
            complete_checkout(
                booking, actor, spot, booking.exit_time, booking.billed_hours,
                booking.final_cost, request.form.get("rating", ""), payment_id,
            )
        notify_checkout_completion(booking, spot)
        flash("Payment received and checkout complete.", "success")
        return redirect(url_for("bookings"))

    @app.route("/bookings/<int:booking_id>/receipt")
    def booking_receipt(booking_id):
        booking = data_store.find_booking(booking_id)
        if booking is None or booking.status != "Completed":
            abort(404)
        user = current_user()
        spot = data_store.find_spot(booking.spot_id)
        if user:
            allowed = (
                user.role == "Driver" and booking.driver_username == user.username
            ) or (spot is not None and spot.host_username == user.username)
        else:
            guest_id = session.get("guest_id", "")
            allowed = bool(guest_id and booking.guest_id == guest_id)
        if not allowed or spot is None:
            abort(404)
        driver = data_store.users_db.get(booking.driver_username)
        customer = booking.guest_name or (
            (driver.full_name or driver.username) if driver else booking.driver_username
        )
        host = data_store.users_db.get(spot.host_username)
        return render_template(
            "receipt.html",
            booking=booking,
            spot=spot,
            customer=customer,
            host_name=(host.full_name or host.username) if host else spot.host_username,
        )

    @app.route("/bookings/<int:booking_id>/reviews", methods=["POST"])
    @login_required
    def submit_review(booking_id):
        user = current_user()
        booking = data_store.find_booking(booking_id)
        spot = data_store.find_spot(booking.spot_id) if booking else None
        if booking is None or spot is None or booking.status != "Completed":
            abort(404)
        if user.role == "Driver" and booking.driver_username == user.username:
            target_username = spot.host_username
        elif user.role == "Host" and spot.host_username == user.username:
            target_username = booking.driver_username
        else:
            abort(404)
        if target_username == "Guest" or target_username not in data_store.users_db:
            flash("A review is available only when both sides have an account.", "error")
            return redirect(url_for("bookings"))
        try:
            rating = int(request.form.get("rating", ""))
        except ValueError:
            rating = 0
        text = request.form.get("review_text", "").strip()
        if rating not in range(1, 6):
            flash("Choose a rating from 1 to 5.", "error")
            return redirect(url_for("bookings"))
        if not text or len(text) > 1000:
            flash("Write a review of up to 1000 characters.", "error")
            return redirect(url_for("bookings"))
        with booking_lock:
            if any(review["booking_id"] == booking_id
                   and review["reviewer"] == user.username for review in data_store.reviews):
                flash("You have already reviewed this completed booking.", "error")
                return redirect(url_for("bookings"))
            data_store.reviews.append({
                "id": max((review["id"] for review in data_store.reviews), default=0) + 1,
                "booking_id": booking_id,
                "reviewer": user.username,
                "target": target_username,
                "rating": rating,
                "text": text,
                "created_at": datetime.now(),
                "visible": True,
            })
            data_store.add_notification(
                target_username, "You received a new ParkShare review."
            )
            data_store.log_event(user.username + " reviewed " + target_username
                                 + " after booking #" + str(booking_id))
        flash("Thanks for sharing your review.", "success")
        return redirect(url_for("bookings"))

    @app.route("/reports/create", methods=["POST"])
    @login_required
    def create_report():
        reporter = current_user()
        target_type = request.form.get("target_type", "")
        target_id = request.form.get("target_id", "").strip()
        reason = request.form.get("reason", "")
        details = request.form.get("details", "").strip()
        allowed_reasons = {
            "misleading_listing", "unsafe_behavior", "fraud",
            "harassment", "other",
        }
        if target_type == "spot":
            try:
                target = data_store.find_spot(int(target_id))
            except ValueError:
                target = None
            if target is None or not target.is_active or target.is_demo:
                abort(404)
            target_id = str(target.spot_id)
            return_url = url_for("spots")
        elif target_type == "user":
            target = data_store.users_db.get(target_id)
            if target is None or target.username == reporter.username:
                abort(404)
            return_url = url_for("spots")
        else:
            abort(400, description="Choose a valid report target.")
        if reason not in allowed_reasons:
            flash("Choose a report reason.", "error")
            return redirect(return_url)
        if not details or len(details) > 1000:
            flash("Add details of up to 1000 characters.", "error")
            return redirect(return_url)
        with booking_lock:
            duplicate = any(
                report["reporter"] == reporter.username
                and report["target_type"] == target_type
                and report["target_id"] == target_id
                and report["status"] == "open"
                for report in data_store.reports
            )
            if duplicate:
                flash("You already have an open report for this target.", "error")
                return redirect(return_url)
            data_store.reports.append({
                "id": max((report["id"] for report in data_store.reports), default=0) + 1,
                "reporter": reporter.username,
                "target_type": target_type,
                "target_id": target_id,
                "reason": reason,
                "details": details,
                "status": "open",
                "resolution": "",
                "created_at": datetime.now(),
                "updated_at": datetime.now(),
            })
            data_store.log_event(reporter.username + " reported a " + target_type
                                 + " (" + target_id + ")")
        flash("Your report was sent to ParkShare moderation.", "success")
        return redirect(return_url)

    @app.route("/verification/request", methods=["POST"])
    @login_required
    def request_host_verification():
        user = current_user()
        if user.role != "Host":
            abort(403)
        if user.verification_status == "verified":
            flash("Your host account is already verified.", "success")
            return redirect(url_for("profile"))
        note = request.form.get("verification_note", "").strip()
        if len(note) > 500:
            flash("Keep the verification note under 500 characters.", "error")
            return redirect(url_for("profile"))
        user.verification_status = "pending"
        user.verification_note = note
        user.verified_at = None
        data_store.log_event(user.username + " requested host verification")
        flash("Verification request sent. Do not include identity-document numbers or images.", "success")
        return redirect(url_for("profile"))

    @app.route("/payments/razorpay/webhook", methods=["POST"])
    def razorpay_webhook():
        if not current_app.config["RAZORPAY_WEBHOOK_SECRET"]:
            abort(503, description="Payment webhook is not configured.")
        body = request.get_data(cache=True)
        signature = request.headers.get("X-Razorpay-Signature", "")
        if not payment_module.verify_webhook_signature(
                body, signature, current_app.config["RAZORPAY_WEBHOOK_SECRET"]):
            abort(400, description="Invalid payment webhook signature.")
        try:
            event = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            abort(400, description="Invalid payment webhook payload.")
        payment = event.get("payload", {}).get("payment", {}).get("entity", {})
        if event.get("event") != "payment.captured":
            return "", 200
        order_id = payment.get("order_id", "")
        with booking_lock:
            booking = next((item for item in data_store.bookings_db
                            if item.payment_order_id == order_id), None)
            if booking is None:
                abort(404)
            if booking.payment_status == "captured" and booking.payment_id == payment.get("id"):
                return "", 200
            if (booking.payment_status != "created"
                    or payment.get("amount") != booking.payment_amount
                    or payment.get("currency") != "INR"
                    or payment.get("status") != "captured"):
                abort(400, description="Captured payment does not match the booking.")
            spot = data_store.find_spot(booking.spot_id)
            actor = booking.driver_username if booking.driver_username != "Guest" else (
                booking.guest_name or "A guest"
            )
            complete_checkout(
                booking, actor, spot, booking.exit_time, booking.billed_hours,
                booking.final_cost, payment_id=payment.get("id", ""),
            )
        notify_checkout_completion(booking, spot)
        return "", 200

    @app.route("/host/earnings")
    @role_required("Host")
    def earnings():
        totals = {}
        completed_count = 0
        now = datetime.now()
        month_labels = []
        monthly_earnings = []
        first_month_number = now.year * 12 + now.month - 1 - 5
        for offset in range(6):
            absolute_month = first_month_number + offset
            month = datetime(absolute_month // 12, absolute_month % 12 + 1, 1)
            month_labels.append(month.strftime("%b %Y"))
            monthly_earnings.append(0.0)
        for booking in data_store.bookings_db:
            spot = data_store.find_spot(booking.spot_id)
            if spot and spot.host_username == current_user().username and booking.final_cost is not None:
                totals[spot.spot_id] = totals.get(spot.spot_id, 0) + booking.final_cost
                completed_count += 1
                completed_at = booking.exit_time or booking.entry_time
                month_number = completed_at.year * 12 + completed_at.month - 1 - first_month_number
                if 0 <= month_number < 6:
                    monthly_earnings[month_number] += booking.final_cost
        total = sum(totals.values())
        period_start = now - timedelta(days=30)
        period_end = now
        host_spots = [spot for spot in data_store.spots_db
                      if spot.host_username == current_user().username and spot.is_active]
        capacity_hours = sum(spot.total_slots for spot in host_spots) * 24 * 30
        booked_hours = 0.0
        for booking in data_store.bookings_db:
            spot = data_store.find_spot(booking.spot_id)
            if not spot or spot.host_username != current_user().username or booking.status == "Cancelled":
                continue
            start = booking.start_time or booking.entry_time
            end = booking.exit_time or booking.end_time or (start + timedelta(hours=booking.planned_hours))
            booked_hours += max(0, (min(end, period_end) - max(start, period_start)).total_seconds()) / 3600
        occupancy = min(100, round(booked_hours / capacity_hours * 100, 1)) if capacity_hours else 0
        return render_template("earnings.html", totals=totals, total=total,
                               completed_count=completed_count,
                               month_labels=month_labels, monthly_earnings=monthly_earnings,
                               occupancy=occupancy)

    @app.route("/activity")
    @login_required
    def activity():
        events = list(reversed(data_store.activity_log[-30:]))
        return render_template("activity.html", events=events)

    @app.route("/admin", methods=["GET", "POST"])
    @login_required
    def admin():
        user = current_user()
        if user.username.casefold() not in current_app.config["ADMIN_USERNAMES"]:
            abort(403)
        if request.method == "POST":
            action = request.form.get("action", "")
            username = request.form.get("username", "").strip()
            reason = request.form.get("reason", "").strip()
            if action in ("suspend", "unsuspend") and username:
                if username.casefold() in current_app.config["ADMIN_USERNAMES"]:
                    abort(400, description="Administrator accounts cannot be moderated here.")
                try:
                    if action == "suspend":
                        auth_module.suspend_user(username, reason)
                        flash(f"{username} was suspended.", "success")
                    else:
                        auth_module.unsuspend_user(username)
                        flash(f"{username} was reinstated.", "success")
                except ValueError as error:
                    flash(str(error), "error")
            elif action in ("verify", "reject_verification") and username:
                candidate = data_store.users_db.get(username)
                if candidate is None or candidate.role != "Host":
                    abort(404)
                if candidate.verification_status != "pending":
                    flash("That verification request is no longer pending.", "error")
                elif action == "verify":
                    candidate.verification_status = "verified"
                    candidate.verified_at = datetime.now()
                    candidate.verification_note = ""
                    data_store.log_event(username + " was verified as a host")
                    flash(username + " is now a verified host.", "success")
                else:
                    candidate.verification_status = "rejected"
                    candidate.verified_at = None
                    candidate.verification_note = ""
                    data_store.log_event(username + " host verification was rejected")
                    flash(username + "'s verification request was rejected.", "success")
            elif action in ("resolve_report", "action_report"):
                try:
                    report_id = int(request.form.get("report_id", ""))
                except ValueError:
                    abort(400, description="Invalid report identifier.")
                report = next((item for item in data_store.reports
                               if item["id"] == report_id), None)
                resolution = request.form.get("resolution", "").strip()
                if report is None:
                    abort(404)
                if report["status"] != "open":
                    flash("That report has already been resolved.", "error")
                elif not resolution or len(resolution) > 500:
                    flash("Add a resolution note of up to 500 characters.", "error")
                else:
                    if action == "action_report":
                        if report["target_type"] == "user":
                            target_user = data_store.users_db.get(report["target_id"])
                            if target_user is None:
                                flash("The reported user no longer exists.", "error")
                                return redirect(url_for("admin"))
                            if target_user.username.casefold() in current_app.config["ADMIN_USERNAMES"]:
                                abort(400, description="Administrator accounts cannot be actioned from a report.")
                            auth_module.suspend_user(target_user.username, resolution)
                        else:
                            target_spot = data_store.find_spot(int(report["target_id"]))
                            if target_spot is None or not target_spot.is_active:
                                flash("The reported listing is already unavailable.", "error")
                                return redirect(url_for("admin"))
                            if data_store.occupied_slots(target_spot.spot_id):
                                flash("The listing cannot be hidden while it has active reservations.", "error")
                                return redirect(url_for("admin"))
                            target_spot.is_active = False
                            data_store.remove_spot_favorites(target_spot.spot_id)
                            data_store.refresh_availability(target_spot)
                    report["status"] = "actioned" if action == "action_report" else "resolved"
                    report["resolution"] = resolution
                    report["updated_at"] = datetime.now()
                    data_store.log_event(
                        user.username + " " + report["status"] + " report #"
                        + str(report_id)
                    )
                    flash("Report updated.", "success")
            elif action in ("hide_review", "restore_review"):
                try:
                    review_id = int(request.form.get("review_id", ""))
                except ValueError:
                    abort(400, description="Invalid review identifier.")
                review = next((item for item in data_store.reviews
                               if item["id"] == review_id), None)
                if review is None:
                    abort(404)
                review["visible"] = action == "restore_review"
                data_store.log_event(
                    user.username + (" hid review #" if action == "hide_review"
                                     else " restored review #") + str(review_id)
                )
                flash("Review moderation updated.", "success")
            else:
                abort(400, description="Choose a valid moderation action.")
            return redirect(url_for("admin"))

        users = []
        for username, candidate in sorted(data_store.users_db.items()):
            users.append({
                "username": username,
                "role": candidate.role,
                "full_name": candidate.full_name,
                "email": candidate.email,
                "is_suspended": candidate.is_suspended,
                "suspended_reason": candidate.suspended_reason,
                "verification_status": candidate.verification_status,
                "verification_note": candidate.verification_note,
            })
        verification_requests = [
            item for item in users if item["verification_status"] == "pending"
        ]
        open_reports = [item for item in data_store.reports if item["status"] == "open"]
        report_targets = {}
        for report in open_reports:
            if report["target_type"] == "spot":
                spot = data_store.find_spot(int(report["target_id"]))
                report_targets[report["id"]] = spot.location if spot else "Listing removed"
            else:
                report_targets[report["id"]] = report["target_id"]
        return render_template(
            "admin.html",
            users=users,
            verification_requests=verification_requests,
            open_reports=open_reports,
            report_targets=report_targets,
            reviews=data_store.reviews,
        )

    @app.route("/health")
    def health():
        return {"status": "ok"}

    return app


app = create_app()


if __name__ == "__main__":
    app.run(
        host=os.environ.get("PARKSHARE_HOST", "127.0.0.1"),
        port=int(os.environ.get("PARKSHARE_PORT", "5000")),
        debug=os.environ.get("PARKSHARE_DEBUG") == "1",
    )
