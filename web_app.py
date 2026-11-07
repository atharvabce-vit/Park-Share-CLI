import hmac
import math
import os
import secrets
import threading
from datetime import datetime
from functools import wraps

from flask import Flask, abort, flash, redirect, render_template, request, session, url_for

import auth_module
import billing_module
import data_store
from models import ParkingSpot, Booking


VEHICLE_TYPES = ("Car", "Motorcycle", "SUV", "Van")
booking_lock = threading.RLock()


def create_app(test_config=None):
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=os.environ.get("PARKSHARE_SECRET_KEY", secrets.token_hex(32)),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
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
        return {
            "csrf_token": csrf_token,
            "find_spot": data_store.find_spot,
            "signed_in_user": current_user(),
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
        vehicle_type = request.args.get("vehicle_type", "")
        sort_by = request.args.get("sort", "recommended")
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
        for spot in data_store.spots_db:
            if not spot.is_active or not spot.has_slots():
                continue
            if keyword and keyword not in spot.location.lower():
                continue
            if max_price is not None and spot.price_per_hour > max_price:
                continue
            if covered and not spot.covered:
                continue
            if ev_charging and not spot.ev_charging:
                continue
            if vehicle_type and vehicle_type not in spot.vehicle_types:
                continue
            results.append(spot)
        if sort_by == "price":
            results.sort(key=lambda item: item.price_per_hour)
        elif sort_by == "rating":
            results.sort(key=lambda item: item.average_rating() or 0, reverse=True)
        elif sort_by == "availability":
            results.sort(key=lambda item: item.available_slots, reverse=True)
        return render_template(
            "spots.html",
            spots=results,
            vehicle_types=VEHICLE_TYPES,
            filters={"location": request.args.get("location", ""),
                     "max_price": max_price_raw,
                     "covered": covered,
                     "ev_charging": ev_charging,
                     "vehicle_type": vehicle_type,
                     "sort": sort_by},
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
            except ValueError:
                flash("Enter a valid hourly price and number of spaces.", "error")
                return redirect(url_for("host_spots"))
            if not location or not math.isfinite(price) or price < 1 or slots < 1 or slots > 100:
                flash("Location is required, price must be at least ₹1, and spaces must be between 1 and 100.", "error")
                return redirect(url_for("host_spots"))
            types = [item for item in VEHICLE_TYPES if item in request.form.getlist("vehicle_types")]
            if not types:
                flash("Select at least one vehicle type for this listing.", "error")
                return redirect(url_for("host_spots"))
            spot = ParkingSpot(
                data_store.next_spot_id(), location, price, user.username, slots,
                description, request.form.get("covered") == "on",
                request.form.get("ev_charging") == "on", types,
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
        except ValueError:
            flash("Enter a valid hourly price.", "error")
            return redirect(url_for("host_spots"))
        types = [item for item in VEHICLE_TYPES if item in request.form.getlist("vehicle_types")]
        if not location or not math.isfinite(price) or price < 1 or not types:
            flash("A location, price of at least ₹1, and one vehicle type are required.", "error")
            return redirect(url_for("host_spots"))
        spot.location = location
        spot.description = description
        spot.price_per_hour = price
        spot.vehicle_types = types
        spot.covered = request.form.get("covered") == "on"
        spot.ev_charging = request.form.get("ev_charging") == "on"
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
        except ValueError:
            flash("Choose a spot and enter a whole-number duration.", "error")
            return redirect(url_for("spots"))
        if hours < 1 or hours > 168:
            flash("Booking duration must be between 1 and 168 hours.", "error")
            return redirect(url_for("spots"))
        with booking_lock:
            spot = data_store.find_spot(spot_id)
            user = current_user()
            if spot is None or not spot.is_active or not spot.has_slots():
                flash("That spot is no longer available.", "error")
                return redirect(url_for("spots"))
            if user.vehicle_type and user.vehicle_type not in spot.vehicle_types:
                flash("This spot does not support your registered vehicle type.", "error")
                return redirect(url_for("spots"))
            slot_number = data_store.next_free_slot(spot)
            if slot_number is None:
                flash("No space is currently available at that listing.", "error")
                return redirect(url_for("spots"))
            spot.available_slots -= 1
            booking = Booking(
                data_store.next_booking_id(), spot.spot_id, user.username, hours,
                spot.price_per_hour, datetime.now(), slot_number,
            )
            data_store.bookings_db.append(booking)
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
            spot.available_slots += 1
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
            spot.available_slots += 1
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
        for booking in data_store.bookings_db:
            spot = data_store.find_spot(booking.spot_id)
            if spot and spot.host_username == current_user().username and booking.final_cost is not None:
                totals[spot.spot_id] = totals.get(spot.spot_id, 0) + booking.final_cost
                completed_count += 1
        total = sum(totals.values())
        return render_template("earnings.html", totals=totals, total=total,
                               completed_count=completed_count)

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
    app.run(debug=os.environ.get("PARKSHARE_DEBUG") == "1")
