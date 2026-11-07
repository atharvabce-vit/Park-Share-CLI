import re
import unittest

import data_store
from werkzeug.security import check_password_hash
from web_app import create_app


class ParkShareWebTests(unittest.TestCase):
    def setUp(self):
        data_store.users_db.clear()
        data_store.spots_db[:] = []
        data_store.bookings_db[:] = []
        data_store.activity_log[:] = []
        self.app = create_app({"TESTING": True, "SECRET_KEY": "test-key"})
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
        self.assertEqual(host_client.get("/host/earnings").status_code, 200)

    def test_driver_cannot_book_a_vehicle_incompatible_spot(self):
        self.register(self.client, "motorcyclist", vehicle_type="Motorcycle")
        host = self.app.test_client()
        self.register(host, "host_two", "Host")
        host_page = host.get("/host/spots")
        host.post("/host/spots", data={
            "csrf_token": self.csrf(host_page), "location": "Van-only garage",
            "price_per_hour": "50", "total_slots": "1", "vehicle_types": ["Van"],
        })
        browse = self.client.get("/spots")
        response = self.client.post("/bookings/create", data={
            "csrf_token": self.csrf(browse), "spot_id": str(data_store.spots_db[0].spot_id), "hours": "1",
        }, follow_redirects=True)
        self.assertIn(b"does not support your registered vehicle type", response.data)
        self.assertEqual(data_store.bookings_db, [])


if __name__ == "__main__":
    unittest.main()
