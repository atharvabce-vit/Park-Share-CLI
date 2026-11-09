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
from web_app import _demo_area_markers, _distance_km, create_app


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


if __name__ == "__main__":
    unittest.main()
