import binascii
import hashlib
import hmac
import re
import os
import struct
import tempfile
import unittest
from datetime import datetime, timedelta
from io import BytesIO
from unittest.mock import patch
import zlib

import auth_module
import data_store
from werkzeug.security import check_password_hash
from web_app import (
    _demo_area_markers,
    _distance_km,
    _notify_booking_parties,
    create_app,
)
from models import Booking, ParkingSpot


class ParkShareWebTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.database_path = os.path.join(self.temp_dir.name, "test.sqlite3")
        data_store.configure_storage(self.database_path)
        data_store.reset_data()
        self.app = create_app({
            "TESTING": True,
            "SECRET_KEY": "test-key",
            "DEMO_MODE": False,
            "UPLOAD_FOLDER": os.path.join(self.temp_dir.name, "uploads"),
            "PASSWORD_RESET_SENDER": lambda email, reset_url: None,
        })
        self.client = self.app.test_client()

    def csrf(self, response):
        match = re.search(rb'name="csrf_token" value="([^"]+)"', response.data)
        self.assertIsNotNone(match)
        return match.group(1).decode("utf-8")

    def register(self, client, username, role="Driver", **extra):
        response = client.get("/register")
        form = {
            "csrf_token": self.csrf(response),
            "username": username,
            "full_name": username.title() + " User",
            "email": username + "@example.com",
            "role": role,
            "password": "securepass1",
            "confirm_password": "securepass1",
            "terms": "accepted",
        }
        if role == "Driver":
            form["vehicle_type"] = "Car"
        form.update(extra)
        return client.post("/register", data=form, follow_redirects=True)

    def png_pixel(self):
        def chunk(name, body):
            return (struct.pack(">I", len(body)) + name + body
                    + struct.pack(">I", binascii.crc32(name + body) & 0xffffffff))
        header = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
        return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
                + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00"))
                + chunk(b"IEND", b""))

    def test_registration_hashes_password_and_saves_profile(self):
        response = self.register(self.client, "driver_one", vehicle_plate="MH01AB1234")
        self.assertEqual(response.status_code, 200)
        user = data_store.users_db["driver_one"]
        self.assertNotEqual(user.password, "securepass1")
        self.assertTrue(check_password_hash(user.password, "securepass1"))
        self.assertEqual(user.full_name, "Driver_One User")
        self.assertEqual(user.vehicle_plate, "MH01AB1234")
        self.assertIn(b"Welcome", response.data)

    def test_signup_requires_csrf_token(self):
        response = self.client.post("/register", data={"username": "bad"})
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("bad", data_store.users_db)

    def test_responses_include_browser_security_headers(self):
        response = self.client.get("/")
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")
        self.assertEqual(
            response.headers["Referrer-Policy"], "strict-origin-when-cross-origin"
        )
        self.assertEqual(response.headers["Permissions-Policy"], "geolocation=(self)")

    def test_health_endpoint_checks_sqlite_readiness(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json, {"status": "ok", "storage": "ok"})

    @patch("web_app.data_store.storage_is_ready", return_value=False)
    def test_health_endpoint_returns_service_unavailable_when_storage_is_down(self, ready):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json, {"status": "error", "storage": "unavailable"})

    def test_login_throttle_cannot_be_bypassed_with_forwarded_ip_header(self):
        page = self.client.get("/login")
        for attempt in range(5):
            page = self.client.get("/login")
            response = self.client.post(
                "/login",
                data={
                    "csrf_token": self.csrf(page),
                    "username": "missing_user",
                    "password": "wrongpass1",
                },
                headers={"X-Forwarded-For": "203.0.113." + str(attempt + 1)},
            )
            self.assertEqual(response.status_code, 200)
        page = self.client.get("/login")
        limited = self.client.post(
            "/login",
            data={
                "csrf_token": self.csrf(page),
                "username": "missing_user",
                "password": "wrongpass1",
            },
            headers={"X-Forwarded-For": "198.51.100.10"},
        )
        self.assertEqual(limited.status_code, 429)

    def test_dashboard_highlights_the_next_three_future_bookings(self):
        self.register(self.client, "dashboard_upcoming_driver")
        spot = ParkingSpot(
            1, "Upcoming garage", 45, "host", 1,
            latitude=19.076, longitude=72.8777,
        )
        data_store.spots_db.append(spot)
        now = datetime.now()
        for booking_id, days in ((1, 3), (2, 1), (3, 5), (4, 7)):
            data_store.bookings_db.append(Booking(
                booking_id, 1, "dashboard_upcoming_driver", 1, 45,
                now, start_time=now + timedelta(days=days),
            ))
        response = self.client.get("/dashboard")
        self.assertIn(b"Your next bookings", response.data)
        self.assertEqual(response.data.count(b"Add to calendar"), 3)
        self.assertLess(
            response.data.index(b"Booking #2"),
            response.data.index(b"Booking #1"),
        )

    def test_driver_can_save_remove_and_reload_favorite_listings(self):
        host = self.app.test_client()
        self.register(host, "favorite_host", "Host")
        host_page = host.get("/host/spots")
        host.post("/host/spots", data={
            "csrf_token": self.csrf(host_page),
            "location": "Favorite garage",
            "price_per_hour": "45",
            "total_slots": "1",
            "latitude": "19.0760",
            "longitude": "72.8777",
            "vehicle_types": ["Car"],
        })
        spot = data_store.spots_db[0]

        driver = self.app.test_client()
        self.register(driver, "favorite_driver")
        browse = driver.get("/spots")
        self.assertIn(b"Save for later", browse.data)
        saved = driver.post(
            "/favorites/" + str(spot.spot_id) + "/toggle",
            data={"csrf_token": self.csrf(browse)},
            follow_redirects=True,
        )
        self.assertIn(b"Listing saved for later", saved.data)
        self.assertIn(b"Remove saved spot", saved.data)
        self.assertEqual(
            data_store.saved_spots, {("favorite_driver", spot.spot_id)}
        )

        data_store.configure_storage(self.database_path)
        self.assertEqual(
            data_store.saved_spots, {("favorite_driver", spot.spot_id)}
        )
        favorites = driver.get("/favorites")
        self.assertEqual(favorites.status_code, 200)
        self.assertIn(b"Favorite garage", favorites.data)

        favorites_csrf = self.csrf(favorites)
        removed = driver.post(
            "/favorites/" + str(spot.spot_id) + "/toggle",
            data={
                "csrf_token": favorites_csrf,
                "return_to": "favorites",
            },
            follow_redirects=True,
        )
        self.assertIn(b"Listing removed from your saved spots", removed.data)
        self.assertIn(b"No saved listings yet", removed.data)
        self.assertEqual(data_store.saved_spots, set())

    def test_host_blocks_and_reopens_listing_availability(self):
        host = self.app.test_client()
        self.register(host, "calendar_host", "Host")
        page = host.get("/host/spots")
        host.post("/host/spots", data={
            "csrf_token": self.csrf(page), "location": "Calendar garage",
            "price_per_hour": "45", "total_slots": "1",
            "latitude": "19.0760", "longitude": "72.8777",
            "vehicle_types": ["Car"],
        })
        spot = data_store.spots_db[0]
        start = datetime.now() + timedelta(days=2)
        end = start + timedelta(hours=2)
        host_page = host.get("/host/spots")
        response = host.post(
            "/host/spots/" + str(spot.spot_id) + "/blocks",
            data={
                "csrf_token": self.csrf(host_page),
                "start_time": start.strftime("%Y-%m-%dT%H:%M"),
                "end_time": end.strftime("%Y-%m-%dT%H:%M"),
                "reason": "Personal use",
            },
            follow_redirects=True,
        )
        self.assertIn(b"space is blocked", response.data)
        self.assertEqual(len(data_store.availability_blocks), 1)

        data_store.configure_storage(self.database_path)
        self.assertEqual(len(data_store.availability_blocks), 1)
        driver = self.app.test_client()
        self.register(driver, "calendar_driver")
        query_start = start.strftime("%Y-%m-%dT%H:%M")
        spots_page = driver.get("/spots?start_time=" + query_start + "&hours=1")
        self.assertIn(b"No spots match those filters", spots_page.data)
        booking = driver.post("/bookings/create", data={
            "csrf_token": self.csrf(spots_page),
            "spot_id": str(spot.spot_id),
            "start_time": query_start,
            "hours": "1",
        }, follow_redirects=True)
        self.assertIn(b"No space is available", booking.data)
        self.assertEqual(data_store.bookings_db, [])

        host_page = host.get("/host/spots")
        block_id = data_store.availability_blocks[0]["id"]
        response = host.post(
            "/host/spots/" + str(spot.spot_id) + "/blocks/"
            + str(block_id) + "/remove",
            data={"csrf_token": self.csrf(host_page)},
            follow_redirects=True,
        )
        self.assertIn(b"Availability reopened", response.data)
        self.assertEqual(data_store.availability_blocks, [])
        spots_page = driver.get("/spots?start_time=" + query_start + "&hours=1")
        self.assertIn(b"Calendar garage", spots_page.data)

    def test_parking_instructions_are_private_to_confirmed_booking_parties(self):
        host = self.app.test_client()
        self.register(host, "instructions_host", "Host")
        page = host.get("/host/spots")
        host.post("/host/spots", data={
            "csrf_token": self.csrf(page), "location": "Instructions garage",
            "description": "Near the station",
            "parking_instructions": "Use the east gate code 4821.",
            "price_per_hour": "45", "total_slots": "1",
            "latitude": "19.0760", "longitude": "72.8777",
            "vehicle_types": ["Car"],
        })
        spot = data_store.spots_db[0]
        public_search = self.client.get("/spots")
        self.assertIn(b"Instructions garage", public_search.data)
        self.assertNotIn(b"east gate code", public_search.data)

        driver = self.app.test_client()
        self.register(driver, "instructions_driver")
        page = driver.get("/spots")
        driver.post("/bookings/create", data={
            "csrf_token": self.csrf(page), "spot_id": str(spot.spot_id),
            "start_time": (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M"),
            "hours": "1",
        })
        booking_page = driver.get("/bookings")
        self.assertIn(b"Parking instructions from your host", booking_page.data)
        self.assertIn(b"east gate code 4821", booking_page.data)
        self.assertNotIn(b"east gate code", host.get("/bookings").data)

        data_store.configure_storage(self.database_path)
        self.assertEqual(
            data_store.find_spot(spot.spot_id).parking_instructions,
            "Use the east gate code 4821.",
        )

    def test_cancellation_policy_is_clear_on_search_and_booking_pages(self):
        host = self.app.test_client()
        self.register(host, "policy_host", "Host")
        page = host.get("/host/spots")
        host.post("/host/spots", data={
            "csrf_token": self.csrf(page), "location": "Policy garage",
            "price_per_hour": "45", "total_slots": "1",
            "latitude": "19.0760", "longitude": "72.8777",
            "vehicle_types": ["Car"],
        })
        driver = self.app.test_client()
        self.register(driver, "policy_driver")
        spots_page = driver.get("/spots")
        self.assertIn(b"Cancel before the reserved start time at no charge", spots_page.data)
        self.assertIn(b"ParkShare does not automatically issue refunds", spots_page.data)
        response = driver.post("/bookings/create", data={
            "csrf_token": self.csrf(spots_page),
            "spot_id": str(data_store.spots_db[0].spot_id),
            "start_time": (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M"),
            "hours": "1",
        })
        self.assertEqual(response.status_code, 302)
        booking_page = driver.get("/bookings")
        self.assertIn(b"Free cancellation is available before", booking_page.data)
        self.assertIn(b"checkout begins", booking_page.data)

    def test_email_preferences_persist_and_filter_email_only(self):
        host_client = self.app.test_client()
        self.register(host_client, "prefs_host", "Host")
        driver_client = self.app.test_client()
        self.register(driver_client, "prefs_driver")

        profile = driver_client.get("/profile")
        self.assertIn(b"Upcoming booking reminders", profile.data)
        response = driver_client.post(
            "/profile/email-preferences",
            data={
                "csrf_token": self.csrf(profile),
                "email_preferences": ["booking_reminders", "saved_spot_alerts"],
            },
            follow_redirects=True,
        )
        self.assertIn(b"preferences have been saved", response.data)
        self.assertFalse(
            data_store.users_db["prefs_driver"].email_preferences["booking_updates"]
        )
        self.assertTrue(
            data_store.users_db["prefs_driver"].email_preferences["booking_reminders"]
        )
        data_store.configure_storage(self.database_path)
        self.assertFalse(
            data_store.users_db["prefs_driver"].email_preferences["booking_updates"]
        )

        spot = ParkingSpot(900, "Preferences garage", 45, "prefs_host", 1)
        booking = Booking(
            900, 900, "prefs_driver", 1, 45, datetime.now(),
        )
        with patch("web_app._send_email") as send_email:
            _notify_booking_parties(
                booking, spot, "ParkShare booking confirmed", "Confirmed."
            )
        self.assertEqual(send_email.call_count, 1)
        self.assertEqual(send_email.call_args.args[0], "prefs_host@example.com")

    def test_admin_moderation_actions_are_audited_and_private(self):
        self.app = create_app({
            "TESTING": True,
            "SECRET_KEY": "test-key",
            "DEMO_MODE": False,
            "UPLOAD_FOLDER": os.path.join(self.temp_dir.name, "uploads"),
            "ADMIN_USERNAMES": ("audit_admin",),
        })
        admin = self.app.test_client()
        self.register(admin, "audit_admin")
        user = self.app.test_client()
        self.register(user, "audited_user")

        page = admin.get("/admin")
        response = admin.post("/admin", data={
            "csrf_token": self.csrf(page),
            "action": "suspend",
            "username": "audited_user",
            "reason": "Repeated policy violations",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(data_store.admin_audit_log), 1)
        entry = data_store.admin_audit_log[0]
        self.assertEqual(entry["moderator"], "audit_admin")
        self.assertEqual(entry["action"], "suspend")
        self.assertEqual(entry["target"], "audited_user")
        self.assertEqual(entry["details"], "Repeated policy violations")

        data_store.configure_storage(self.database_path)
        self.assertEqual(data_store.admin_audit_log[0]["action"], "suspend")
        self.assertIn(b"Repeated policy violations", admin.get("/admin").data)
        self.assertEqual(user.get("/admin").status_code, 403)

    def test_host_can_export_completed_earnings_as_safe_csv(self):
        host = self.app.test_client()
        self.register(host, "export_host", "Host")
        page = host.get("/host/spots")
        host.post("/host/spots", data={
            "csrf_token": self.csrf(page), "location": "Export garage",
            "price_per_hour": "45", "total_slots": "1",
            "latitude": "19.0760", "longitude": "72.8777",
            "vehicle_types": ["Car"],
        })
        spot = data_store.spots_db[0]
        driver = self.app.test_client()
        self.register(driver, "export_driver")
        page = driver.get("/spots")
        driver.post("/bookings/create", data={
            "csrf_token": self.csrf(page), "spot_id": str(spot.spot_id),
            "start_time": (datetime.now() + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M"),
            "hours": "1",
        })
        booking = data_store.bookings_db[0]
        booking.start_time = datetime.now() - timedelta(hours=2)
        booking.end_time = datetime.now() - timedelta(hours=1)
        booking.entry_time = booking.start_time
        booking.status = "Checked In"
        page = driver.get("/bookings")
        response = driver.post(
            "/bookings/" + str(booking.booking_id) + "/checkout",
            data={"csrf_token": self.csrf(page)},
        )
        self.assertEqual(response.status_code, 302)

        spot.location = "=SUM(1,1)"
        export = host.get("/host/earnings/export.csv")
        self.assertEqual(export.status_code, 200)
        self.assertTrue(export.mimetype.startswith("text/csv"))
        self.assertIn("attachment;", export.headers["Content-Disposition"])
        self.assertIn(b"Booking ID,Spot ID,Location,Driver", export.data)
        self.assertIn(b"'=SUM(1,1)", export.data)
        self.assertIn(b"Completed", host.get("/host/earnings").data)
        self.assertEqual(
            driver.get("/host/earnings/export.csv").status_code, 302
        )

    def test_hosts_cannot_block_periods_with_reservations_or_other_listings(self):
        host = self.app.test_client()
        self.register(host, "calendar_owner", "Host")
        page = host.get("/host/spots")
        host.post("/host/spots", data={
            "csrf_token": self.csrf(page), "location": "Booked calendar garage",
            "price_per_hour": "45", "total_slots": "1",
            "latitude": "19.0760", "longitude": "72.8777",
            "vehicle_types": ["Car"],
        })
        spot = data_store.spots_db[0]
        driver = self.app.test_client()
        self.register(driver, "calendar_booker")
        start = datetime.now() + timedelta(days=3)
        page = driver.get("/spots")
        driver.post("/bookings/create", data={
            "csrf_token": self.csrf(page), "spot_id": str(spot.spot_id),
            "start_time": start.strftime("%Y-%m-%dT%H:%M"), "hours": "2",
        })

        host_page = host.get("/host/spots")
        response = host.post(
            "/host/spots/" + str(spot.spot_id) + "/blocks",
            data={
                "csrf_token": self.csrf(host_page),
                "start_time": start.strftime("%Y-%m-%dT%H:%M"),
                "end_time": (start + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M"),
            },
            follow_redirects=True,
        )
        self.assertIn(b"cannot overlap an active reservation", response.data)
        self.assertEqual(data_store.availability_blocks, [])

        other_host = self.app.test_client()
        self.register(other_host, "calendar_other", "Host")
        response = other_host.post(
            "/host/spots/" + str(spot.spot_id) + "/blocks",
            data={
                "csrf_token": self.csrf(other_host.get("/host/spots")),
                "start_time": start.strftime("%Y-%m-%dT%H:%M"),
                "end_time": (start + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M"),
            },
        )
        self.assertEqual(response.status_code, 404)

    def test_driver_reschedule_request_requires_host_approval_and_persists(self):
        host = self.app.test_client()
        self.register(host, "reschedule_host", "Host")
        page = host.get("/host/spots")
        host.post("/host/spots", data={
            "csrf_token": self.csrf(page), "location": "Reschedule garage",
            "price_per_hour": "45", "total_slots": "1",
            "latitude": "19.0760", "longitude": "72.8777",
            "vehicle_types": ["Car"],
        })
        spot = data_store.spots_db[0]
        driver = self.app.test_client()
        self.register(driver, "reschedule_driver")
        original_start = (datetime.now() + timedelta(days=3)).replace(
            second=0, microsecond=0
        )
        page = driver.get("/spots")
        driver.post("/bookings/create", data={
            "csrf_token": self.csrf(page), "spot_id": str(spot.spot_id),
            "start_time": original_start.strftime("%Y-%m-%dT%H:%M"),
            "hours": "2",
        })
        booking = data_store.bookings_db[0]
        requested_start = original_start + timedelta(days=2)
        page = driver.get("/bookings")
        response = driver.post(
            "/bookings/" + str(booking.booking_id) + "/reschedule",
            data={
                "csrf_token": self.csrf(page),
                "start_time": requested_start.strftime("%Y-%m-%dT%H:%M"),
                "hours": "3",
            },
            follow_redirects=True,
        )
        self.assertIn(b"Reschedule request sent", response.data)
        self.assertEqual(booking.start_time, original_start)
        self.assertEqual(data_store.reschedule_requests[0]["status"], "pending")
        data_store.configure_storage(self.database_path)
        self.assertEqual(len(data_store.reschedule_requests), 1)

        page = host.get("/bookings")
        self.assertIn(b"Accept new time", page.data)
        request_id = data_store.reschedule_requests[0]["id"]
        response = host.post(
            "/bookings/" + str(booking.booking_id) + "/reschedule/"
            + str(request_id),
            data={
                "csrf_token": self.csrf(page),
                "action": "accept",
            },
            follow_redirects=True,
        )
        self.assertIn(b"Reschedule request accepted", response.data)
        updated_booking = data_store.find_booking(booking.booking_id)
        self.assertEqual(updated_booking.start_time, requested_start)
        self.assertEqual(updated_booking.end_time, requested_start + timedelta(hours=3))
        self.assertEqual(updated_booking.planned_hours, 3)
        self.assertEqual(data_store.reschedule_requests[0]["status"], "accepted")
        self.assertIn("accepted", driver.get("/notifications").data.decode().lower())

    def test_saved_spot_alerts_cover_price_changes_and_reopened_availability(self):
        host = self.app.test_client()
        self.register(host, "alerts_host", "Host")
        page = host.get("/host/spots")
        host.post("/host/spots", data={
            "csrf_token": self.csrf(page), "location": "Saved alert garage",
            "price_per_hour": "45", "total_slots": "1",
            "latitude": "19.0760", "longitude": "72.8777",
            "vehicle_types": ["Car"],
        })
        spot = data_store.spots_db[0]
        saved_driver = self.app.test_client()
        self.register(saved_driver, "alerts_saved")
        page = saved_driver.get("/spots")
        saved_driver.post(
            "/favorites/" + str(spot.spot_id) + "/toggle",
            data={"csrf_token": self.csrf(page)},
        )

        page = host.get("/host/spots")
        response = host.post(
            "/host/spots/" + str(spot.spot_id) + "/edit",
            data={
                "csrf_token": self.csrf(page),
                "location": "Saved alert garage",
                "description": "",
                "price_per_hour": "55",
                "latitude": "19.0760",
                "longitude": "72.8777",
                "vehicle_types": ["Car"],
            },
            follow_redirects=True,
        )
        self.assertIn(b"Listing details updated", response.data)
        notifications = saved_driver.get("/notifications")
        self.assertIn(b"price changed for your saved spot", notifications.data)
        self.assertIn(b"55.00", notifications.data)

        booking_driver = self.app.test_client()
        self.register(booking_driver, "alerts_booker")
        start = (datetime.now() + timedelta(days=2)).replace(
            second=0, microsecond=0
        )
        page = booking_driver.get("/spots")
        booking_driver.post("/bookings/create", data={
            "csrf_token": self.csrf(page),
            "spot_id": str(spot.spot_id),
            "start_time": start.strftime("%Y-%m-%dT%H:%M"),
            "hours": "1",
        })
        booking = data_store.bookings_db[0]
        page = booking_driver.get("/bookings")
        response = booking_driver.post(
            "/bookings/" + str(booking.booking_id) + "/cancel",
            data={"csrf_token": self.csrf(page)},
        )
        self.assertEqual(response.status_code, 302)
        notifications = saved_driver.get("/notifications")
        self.assertIn(b"saved parking spot is available", notifications.data)

    def test_reschedule_conflicts_are_rechecked_before_acceptance(self):
        host = self.app.test_client()
        self.register(host, "reschedule_conflict_host", "Host")
        page = host.get("/host/spots")
        host.post("/host/spots", data={
            "csrf_token": self.csrf(page), "location": "Reschedule conflict garage",
            "price_per_hour": "45", "total_slots": "1",
            "latitude": "19.0760", "longitude": "72.8777",
            "vehicle_types": ["Car"],
        })
        spot = data_store.spots_db[0]
        first_driver = self.app.test_client()
        self.register(first_driver, "reschedule_first")
        original_start = (datetime.now() + timedelta(days=4)).replace(
            second=0, microsecond=0
        )
        page = first_driver.get("/spots")
        first_driver.post("/bookings/create", data={
            "csrf_token": self.csrf(page), "spot_id": str(spot.spot_id),
            "start_time": original_start.strftime("%Y-%m-%dT%H:%M"), "hours": "1",
        })
        first_booking = data_store.bookings_db[0]
        second_driver = self.app.test_client()
        self.register(second_driver, "reschedule_second")
        requested_start = original_start + timedelta(days=2)
        page = second_driver.get("/spots")
        second_driver.post("/bookings/create", data={
            "csrf_token": self.csrf(page), "spot_id": str(spot.spot_id),
            "start_time": requested_start.strftime("%Y-%m-%dT%H:%M"), "hours": "1",
        })
        second_booking = data_store.bookings_db[1]

        page = first_driver.get("/bookings")
        first_driver.post(
            "/bookings/" + str(first_booking.booking_id) + "/reschedule",
            data={
                "csrf_token": self.csrf(page),
                "start_time": requested_start.strftime("%Y-%m-%dT%H:%M"),
                "hours": "1",
            },
        )
        reschedule = data_store.reschedule_requests[0]
        host_page = host.get("/bookings")
        response = host.post(
            "/bookings/" + str(first_booking.booking_id) + "/reschedule/"
            + str(reschedule["id"]),
            data={"csrf_token": self.csrf(host_page), "action": "accept"},
            follow_redirects=True,
        )
        self.assertIn(b"requested period is no longer available", response.data)
        self.assertEqual(data_store.find_booking(first_booking.booking_id).start_time,
                         original_start)
        self.assertEqual(data_store.find_booking(second_booking.booking_id).start_time,
                         requested_start)

    def test_guests_cannot_save_listings_or_access_favorites(self):
        self.assertEqual(self.client.get("/favorites").status_code, 302)
        self.assertEqual(
            self.client.post("/favorites/1/toggle").status_code, 400
        )
        self.assertEqual(data_store.saved_spots, set())
        host = self.app.test_client()
        self.register(host, "favorite_host_role", "Host")
        page = host.get("/spots")
        self.assertEqual(host.get("/favorites").status_code, 302)
        response = host.post("/favorites/1/toggle", data={
            "csrf_token": self.csrf(page),
        })
        self.assertEqual(response.status_code, 302)
        self.assertIn("/dashboard", response.location)
        self.assertEqual(data_store.saved_spots, set())

    def test_guest_can_browse_book_and_manage_a_sample_listing(self):
        self.app = create_app({
            "TESTING": True,
            "SECRET_KEY": "test-key",
            "DEMO_MODE": True,
            "UPLOAD_FOLDER": os.path.join(self.temp_dir.name, "uploads"),
        })
        self.client = self.app.test_client()
        seeded_count = len(data_store.spots_db)
        data_store.configure_builtin_listings(True)
        self.assertEqual(len(data_store.spots_db), seeded_count)
        self.assertGreaterEqual(seeded_count, 20)
        self.assertTrue(all(spot.is_demo for spot in data_store.spots_db))

        browse = self.client.get("/spots")
        self.assertEqual(browse.status_code, 200)
        self.assertIn(b"BUILT-IN SAMPLE", browse.data)
        spot = data_store.spots_db[0]
        response = self.client.post("/bookings/create", data={
            "csrf_token": self.csrf(browse),
            "spot_id": str(spot.spot_id),
            "guest_name": "Guest Driver",
            "vehicle_type": "Car",
            "start_time": (datetime.now() + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M"),
            "hours": "1",
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        data_store.configure_storage(self.database_path)
        booking = data_store.bookings_db[0]
        self.assertEqual(booking.guest_name, "Guest Driver")
        self.assertTrue(booking.guest_id)
        self.assertIn(b"Guest Driver", response.data)
        self.assertIn(b"Guest bookings", response.data)

        other_client = self.app.test_client()
        other_page = other_client.get("/spots")
        self.assertNotIn(b"Guest Driver", other_client.get("/bookings").data)
        forbidden = other_client.post(
            "/bookings/" + str(booking.booking_id) + "/cancel",
            data={"csrf_token": self.csrf(other_page)},
        )
        self.assertEqual(forbidden.status_code, 404)

        booking_page = self.client.get("/bookings")
        cancelled = self.client.post(
            "/bookings/" + str(booking.booking_id) + "/cancel",
            data={"csrf_token": self.csrf(booking_page)},
            follow_redirects=True,
        )
        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(booking.status, "Cancelled")

    def test_sample_listings_are_off_by_default_and_disabled_samples_cannot_be_booked(self):
        demo_app = create_app({
            "TESTING": True,
            "SECRET_KEY": "test-key",
            "DEMO_MODE": True,
        })
        self.assertTrue(any(spot.is_demo and spot.is_active for spot in data_store.spots_db))

        normal_app = create_app({
            "TESTING": True,
            "SECRET_KEY": "test-key",
            "DEMO_MODE": False,
        })
        self.assertFalse(any(spot.is_demo and spot.is_active for spot in data_store.spots_db))
        normal_client = normal_app.test_client()
        response = normal_client.get("/spots")
        self.assertNotIn(b"BUILT-IN SAMPLE", response.data)
        self.assertIn(b"No parking listings match yet", response.data)

        spot = next(spot for spot in data_store.spots_db if spot.is_demo)
        booking_form = normal_client.get("/register")
        rejected = normal_client.post("/bookings/create", data={
            "csrf_token": self.csrf(booking_form),
            "spot_id": str(spot.spot_id),
            "guest_name": "Guest Driver",
            "vehicle_type": "Car",
            "start_time": (datetime.now() + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M"),
            "hours": "1",
        }, follow_redirects=True)
        self.assertIn(b"That spot is no longer available.", rejected.data)
        self.assertEqual(data_store.bookings_db, [])

    @patch("tomtom_parking.search_nearby")
    def test_tomtom_facilities_are_discovery_only(self, search_nearby):
        search_nearby.return_value = [{
            "id": "provider-facility-1",
            "name": "Example Public Garage",
            "address": "Central Mumbai",
            "latitude": 19.076,
            "longitude": 72.8777,
            "distance_km": 0.4,
            "availability": True,
            "empty_spots": 3,
            "updated_at": "2026-11-08T06:30:00Z",
        }]
        provider_app = create_app({
            "TESTING": True,
            "SECRET_KEY": "test-key",
            "DEMO_MODE": False,
            "TOMTOM_API_KEY": "test-provider-key",
        })
        client = provider_app.test_client()
        response = client.get(
            "/spots?near_lat=19.076&near_lon=72.8777&radius_km=5"
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Example Public Garage", response.data)
        self.assertIn(b"3 spaces reported free", response.data)
        self.assertIn(b"ParkShare cannot reserve a space there", response.data)
        self.assertNotIn(b'name="spot_id" value="provider-facility-1"', response.data)
        search_nearby.assert_called_once_with(19.076, 72.8777, 5.0, "test-provider-key")

    @patch("tomtom_parking._get_json")
    def test_tomtom_adapter_uses_search_availability_and_validates_results(self, get_json):
        get_json.side_effect = [
            {"results": [{
                "id": "facility-1",
                "poi": {"name": "Central Garage"},
                "address": {"freeformAddress": "Mumbai"},
                "position": {"lat": 19.076, "lon": 72.8777},
                "dist": 420,
                "dataSources": {"parkingAvailability": {"id": "availability-1"}},
            }]},
            {"statuses": [{"current": {
                "available": True,
                "emptySpots": 4,
                "updatedAt": "2026-11-08T06:30:00Z",
            }}]},
        ]
        from tomtom_parking import search_nearby

        facilities = search_nearby(19.076, 72.8777, 5, "test-key")
        self.assertEqual(len(facilities), 1)
        self.assertEqual(facilities[0]["name"], "Central Garage")
        self.assertEqual(facilities[0]["distance_km"], 0.4)
        self.assertTrue(facilities[0]["availability"])
        self.assertEqual(facilities[0]["empty_spots"], 4)
        self.assertEqual(get_json.call_count, 2)

    def test_registration_validation_keeps_profile_fields(self):
        response = self.client.get("/register")
        response = self.client.post("/register", data={
            "csrf_token": self.csrf(response),
            "username": "short",
            "full_name": "A Driver",
            "email": "not-an-email",
            "role": "Driver",
            "vehicle_type": "Car",
            "password": "securepass1",
            "confirm_password": "securepass1",
            "terms": "accepted",
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Enter a valid email address.", response.data)
        self.assertIn(b'value="short"', response.data)
        self.assertNotIn("short", data_store.users_db)

    def test_host_can_list_and_driver_can_book_checkout_and_rate(self):
        host_client = self.app.test_client()
        self.register(host_client, "host_one", "Host")
        host_page = host_client.get("/host/spots")
        response = host_client.post("/host/spots", data={
            "csrf_token": self.csrf(host_page),
            "location": "Central Mumbai",
            "description": "Covered space close to the station",
            "price_per_hour": "75",
            "total_slots": "2",
            "latitude": "19.0760",
            "longitude": "72.8777",
            "vehicle_types": ["Car", "SUV"],
            "covered": "on",
            "ev_charging": "on",
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        spot = data_store.spots_db[0]
        self.assertTrue(spot.covered)
        self.assertTrue(spot.ev_charging)
        self.assertEqual(spot.vehicle_types, ["Car", "SUV"])

        host_page = host_client.get("/host/spots")
        response = host_client.post(
            "/host/spots/" + str(spot.spot_id) + "/edit",
            data={
                "csrf_token": self.csrf(host_page),
                "location": "Central Mumbai",
                "description": "Updated directions for the entrance",
                "price_per_hour": "75",
                "latitude": "19.0760",
                "longitude": "72.8777",
                "vehicle_types": ["Car", "SUV"],
                "covered": "on",
                "ev_charging": "on",
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(spot.description, "Updated directions for the entrance")

        driver_client = self.app.test_client()
        self.register(driver_client, "driver_two")
        browse = driver_client.get("/spots?covered=1&ev_charging=1&vehicle_type=Car")
        self.assertIn(b"Central Mumbai", browse.data)
        response = driver_client.post("/bookings/create", data={
            "csrf_token": self.csrf(browse), "spot_id": str(spot.spot_id), "hours": "2",
        }, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        booking = data_store.bookings_db[0]
        self.assertEqual(spot.available_slots, 1)

        host_page = host_client.get("/host/spots")
        response = host_client.post(
            "/host/spots/" + str(spot.spot_id) + "/remove",
            data={"csrf_token": self.csrf(host_page)},
            follow_redirects=True,
        )
        self.assertIn(b"cannot be removed", response.data)
        self.assertTrue(spot.is_active)

        booking_page = driver_client.get("/bookings")
        response = driver_client.post(
            "/bookings/" + str(booking.booking_id) + "/checkout",
            data={"csrf_token": self.csrf(booking_page), "promo_code": "PARK10", "rating": "5"},
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(booking.status, "Completed")
        self.assertEqual(spot.available_slots, 2)
        self.assertEqual(spot.average_rating(), 5)
        self.assertIsNotNone(booking.final_cost)
        earnings = host_client.get("/host/earnings")
        self.assertEqual(earnings.status_code, 200)
        self.assertIn(b"30-day occupancy", earnings.data)
        self.assertIn(b"Monthly earnings", earnings.data)

    def test_booking_emails_and_reminders_are_sent_once(self):
        self.app.config.update(
            SMTP_HOST="smtp.example.com",
            SMTP_FROM="parkshare@example.com",
            SMTP_PORT=587,
            SMTP_USER="",
            SMTP_PASSWORD="",
        )
        host = self.app.test_client()
        self.register(host, "mail_host", "Host")
        host_page = host.get("/host/spots")
        host.post("/host/spots", data={
            "csrf_token": self.csrf(host_page),
            "location": "Email reminder garage",
            "price_per_hour": "40",
            "total_slots": "1",
            "latitude": "19.0760",
            "longitude": "72.8777",
            "vehicle_types": ["Car"],
        })
        driver = self.app.test_client()
        self.register(driver, "mail_driver")
        booking_start = datetime.now() + timedelta(hours=12)
        browse = driver.get("/spots")
        with patch("web_app.smtplib.SMTP") as smtp:
            response = driver.post("/bookings/create", data={
                "csrf_token": self.csrf(browse),
                "spot_id": str(data_store.spots_db[0].spot_id),
                "hours": "1",
                "start_time": booking_start.strftime("%Y-%m-%dT%H:%M"),
            }, follow_redirects=True)
            self.assertEqual(response.status_code, 200)
            send_message = smtp.return_value.__enter__.return_value.send_message
            self.assertEqual(send_message.call_count, 2)
            recipients = {
                call.args[0]["To"] for call in send_message.call_args_list
            }
            self.assertEqual(recipients, {
                "mail_host@example.com", "mail_driver@example.com",
            })

            runner = self.app.test_cli_runner()
            sent = runner.invoke(args=["send-booking-reminders"])
            self.assertEqual(sent.exit_code, 0, sent.output)
            self.assertIn("Sent 1 booking reminder", sent.output)
            booking = data_store.bookings_db[0]
            self.assertIsNotNone(booking.reminder_sent_at)
            self.assertEqual(send_message.call_count, 3)
            repeated = runner.invoke(args=["send-booking-reminders"])
            self.assertEqual(repeated.exit_code, 0, repeated.output)
            self.assertIn("Sent 0 booking reminder", repeated.output)
            self.assertEqual(send_message.call_count, 3)

            booking.start_time = datetime.now() - timedelta(minutes=1)
            booking.end_time = datetime.now() + timedelta(hours=1)
            booking_page = driver.get("/bookings")
            checked_in = driver.post(
                "/bookings/" + str(booking.booking_id) + "/check-in",
                data={"csrf_token": self.csrf(booking_page)},
            )
            self.assertEqual(checked_in.status_code, 302)
            booking_page = driver.get("/bookings")
            checked_out = driver.post(
                "/bookings/" + str(booking.booking_id) + "/checkout",
                data={"csrf_token": self.csrf(booking_page)},
            )
            self.assertEqual(checked_out.status_code, 302)
            self.assertEqual(booking.status, "Completed")
            subjects = [
                call.args[0]["Subject"] for call in send_message.call_args_list
            ]
            self.assertEqual(subjects.count("ParkShare check-in recorded"), 2)
            self.assertEqual(subjects.count("ParkShare checkout completed"), 2)

        data_store.configure_storage(self.database_path)
        self.assertIsNotNone(data_store.bookings_db[0].reminder_sent_at)

    def test_failed_booking_reminder_is_not_marked_sent(self):
        self.app.config.update(
            SMTP_HOST="smtp.example.com",
            SMTP_FROM="parkshare@example.com",
        )
        host = self.app.test_client()
        self.register(host, "retry_host", "Host")
        host_page = host.get("/host/spots")
        host.post("/host/spots", data={
            "csrf_token": self.csrf(host_page),
            "location": "Reminder retry garage",
            "price_per_hour": "40",
            "total_slots": "1",
            "latitude": "19.0760",
            "longitude": "72.8777",
            "vehicle_types": ["Car"],
        })
        driver = self.app.test_client()
        self.register(driver, "retry_driver")
        browse = driver.get("/spots")
        driver.post("/bookings/create", data={
            "csrf_token": self.csrf(browse),
            "spot_id": str(data_store.spots_db[0].spot_id),
            "hours": "1",
            "start_time": (datetime.now() + timedelta(hours=12)).strftime("%Y-%m-%dT%H:%M"),
        })

        with patch("web_app.smtplib.SMTP") as smtp:
            smtp.return_value.__enter__.return_value.send_message.side_effect = OSError(
                "SMTP unavailable"
            )
            result = self.app.test_cli_runner().invoke(
                args=["send-booking-reminders"]
            )
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("Sent 0 booking reminder", result.output)
        self.assertIsNone(data_store.bookings_db[0].reminder_sent_at)

    def test_reminder_command_requires_smtp_configuration(self):
        result = self.app.test_cli_runner().invoke(
            args=["send-booking-reminders"]
        )
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("Configure PARKSHARE_SMTP_HOST", result.output)

    def test_driver_can_check_in_and_complete_a_booking(self):
        host = self.app.test_client()
        self.register(host, "checkin_host", "Host")
        host_page = host.get("/host/spots")
        host.post("/host/spots", data={
            "csrf_token": self.csrf(host_page), "location": "Check-in garage",
            "price_per_hour": "40", "total_slots": "1",
            "latitude": "19.0760", "longitude": "72.8777",
            "vehicle_types": ["Car"],
        })
        self.register(self.client, "checkin_driver")
        browse = self.client.get("/spots")
        start = (datetime.now() + timedelta(minutes=5)).replace(second=0, microsecond=0)
        self.client.post("/bookings/create", data={
            "csrf_token": self.csrf(browse), "spot_id": "1", "hours": "2",
            "start_time": start.strftime("%Y-%m-%dT%H:%M"),
        })
        booking = data_store.bookings_db[0]
        self.assertEqual(booking.status, "Active")
        booking.start_time = datetime.now() - timedelta(minutes=1)
        booking.end_time = datetime.now() + timedelta(hours=1)
        booking.entry_time = booking.start_time
        data_store.persist()

        page = self.client.get("/bookings")
        self.assertIn(b"Check in", page.data)
        checked_in = self.client.post(
            "/bookings/" + str(booking.booking_id) + "/check-in",
            data={"csrf_token": self.csrf(page)},
            follow_redirects=True,
        )
        self.assertEqual(booking.status, "Checked In")
        self.assertIsNotNone(booking.checkin_time)
        self.assertIn(booking, data_store.active_bookings())
        self.assertIn(b"checked in", checked_in.data.lower())

        page = self.client.get("/bookings")
        completed = self.client.post(
            "/bookings/" + str(booking.booking_id) + "/checkout",
            data={"csrf_token": self.csrf(page)},
            follow_redirects=True,
        )
        self.assertEqual(booking.status, "Completed")
        self.assertIn(b"Checkout complete", completed.data)

    def test_razorpay_checkout_verifies_captured_amount_before_completion(self):
        payment_app = create_app({
            "TESTING": True,
            "SECRET_KEY": "test-key",
            "DEMO_MODE": False,
            "UPLOAD_FOLDER": os.path.join(self.temp_dir.name, "uploads"),
            "PAYMENT_MODE": "test",
            "RAZORPAY_KEY_ID": "rzp_test_example",
            "RAZORPAY_KEY_SECRET": "test-secret",
        })
        host = payment_app.test_client()
        self.register(host, "payment_host", "Host")
        host_page = host.get("/host/spots")
        host.post("/host/spots", data={
            "csrf_token": self.csrf(host_page), "location": "Paid checkout garage",
            "price_per_hour": "75", "total_slots": "1",
            "latitude": "19.0760", "longitude": "72.8777",
            "vehicle_types": ["Car"],
        })
        driver = payment_app.test_client()
        self.register(driver, "payment_driver")
        browse = driver.get("/spots")
        driver.post("/bookings/create", data={
            "csrf_token": self.csrf(browse), "spot_id": "1", "hours": "1",
            "start_time": datetime.now().strftime("%Y-%m-%dT%H:%M"),
        })
        booking = data_store.bookings_db[0]

        with patch("payment_module.create_order", return_value={
                "id": "order_test_123", "amount": 6750, "currency": "INR"}):
            page = driver.get("/bookings")
            order_response = driver.post(
                "/bookings/" + str(booking.booking_id) + "/checkout",
                data={"csrf_token": self.csrf(page), "promo_code": "PARK10"},
            )
        self.assertEqual(order_response.status_code, 200)
        self.assertEqual(order_response.get_json()["order_id"], "order_test_123")
        self.assertEqual(booking.payment_status, "created")
        self.assertEqual(booking.payment_amount, 6750)
        self.assertEqual(booking.status, "Active")
        data_store.configure_storage(self.database_path)
        booking = data_store.find_booking(booking.booking_id)
        self.assertEqual(booking.payment_order_id, "order_test_123")
        self.assertEqual(booking.payment_amount, 6750)
        self.assertEqual(booking.payment_status, "created")

        with patch("payment_module.verify_checkout_signature", return_value=True), patch(
                "payment_module.fetch_payment", return_value={
                    "id": "pay_test_123",
                    "order_id": "order_test_123",
                    "amount": 6750,
                    "currency": "INR",
                    "status": "captured",
                }):
            page = driver.get("/bookings")
            verified = driver.post(
                "/bookings/" + str(booking.booking_id) + "/payment/verify",
                data={
                    "csrf_token": self.csrf(page),
                    "razorpay_order_id": "order_test_123",
                    "razorpay_payment_id": "pay_test_123",
                    "razorpay_signature": "verified-signature",
                    "rating": "5",
                },
                follow_redirects=True,
            )
        self.assertEqual(verified.status_code, 200)
        self.assertEqual(booking.status, "Completed")
        self.assertEqual(booking.payment_status, "captured")
        self.assertEqual(booking.payment_id, "pay_test_123")
        self.assertEqual(booking.final_cost, 67.5)
        receipt = driver.get("/bookings/" + str(booking.booking_id) + "/receipt")
        self.assertEqual(receipt.status_code, 200)
        self.assertIn(b"Paid via Razorpay", receipt.data)
        self.assertIn(b"67.50", receipt.data)

    def test_razorpay_rejects_bad_signatures_and_uncaptured_payments(self):
        payment_app = create_app({
            "TESTING": True,
            "SECRET_KEY": "test-key",
            "DEMO_MODE": False,
            "PAYMENT_MODE": "test",
            "RAZORPAY_KEY_ID": "rzp_test_example",
            "RAZORPAY_KEY_SECRET": "test-secret",
        })
        host = payment_app.test_client()
        self.register(host, "badpay_host", "Host")
        host_page = host.get("/host/spots")
        host.post("/host/spots", data={
            "csrf_token": self.csrf(host_page), "location": "Payment verification garage",
            "price_per_hour": "30", "total_slots": "1",
            "latitude": "19.0760", "longitude": "72.8777",
            "vehicle_types": ["Car"],
        })
        driver = payment_app.test_client()
        self.register(driver, "badpay_driver")
        browse = driver.get("/spots")
        driver.post("/bookings/create", data={
            "csrf_token": self.csrf(browse), "spot_id": "1", "hours": "1",
            "start_time": datetime.now().strftime("%Y-%m-%dT%H:%M"),
        })
        booking = data_store.bookings_db[0]
        with patch("payment_module.create_order", return_value={
                "id": "order_bad_sig", "amount": 3000, "currency": "INR"}):
            page = driver.get("/bookings")
            driver.post(
                "/bookings/" + str(booking.booking_id) + "/checkout",
                data={"csrf_token": self.csrf(page)},
            )
        page = driver.get("/bookings")
        with patch("payment_module.verify_checkout_signature", return_value=False):
            rejected = driver.post(
                "/bookings/" + str(booking.booking_id) + "/payment/verify",
                data={
                    "csrf_token": self.csrf(page),
                    "razorpay_order_id": "order_bad_sig",
                    "razorpay_payment_id": "pay_bad_sig",
                    "razorpay_signature": "forged",
                },
            )
        self.assertEqual(rejected.status_code, 400)
        self.assertEqual(booking.status, "Active")
        self.assertEqual(booking.payment_status, "created")

        with patch("payment_module.verify_checkout_signature", return_value=True), patch(
                "payment_module.fetch_payment", return_value={
                    "order_id": "order_bad_sig",
                    "amount": booking.payment_amount,
                    "currency": "INR",
                    "status": "authorized",
                }):
            rejected = driver.post(
                "/bookings/" + str(booking.booking_id) + "/payment/verify",
                data={
                    "csrf_token": self.csrf(page),
                    "razorpay_order_id": "order_bad_sig",
                    "razorpay_payment_id": "pay_not_captured",
                    "razorpay_signature": "valid",
                },
            )
        self.assertEqual(rejected.status_code, 400)
        self.assertEqual(booking.status, "Active")

    def test_razorpay_webhook_verifies_signature_and_completes_payment_idempotently(self):
        webhook_secret = "hook-secret"
        payment_app = create_app({
            "TESTING": True,
            "SECRET_KEY": "test-key",
            "DEMO_MODE": False,
            "PAYMENT_MODE": "test",
            "RAZORPAY_KEY_ID": "rzp_test_example",
            "RAZORPAY_KEY_SECRET": "test-secret",
            "RAZORPAY_WEBHOOK_SECRET": webhook_secret,
        })
        host = payment_app.test_client()
        self.register(host, "webhook_host", "Host")
        host_page = host.get("/host/spots")
        host.post("/host/spots", data={
            "csrf_token": self.csrf(host_page), "location": "Webhook garage",
            "price_per_hour": "25", "total_slots": "1",
            "latitude": "19.0760", "longitude": "72.8777",
            "vehicle_types": ["Car"],
        })
        driver = payment_app.test_client()
        self.register(driver, "webhook_driver")
        browse = driver.get("/spots")
        driver.post("/bookings/create", data={
            "csrf_token": self.csrf(browse), "spot_id": "1", "hours": "1",
            "start_time": datetime.now().strftime("%Y-%m-%dT%H:%M"),
        })
        booking = data_store.bookings_db[0]
        with patch("payment_module.create_order", return_value={
                "id": "order_webhook_123", "amount": 2500, "currency": "INR"}):
            page = driver.get("/bookings")
            driver.post(
                "/bookings/" + str(booking.booking_id) + "/checkout",
                data={"csrf_token": self.csrf(page)},
            )

        payload = (
            '{"event":"payment.captured","payload":{"payment":{"entity":{'
            '"id":"pay_webhook_123","order_id":"order_webhook_123",'
            '"amount":2500,"currency":"INR","status":"captured"}}}}'
        ).encode("utf-8")
        signature = hmac.new(
            webhook_secret.encode("utf-8"), payload, hashlib.sha256
        ).hexdigest()
        invalid = driver.post(
            "/payments/razorpay/webhook",
            data=payload,
            headers={"X-Razorpay-Signature": "invalid"},
        )
        self.assertEqual(invalid.status_code, 400)
        accepted = driver.post(
            "/payments/razorpay/webhook",
            data=payload,
            headers={"X-Razorpay-Signature": signature},
        )
        duplicate = driver.post(
            "/payments/razorpay/webhook",
            data=payload,
            headers={"X-Razorpay-Signature": signature},
        )
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(duplicate.status_code, 200)
        self.assertEqual(booking.status, "Completed")
        self.assertEqual(booking.payment_status, "captured")
        self.assertEqual(booking.payment_id, "pay_webhook_123")

    def test_only_configured_admin_can_approve_host_verification(self):
        self.app = create_app({
            "TESTING": True,
            "SECRET_KEY": "test-key",
            "DEMO_MODE": False,
            "UPLOAD_FOLDER": os.path.join(self.temp_dir.name, "uploads"),
            "ADMIN_USERNAMES": ("trusted_moderator",),
        })
        self.client = self.app.test_client()
        moderator = self.app.test_client()
        self.register(moderator, "trusted_moderator")
        host = self.app.test_client()
        self.register(host, "pending_host", "Host")
        host_page = host.get("/host/spots")
        host.post("/host/spots", data={
            "csrf_token": self.csrf(host_page),
            "location": "Verification badge garage",
            "price_per_hour": "35",
            "total_slots": "1",
            "latitude": "19.0760",
            "longitude": "72.8777",
            "vehicle_types": ["Car"],
        })

        forbidden = host.get("/admin")
        self.assertEqual(forbidden.status_code, 403)
        profile = host.get("/profile")
        requested = host.post(
            "/verification/request",
            data={
                "csrf_token": self.csrf(profile),
                "verification_note": "Please review my host account.",
            },
            follow_redirects=True,
        )
        self.assertIn(b"Verification request sent", requested.data)
        self.assertEqual(data_store.users_db["pending_host"].verification_status, "pending")
        self.assertNotIn(b"Moderation", host.get("/dashboard").data)

        panel = moderator.get("/admin")
        self.assertEqual(panel.status_code, 200)
        self.assertIn(b"Please review my host account.", panel.data)
        approved = moderator.post("/admin", data={
            "csrf_token": self.csrf(panel),
            "action": "verify",
            "username": "pending_host",
        }, follow_redirects=True)
        self.assertEqual(data_store.users_db["pending_host"].verification_status, "verified")
        self.assertIsNotNone(data_store.users_db["pending_host"].verified_at)
        browse = self.client.get("/spots")
        self.assertIn(b"Verified host", browse.data)
        self.assertIn(b"Moderation", moderator.get("/dashboard").data)

        data_store.configure_storage(self.database_path)
        self.assertEqual(data_store.users_db["pending_host"].verification_status, "verified")

    def test_completed_booking_reviews_and_reports_are_moderated_and_persisted(self):
        self.app = create_app({
            "TESTING": True,
            "SECRET_KEY": "test-key",
            "DEMO_MODE": False,
            "UPLOAD_FOLDER": os.path.join(self.temp_dir.name, "uploads"),
            "ADMIN_USERNAMES": ("review_moderator",),
        })
        self.client = self.app.test_client()
        moderator = self.app.test_client()
        self.register(moderator, "review_moderator")
        host = self.app.test_client()
        self.register(host, "review_host", "Host")
        host_page = host.get("/host/spots")
        host.post("/host/spots", data={
            "csrf_token": self.csrf(host_page),
            "location": "Trust review garage",
            "price_per_hour": "45",
            "total_slots": "1",
            "latitude": "19.0760",
            "longitude": "72.8777",
            "vehicle_types": ["Car"],
        })
        driver = self.app.test_client()
        self.register(driver, "review_driver")
        browse = driver.get("/spots")
        driver.post("/bookings/create", data={
            "csrf_token": self.csrf(browse),
            "spot_id": "1",
            "hours": "1",
            "start_time": datetime.now().strftime("%Y-%m-%dT%H:%M"),
        })
        booking = data_store.bookings_db[0]
        booking.start_time = datetime.now() - timedelta(minutes=1)
        booking.entry_time = booking.start_time
        booking.end_time = datetime.now() + timedelta(hours=1)
        data_store.persist()
        booking_page = driver.get("/bookings")
        driver.post(
            "/bookings/" + str(booking.booking_id) + "/checkout",
            data={"csrf_token": self.csrf(booking_page)},
        )
        self.assertEqual(booking.status, "Completed")

        driver_page = driver.get("/bookings")
        self.assertIn(b"Review your host", driver_page.data)
        review_response = driver.post(
            "/bookings/" + str(booking.booking_id) + "/reviews",
            data={
                "csrf_token": self.csrf(driver_page),
                "rating": "5",
                "review_text": "The space was exactly as described.",
            },
            follow_redirects=True,
        )
        self.assertIn(b"Thanks for sharing your review", review_response.data)
        host_page = host.get("/bookings")
        host_review = host.post(
            "/bookings/" + str(booking.booking_id) + "/reviews",
            data={
                "csrf_token": self.csrf(host_page),
                "rating": "4",
                "review_text": "The driver followed the parking instructions.",
            },
            follow_redirects=True,
        )
        self.assertIn(b"Thanks for sharing your review", host_review.data)
        self.assertEqual(len(data_store.reviews), 2)

        panel = driver.get("/spots")
        self.assertIn(b"5.0 / 5 from 1 verified booking review", panel.data)
        duplicate = driver.post(
            "/bookings/" + str(booking.booking_id) + "/reviews",
            data={
                "csrf_token": self.csrf(driver.get("/bookings")),
                "rating": "3",
                "review_text": "A second review should not be allowed.",
            },
            follow_redirects=True,
        )
        self.assertIn(b"already reviewed", duplicate.data)
        self.assertEqual(len(data_store.reviews), 2)

        report_page = driver.get("/spots")
        report_response = driver.post("/reports/create", data={
            "csrf_token": self.csrf(report_page),
            "target_type": "user",
            "target_id": "review_host",
            "reason": "other",
            "details": "Please review this account activity.",
        }, follow_redirects=True)
        self.assertIn(b"Your report was sent", report_response.data)
        report = data_store.reports[0]
        panel = moderator.get("/admin")
        self.assertIn(b"Please review this account activity.", panel.data)
        moderator.post("/admin", data={
            "csrf_token": self.csrf(panel),
            "action": "action_report",
            "report_id": str(report["id"]),
            "resolution": "Reviewed and contacted the host.",
        })
        self.assertEqual(report["status"], "actioned")
        self.assertIn("Reviewed and contacted", report["resolution"])
        self.assertTrue(data_store.users_db["review_host"].is_suspended)

        panel = moderator.get("/admin")
        moderator.post("/admin", data={
            "csrf_token": self.csrf(panel),
            "action": "hide_review",
            "review_id": str(data_store.reviews[0]["id"]),
        })
        self.assertFalse(data_store.reviews[0]["visible"])
        self.assertNotIn(b"The space was exactly as described.", driver.get("/spots").data)
        data_store.configure_storage(self.database_path)
        self.assertEqual(len(data_store.reviews), 2)
        self.assertEqual(len(data_store.reports), 1)
        self.assertEqual(data_store.reports[0]["status"], "actioned")
        self.assertFalse(data_store.reviews[0]["visible"])

    def test_driver_cannot_book_a_vehicle_incompatible_spot(self):
        self.register(self.client, "motorcyclist", vehicle_type="Motorcycle")
        host = self.app.test_client()
        self.register(host, "host_two", "Host")
        host_page = host.get("/host/spots")
        host.post("/host/spots", data={
            "csrf_token": self.csrf(host_page), "location": "Van-only garage",
            "price_per_hour": "50", "total_slots": "1",
            "latitude": "19.0760", "longitude": "72.8777",
            "vehicle_types": ["Van"],
        })
        browse = self.client.get("/spots")
        response = self.client.post("/bookings/create", data={
            "csrf_token": self.csrf(browse), "spot_id": str(data_store.spots_db[0].spot_id), "hours": "1",
        }, follow_redirects=True)
        self.assertIn(b"does not support your registered vehicle type", response.data)
        self.assertEqual(data_store.bookings_db, [])

    def test_booking_windows_prevent_overlaps_but_allow_other_times(self):
        host = self.app.test_client()
        self.register(host, "time_host", "Host")
        host_page = host.get("/host/spots")
        host.post("/host/spots", data={
            "csrf_token": self.csrf(host_page), "location": "Timed parking",
            "price_per_hour": "40", "total_slots": "1",
            "latitude": "19.0760", "longitude": "72.8777",
            "vehicle_types": ["Car"],
        })
        start = (datetime.now() + timedelta(days=1)).replace(second=0, microsecond=0)
        start_value = start.strftime("%Y-%m-%dT%H:%M")
        self.register(self.client, "time_driver_one")
        browse = self.client.get("/spots")
        first = self.client.post("/bookings/create", data={
            "csrf_token": self.csrf(browse), "spot_id": "1",
            "hours": "2", "start_time": start_value,
        }, follow_redirects=True)
        self.assertIn(b"Booking confirmed", first.data)

        second_driver = self.app.test_client()
        self.register(second_driver, "time_driver_two")
        browse = second_driver.get("/spots")
        conflicting = second_driver.post("/bookings/create", data={
            "csrf_token": self.csrf(browse), "spot_id": "1",
            "hours": "1", "start_time": (start + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M"),
        }, follow_redirects=True)
        self.assertIn(b"No space is available", conflicting.data)
        non_overlapping = second_driver.post("/bookings/create", data={
            "csrf_token": self.csrf(conflicting), "spot_id": "1",
            "hours": "1", "start_time": (start + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M"),
        }, follow_redirects=True)
        self.assertIn(b"Booking confirmed", non_overlapping.data)

        browse = self.client.get("/spots")
        current_booking = self.client.post("/bookings/create", data={
            "csrf_token": self.csrf(browse), "spot_id": "1",
            "hours": "1", "start_time": datetime.now().strftime("%Y-%m-%dT%H:%M"),
        }, follow_redirects=True)
        self.assertIn(b"Booking confirmed", current_booking.data)
        browse = second_driver.get("/spots")
        while_active = second_driver.post("/bookings/create", data={
            "csrf_token": self.csrf(browse), "spot_id": "1", "hours": "1",
            "start_time": (datetime.now() + timedelta(days=5)).strftime("%Y-%m-%dT%H:%M"),
        }, follow_redirects=True)
        self.assertIn(b"No space is available", while_active.data)

    def test_persistent_store_reloads_saved_accounts_and_bookings(self):
        host = self.app.test_client()
        self.register(host, "persist_host", "Host")
        host_page = host.get("/host/spots")
        host.post("/host/spots", data={
            "csrf_token": self.csrf(host_page), "location": "Persistent garage",
            "price_per_hour": "55", "total_slots": "1",
            "latitude": "19.0760", "longitude": "72.8777",
            "vehicle_types": ["Car"],
        })
        self.register(self.client, "persist_driver")
        browse = self.client.get("/spots")
        self.client.post("/bookings/create", data={
            "csrf_token": self.csrf(browse), "spot_id": "1", "hours": "1",
        })
        stored_path = os.path.join(self.temp_dir.name, "test.sqlite3")
        data_store.configure_storage(stored_path)
        self.assertIn("persist_driver", data_store.users_db)
        self.assertTrue(check_password_hash(data_store.users_db["persist_driver"].password, "securepass1"))
        self.assertEqual(len(data_store.spots_db), 1)
        self.assertEqual(data_store.spots_db[0].latitude, 19.076)
        self.assertEqual(data_store.spots_db[0].longitude, 72.8777)
        self.assertEqual(len(data_store.bookings_db), 1)
        self.assertEqual(data_store.bookings_db[0].driver_username, "persist_driver")

    def test_map_search_filters_by_radius_and_registered_vehicle(self):
        host = self.app.test_client()
        self.register(host, "map_host", "Host")
        listings = [
            ("Central car parking", "100", "19.0760", "72.8777", ["Car"]),
            ("Nearby car parking", "50", "19.0800", "72.8800", ["Car"]),
            ("Motorcycle parking", "40", "19.0760", "72.8777", ["Motorcycle"]),
            ("Far car parking", "25", "19.5000", "73.4000", ["Car"]),
        ]
        for location, price, latitude, longitude, vehicle_types in listings:
            page = host.get("/host/spots")
            host.post("/host/spots", data={
                "csrf_token": self.csrf(page), "location": location,
                "price_per_hour": price, "total_slots": "1",
                "latitude": latitude, "longitude": longitude,
                "vehicle_types": vehicle_types,
            })

        self.register(self.client, "nearby_driver", vehicle_type="Car")
        response = self.client.get(
            "/spots?location=typed+place&near_lat=19.0760&near_lon=72.8777&radius_km=5"
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Central car parking", response.data)
        self.assertIn(b"Nearby car parking", response.data)
        self.assertNotIn(b"Motorcycle parking", response.data)
        self.assertNotIn(b"Far car parking", response.data)
        self.assertIn(b"km away", response.data)
        self.assertIn(b"match", response.data)
        map_script = self.client.get("/static/parking-map.js")
        map_script_data = map_script.get_data()
        map_script.close()
        self.assertIn(b"Finding the typed place before searching nearby", map_script_data)
        self.assertIn(b"findPlace(locationInput.value.trim())", map_script_data)
        self.assertIn(b"&countrycodes=in", map_script_data)
        self.assertIn(b'event.key === "Enter"', map_script_data)
        self.assertIn(b"Show on map", response.data)
        self.assertLess(
            response.data.index(b"<h2>Nearby car parking</h2>"),
            response.data.index(b"<h2>Central car parking</h2>"),
        )
        self.assertIn(b"data-parking-map", response.data)
        self.assertIn(b"parking-map.js", response.data)

    def test_host_must_pin_the_listing_entrance(self):
        host = self.app.test_client()
        self.register(host, "unmapped_host", "Host")
        page = host.get("/host/spots")
        self.assertIn(b"pin the entrance", page.data)
        self.assertIn(b"data-parking-map", page.data)
        response = host.post("/host/spots", data={
            "csrf_token": self.csrf(page), "location": "Unpinned parking",
            "price_per_hour": "45", "total_slots": "1", "vehicle_types": ["Car"],
        }, follow_redirects=True)
        self.assertIn(b"Pin the parking entrance on the map", response.data)
        self.assertEqual(data_store.spots_db, [])

    def test_empty_search_explains_that_a_host_must_list_a_space(self):
        self.register(self.client, "empty_inventory_driver")
        response = self.client.get("/spots")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"No parking listings match yet", response.data)
        self.assertIn(b"Create a host account", response.data)

    def test_demo_coverage_markers_are_generated_every_five_km_and_never_listed_as_spots(self):
        markers = _demo_area_markers(19.076, 72.8777, 20)
        self.assertEqual(len(markers), 32)
        for distance in (5, 10, 15, 20):
            ring = [marker for marker in markers if marker["distance_km"] == distance]
            self.assertEqual(len(ring), 8)
            for marker in ring:
                self.assertAlmostEqual(
                    _distance_km(19.076, 72.8777, marker["latitude"], marker["longitude"]),
                    distance, delta=0.01,
                )
                self.assertTrue(marker["demo"])

        self.register(self.client, "coverage_driver")
        response = self.client.get(
            "/spots?near_lat=19.076&near_lon=72.8777&radius_km=20"
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"No parking listings match yet", response.data)
        self.assertIn(b"Search-area coverage preview", response.data)
        self.assertIn(b"not real parking spaces", response.data.lower())
        self.assertIn(b"20 km ring", response.data)
        self.assertEqual(response.data.count(b"DEMO AREA PIN"), 32)
        self.assertEqual(data_store.spots_db, [])
        self.assertEqual(data_store.bookings_db, [])

    def test_profile_update_notifications_and_account_deletion_require_password(self):
        self.register(self.client, "profile_driver")
        data_store.saved_spots.add(("profile_driver", 99))
        profile = self.client.get("/profile")
        updated = self.client.post("/profile", data={
            "csrf_token": self.csrf(profile), "full_name": "Updated Driver",
            "email": "new@example.com", "phone": "12345",
            "vehicle_type": "SUV", "vehicle_plate": "ab123",
        }, follow_redirects=True)
        self.assertIn(b"Your profile has been updated", updated.data)
        self.assertEqual(data_store.users_db["profile_driver"].vehicle_type, "SUV")
        data_store.add_notification("profile_driver", "A test update.")
        data_store.persist()
        notifications = self.client.get("/notifications")
        self.assertIn(b"A test update.", notifications.data)

        profile = self.client.get("/profile")
        denied = self.client.post("/account/delete", data={
            "csrf_token": self.csrf(profile), "current_password": "wrongpass1",
        }, follow_redirects=True)
        self.assertIn(b"current password", denied.data)
        profile = self.client.get("/profile")
        deleted = self.client.post("/account/delete", data={
            "csrf_token": self.csrf(profile), "current_password": "securepass1",
        }, follow_redirects=True)
        self.assertIn(b"Your account has been deleted", deleted.data)
        self.assertNotIn("profile_driver", data_store.users_db)
        self.assertEqual(data_store.saved_spots, set())

    def test_mark_all_notifications_read_only_updates_current_users_notifications(self):
        self.register(self.client, "notification_owner")
        other_client = self.app.test_client()
        self.register(other_client, "notification_other")
        data_store.add_notification("notification_owner", "First update.")
        data_store.add_notification("notification_owner", "Second update.")
        data_store.add_notification("notification_other", "Other user's update.")
        data_store.persist()

        page = self.client.get("/notifications")
        self.assertIn(b"Mark all read", page.data)
        response = self.client.post(
            "/notifications/read-all",
            data={"csrf_token": self.csrf(page)},
            follow_redirects=True,
        )

        self.assertIn(b"All notifications marked as read.", response.data)
        owner_items = [
            item for item in data_store.notifications
            if item["username"] == "notification_owner"
        ]
        other_items = [
            item for item in data_store.notifications
            if item["username"] == "notification_other"
        ]
        self.assertEqual(len(owner_items), 2)
        self.assertTrue(all(item["read"] for item in owner_items))
        self.assertEqual(len(other_items), 1)
        self.assertFalse(other_items[0]["read"])
        self.assertNotIn(b"Mark all read", response.data)

    def test_password_change_checks_current_password_and_invalidates_reset_tokens(self):
        self.register(self.client, "password_change_driver")
        data_store.password_reset_tokens["old-token-hash"] = (
            "password_change_driver", datetime.now() + timedelta(minutes=10)
        )
        page = self.client.get("/profile")
        denied = self.client.post("/profile/password", data={
            "csrf_token": self.csrf(page),
            "current_password": "wrongpass1",
            "new_password": "newsecure2",
            "confirm_password": "newsecure2",
        }, follow_redirects=True)
        self.assertIn(b"Enter your current password", denied.data)
        self.assertTrue(auth_module.authenticate("password_change_driver", "securepass1"))

        page = self.client.get("/profile")
        changed = self.client.post("/profile/password", data={
            "csrf_token": self.csrf(page),
            "current_password": "securepass1",
            "new_password": "newsecure2",
            "confirm_password": "newsecure2",
        }, follow_redirects=True)
        self.assertIn(b"Your password has been changed.", changed.data)
        self.assertIsNotNone(auth_module.authenticate("password_change_driver", "newsecure2"))
        self.assertIsNone(auth_module.authenticate("password_change_driver", "securepass1"))
        self.assertNotIn("old-token-hash", data_store.password_reset_tokens)

    def test_booking_calendar_download_is_private_to_booking_owner(self):
        host = self.app.test_client()
        self.register(host, "calendar_download_host", "Host")
        page = host.get("/host/spots")
        host.post("/host/spots", data={
            "csrf_token": self.csrf(page), "location": "Calendar garage, level 2",
            "price_per_hour": "45", "total_slots": "1",
            "latitude": "19.0760", "longitude": "72.8777",
            "vehicle_types": ["Car"],
        })
        spot = data_store.spots_db[0]
        self.register(self.client, "calendar_download_driver")
        search = self.client.get("/spots")
        response = self.client.post("/bookings/create", data={
            "csrf_token": self.csrf(search), "spot_id": str(spot.spot_id),
            "hours": "2",
        })
        booking = data_store.bookings_db[0]
        calendar = self.client.get(
            "/bookings/" + str(booking.booking_id) + "/calendar.ics"
        )
        self.assertEqual(calendar.status_code, 200)
        self.assertIn(b"BEGIN:VCALENDAR", calendar.data)
        self.assertIn(b"SUMMARY:ParkShare parking - Calendar garage\\, level 2", calendar.data)
        self.assertIn(
            "attachment; filename=parkshare-booking-",
            calendar.headers["Content-Disposition"],
        )

        other = self.app.test_client()
        self.register(other, "calendar_download_other")
        self.assertEqual(
            other.get("/bookings/" + str(booking.booking_id) + "/calendar.ics").status_code,
            404,
        )

    def test_spot_search_supports_minimum_price_filter(self):
        host = self.app.test_client()
        self.register(host, "price_filter_host", "Host")
        page = host.get("/host/spots")
        for location, price in (("Budget parking", "20"), ("Premium parking", "80")):
            host.post("/host/spots", data={
                "csrf_token": self.csrf(page), "location": location,
                "price_per_hour": price, "total_slots": "1",
                "latitude": "19.0760", "longitude": "72.8777",
                "vehicle_types": ["Car"],
            })
            page = host.get("/host/spots")
        response = self.client.get("/spots?min_price=50")
        self.assertIn(b"Premium parking", response.data)
        self.assertNotIn(b"Budget parking", response.data)
        self.assertIn(b'value="50"', response.data)

    def test_spot_search_matches_listing_descriptions(self):
        host = self.app.test_client()
        self.register(host, "description_search_host", "Host")
        page = host.get("/host/spots")
        host.post("/host/spots", data={
            "csrf_token": self.csrf(page), "location": "Central parking",
            "description": "Covered space with an EV charger",
            "price_per_hour": "45", "total_slots": "1",
            "latitude": "19.0760", "longitude": "72.8777",
            "vehicle_types": ["Car"],
        })
        response = self.client.get("/spots?location=EV+charger")
        self.assertIn(b"Central parking", response.data)
        self.assertIn(b"Covered space with an EV charger", response.data)

    def test_spot_search_can_filter_by_minimum_rating(self):
        high_rated = ParkingSpot(
            1, "Highly rated", 45, "host", 1,
            latitude=19.076, longitude=72.8777,
        )
        high_rated.add_rating(5)
        low_rated = ParkingSpot(
            2, "Lower rated", 40, "host", 1,
            latitude=19.076, longitude=72.8777,
        )
        low_rated.add_rating(3)
        data_store.spots_db.extend([high_rated, low_rated])
        response = self.client.get("/spots?minimum_rating=4")
        self.assertIn(b"Highly rated", response.data)
        self.assertNotIn(b"Lower rated", response.data)
        self.assertIn(b">4+ stars</option>", response.data)

    def test_invalid_spot_sort_falls_back_to_recommended(self):
        response = self.client.get("/spots?sort=unknown")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'<option value="recommended" selected>', response.data)
        self.assertNotIn(b'<option value="unknown" selected>', response.data)
        self.assertIn(b"Clear filters", response.data)

    def test_password_reset_uses_one_time_token(self):
        self.register(self.client, "reset_driver")
        captured = []
        self.app.config["PASSWORD_RESET_SENDER"] = lambda email, reset_url: captured.append(reset_url)
        page = self.client.get("/forgot-password")
        response = self.client.post("/forgot-password", data={
            "csrf_token": self.csrf(page), "email": "reset_driver@example.com",
        }, follow_redirects=True)
        self.assertIn(b"If an account uses that email", response.data)
        self.assertEqual(len(captured), 1)
        token = captured[0].rsplit("/", 1)[1]
        reset_page = self.client.get("/reset-password/" + token)
        response = self.client.post("/reset-password/" + token, data={
            "csrf_token": self.csrf(reset_page),
            "password": "newsecure2", "confirm_password": "newsecure2",
        }, follow_redirects=True)
        self.assertIn(b"password has been updated", response.data)
        self.assertIsNotNone(auth_module.authenticate("reset_driver", "newsecure2"))
        self.assertEqual(self.client.get("/reset-password/" + token).status_code, 302)

    def test_listing_photos_reject_non_image_uploads(self):
        host = self.app.test_client()
        self.register(host, "photo_host", "Host")
        page = host.get("/host/spots")
        response = host.post("/host/spots", data={
            "csrf_token": self.csrf(page), "location": "Photo listing",
            "price_per_hour": "50", "total_slots": "1",
            "latitude": "19.0760", "longitude": "72.8777",
            "vehicle_types": ["Car"],
            "photo": (BytesIO(b"not an image"), "fake.jpg"),
        }, content_type="multipart/form-data", follow_redirects=True)
        self.assertIn(b"Upload a valid JPG, PNG, or WebP image", response.data)
        self.assertEqual(data_store.spots_db, [])

    def test_listing_photo_upload_is_saved_and_rendered(self):
        host = self.app.test_client()
        self.register(host, "valid_photo_host", "Host")
        page = host.get("/host/spots")
        response = host.post("/host/spots", data={
            "csrf_token": self.csrf(page), "location": "Image garage",
            "price_per_hour": "50", "total_slots": "1",
            "latitude": "19.0760", "longitude": "72.8777",
            "vehicle_types": ["Car"],
            "photo": (BytesIO(self.png_pixel()), "garage.png"),
        }, content_type="multipart/form-data", follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        spot = data_store.spots_db[0]
        self.assertTrue(spot.photo.startswith("uploads/"))
        self.assertTrue(os.path.isfile(os.path.join(self.temp_dir.name, spot.photo)))
        self.assertIn(b"Image garage", response.data)

    def test_host_can_remove_a_listing_photo(self):
        host = self.app.test_client()
        self.register(host, "remove_photo_host", "Host")
        page = host.get("/host/spots")
        host.post("/host/spots", data={
            "csrf_token": self.csrf(page), "location": "Photo garage",
            "price_per_hour": "50", "total_slots": "1",
            "latitude": "19.0760", "longitude": "72.8777",
            "vehicle_types": ["Car"],
            "photo": (BytesIO(self.png_pixel()), "garage.png"),
        }, content_type="multipart/form-data")
        spot = data_store.spots_db[0]
        photo_path = os.path.join(self.temp_dir.name, spot.photo)
        self.assertTrue(os.path.isfile(photo_path))
        page = host.get("/host/spots")
        response = host.post(
            "/host/spots/" + str(spot.spot_id) + "/edit",
            data={
                "csrf_token": self.csrf(page), "location": spot.location,
                "price_per_hour": "50", "latitude": "19.0760",
                "longitude": "72.8777", "vehicle_types": ["Car"],
                "remove_photo": "on",
            },
            follow_redirects=True,
        )
        self.assertIn(b"Listing details updated", response.data)
        self.assertEqual(spot.photo, "")
        self.assertFalse(os.path.exists(photo_path))


if __name__ == "__main__":
    unittest.main()
