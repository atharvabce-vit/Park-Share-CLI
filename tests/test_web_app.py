import binascii
import re
import os
import struct
import tempfile
import unittest
from datetime import datetime, timedelta
from io import BytesIO
import zlib

import auth_module
import data_store
from werkzeug.security import check_password_hash
from web_app import create_app


class ParkShareWebTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        data_store.configure_storage(os.path.join(self.temp_dir.name, "test.sqlite3"))
        data_store.reset_data()
        self.app = create_app({
            "TESTING": True,
            "SECRET_KEY": "test-key",
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
            "/spots?near_lat=19.0760&near_lon=72.8777&radius_km=5"
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Central car parking", response.data)
        self.assertIn(b"Nearby car parking", response.data)
        self.assertNotIn(b"Motorcycle parking", response.data)
        self.assertNotIn(b"Far car parking", response.data)
        self.assertIn(b"km away", response.data)
        self.assertIn(b"match", response.data)
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
        self.assertIn(b"No parking spots have been listed yet", response.data)
        self.assertIn(b"Create a host account", response.data)

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
