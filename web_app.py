import hmac
import hashlib
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

from flask import Flask, abort, current_app, flash, redirect, render_template, request, session, url_for

import auth_module
import billing_module
import data_store
from models import ParkingSpot, Booking
from werkzeug.security import generate_password_hash


VEHICLE_TYPES = ("Car", "Motorcycle", "SUV", "Van")
booking_lock = threading.RLock()
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


def create_app(test_config=None):
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=os.environ.get("PARKSHARE_SECRET_KEY", secrets.token_hex(32)),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        MAX_CONTENT_LENGTH=MAX_PHOTO_BYTES + 1024 * 1024,
        UPLOAD_FOLDER=str(Path(app.static_folder) / "uploads"),
        PUBLIC_URL=os.environ.get(
            "PARKSHARE_PUBLIC_URL",
            "http://127.0.0.1:" + os.environ.get("PARKSHARE_PORT", "5000"),
        ),
    )
    if test_config:
        app.config.update(test_config)

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

    @app.before_request
    def protect_forms():
        if request.method == "POST":
            submitted = request.form.get("csrf_token", "")
            expected = session.get("_csrf_token", "")
            if not submitted or not expected or not hmac.compare_digest(submitted, expected):
                abort(400, description="Form expired or invalid. Reload the page and try again.")

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
            "signed_in_user": user,
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
        return render_template("landing.html", spot_count=len(active_spots), available_slots=available_slots)

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
            user = auth_module.authenticate(
                request.form.get("username", ""),
                request.form.get("password", ""),
            )
            if user is None:
                flash("We couldn't match that username and password.", "error")
            else:
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
        data_store.notifications[:] = [
            item for item in data_store.notifications if item["username"] != user.username
        ]
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
    @login_required
    def spots():
        keyword = request.args.get("location", "").strip().lower()
        max_price_raw = request.args.get("max_price", "").strip()
        covered = request.args.get("covered") == "1"
        ev_charging = request.args.get("ev_charging") == "1"
        requested_vehicle = request.args.get("vehicle_type")
        vehicle_type = requested_vehicle if requested_vehicle is not None else (current_user().vehicle_type or "Any")
        if vehicle_type not in VEHICLE_TYPES and vehicle_type != "Any":
            vehicle_type = current_user().vehicle_type or "Any"
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
            }
            for spot in results if spot.latitude is not None and spot.longitude is not None
        ]
        demo_markers = (
            _demo_area_markers(near_latitude, near_longitude, radius_km)
            if near_latitude is not None else []
        )
        map_markers.extend(demo_markers)
        if sort_by == "price":
            results.sort(key=lambda item: item.price_per_hour)
        elif sort_by == "rating":
            results.sort(key=lambda item: item.average_rating() or 0, reverse=True)
        elif sort_by == "availability":
            results.sort(key=lambda item: availability[item.spot_id], reverse=True)
        else:
            results.sort(key=lambda item: recommendation_scores[item.spot_id], reverse=True)
        return render_template(
            "spots.html",
            spots=results,
            availability=availability,
            distances=distances,
            recommendation_scores=recommendation_scores,
            map_markers=map_markers,
            demo_markers=demo_markers,
            vehicle_types=VEHICLE_TYPES,
            active_spot_count=sum(1 for spot in data_store.spots_db if spot.is_active),
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
    @login_required
    def bookings():
        user = current_user()
        mine = [booking for booking in data_store.bookings_db
                if (booking.driver_username == user.username if user.role == "Driver"
                    else data_store.find_spot(booking.spot_id).host_username == user.username)]
        return render_template("bookings.html", bookings=mine)

    @app.route("/bookings/create", methods=["POST"])
    @role_required("Driver")
    def create_booking():
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
        with booking_lock:
            spot = data_store.find_spot(spot_id)
            user = current_user()
            if spot is None or not spot.is_active:
                flash("That spot is no longer available.", "error")
                return redirect(url_for("spots"))
            if user.vehicle_type and user.vehicle_type not in spot.vehicle_types:
                flash("This spot does not support your registered vehicle type.", "error")
                return redirect(url_for("spots"))
            slot_number = data_store.free_slot_for_window(spot, start_time, end_time)
            if slot_number is None:
                flash("No space is available for that time range.", "error")
                return redirect(url_for("spots"))
            booking = Booking(
                data_store.next_booking_id(), spot.spot_id, user.username, hours,
                spot.price_per_hour, start_time, slot_number, start_time, end_time,
            )
            data_store.bookings_db.append(booking)
            data_store.refresh_availability(spot)
            data_store.add_notification(
                spot.host_username,
                user.username + " booked " + spot.location + " from "
                + start_time.strftime("%d %b %Y %I:%M %p") + ".",
            )
            data_store.log_event(user.username + " booked spot #" + str(spot.spot_id)
                                 + " (slot " + str(slot_number) + ")")
        flash("Booking confirmed — your assigned space is #" + str(slot_number) + ".", "success")
        return redirect(url_for("bookings"))

    @app.route("/bookings/<int:booking_id>/cancel", methods=["POST"])
    @role_required("Driver")
    def cancel_booking(booking_id):
        with booking_lock:
            booking = data_store.find_booking(booking_id)
            if booking is None or booking.status != "Active" or booking.driver_username != current_user().username:
                abort(404)
            booking.status = "Cancelled"
            spot = data_store.find_spot(booking.spot_id)
            data_store.refresh_availability(spot)
            data_store.add_notification(
                spot.host_username,
                current_user().username + " cancelled booking #" + str(booking_id) + ".",
            )
            data_store.log_event(current_user().username + " cancelled booking #" + str(booking_id))
        flash("Booking cancelled without charge.", "success")
        return redirect(url_for("bookings"))

    @app.route("/bookings/<int:booking_id>/checkout", methods=["POST"])
    @role_required("Driver")
    def checkout(booking_id):
        with booking_lock:
            booking = data_store.find_booking(booking_id)
            if booking is None or booking.status != "Active" or booking.driver_username != current_user().username:
                abort(404)
            if booking.start_time and datetime.now() < booking.start_time:
                flash("You cannot check out before your booking starts.", "error")
                return redirect(url_for("bookings"))
            spot = data_store.find_spot(booking.spot_id)
            exit_time = datetime.now()
            amount, hours, overtime = billing_module.final_cost(
                booking.price_per_hour, booking.entry_time, exit_time, booking.planned_hours,
            )
            promo = request.form.get("promo_code", "").strip()
            final_amount, discount, unused_rate = billing_module.apply_discount(amount, promo)
            booking.exit_time = exit_time
            booking.billed_hours = hours
            booking.final_cost = final_amount
            booking.status = "Completed"
            data_store.refresh_availability(spot)
            rating_raw = request.form.get("rating", "")
            if rating_raw:
                try:
                    rating = int(rating_raw)
                    if rating < 1 or rating > 5:
                        raise ValueError()
                except ValueError:
                    flash("Checkout completed, but the rating must be from 1 to 5.", "error")
                    rating = None
                if rating is not None:
                    spot.add_rating(rating)
                    data_store.log_event(current_user().username + " rated spot #" + str(spot.spot_id)
                                         + " " + str(rating) + "/5")
            data_store.log_event(current_user().username + " released spot #" + str(spot.spot_id)
                                 + " | Rs " + str(final_amount))
            data_store.add_notification(
                spot.host_username,
                current_user().username + " completed booking #" + str(booking_id) + ".",
            )
            data_store.persist()
        if promo and not discount:
            flash("Checkout complete. That promo code was not recognized.", "error")
        else:
            flash("Checkout complete — final total: ₹" + format(final_amount, ".2f") + ".", "success")
        return redirect(url_for("bookings"))

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
