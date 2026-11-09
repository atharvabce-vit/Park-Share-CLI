import re

import data_store
from models import User
from utils_module import ask_text, line
from werkzeug.security import check_password_hash, generate_password_hash


def create_user(username, password, role, full_name="", email="", phone="",
                vehicle_type="", vehicle_plate=""):
    username = username.strip()
    full_name = full_name.strip()
    email = email.strip().lower()
    phone = phone.strip()
    vehicle_plate = vehicle_plate.strip().upper()

    if len(username) < 3 or len(username) > 32:
        raise ValueError("Username must be between 3 and 32 characters.")
    if not all(char.isalnum() or char in "_-" for char in username):
        raise ValueError("Username can contain only letters, numbers, underscores, and hyphens.")
    if username.lower() in (name.lower() for name in data_store.users_db):
        raise ValueError("That username is already taken.")
    if len(password) < 8 or not any(char.isdigit() for char in password):
        raise ValueError("Password must be at least 8 characters and include a number.")
    if role not in ("Host", "Driver"):
        raise ValueError("Choose Host or Driver as your account type.")
    if not full_name:
        raise ValueError("Enter your full name.")
    if len(full_name) > 80 or len(phone) > 30 or len(vehicle_plate) > 20:
        raise ValueError("Profile details are longer than the allowed limit.")
    if len(email) > 254:
        raise ValueError("Email address is too long.")
    if email and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        raise ValueError("Enter a valid email address.")
    if email and any(user.email.lower() == email for user in data_store.users_db.values()):
        raise ValueError("An account already uses that email address.")
    if role == "Driver":
        matching_type = next((kind for kind in ("Car", "Motorcycle", "SUV", "Van")
                              if kind.lower() == vehicle_type.lower()), None)
        if matching_type is None:
            raise ValueError("Choose a supported vehicle type.")
        vehicle_type = matching_type

    user = User(username, generate_password_hash(password), role, full_name, email, phone,
                vehicle_type, vehicle_plate)
    data_store.users_db[username] = user
    data_store.log_event(username + " registered as a " + role)
    return user


def suspend_user(username, reason):
    user = data_store.users_db.get(username)
    if user is None:
        raise ValueError("That user does not exist.")
    user.is_suspended = True
    user.suspended_reason = reason.strip()[:200] if reason else "Account suspended by an administrator."
    data_store.suspended_users.add(username)
    data_store.log_event(f"User {username} was suspended: {user.suspended_reason}")
    return user


def unsuspend_user(username):
    user = data_store.users_db.get(username)
    if user is None:
        raise ValueError("That user does not exist.")
    user.is_suspended = False
    user.suspended_reason = ""
    data_store.suspended_users.discard(username)
    data_store.log_event(f"User {username} was reinstated.")
    return user


def authenticate(username, password):
    username = (username or "").strip()
    if not username or not password:
        return None
    user = data_store.users_db.get(username)
    if user is None:
        user = next((candidate for name, candidate in data_store.users_db.items()
                     if name.lower() == username.lower()), None)
    if user is None:
        return None
    if user.is_suspended:
        return None
    if not check_password_hash(user.password, password):
        return None
    return user


def register_user():
    line()
    print("Register New User")
    username = ask_text("Enter a new username: ")
    password = ask_text("Create a password: ")
    confirm = ask_text("Confirm your password: ")
    if confirm != password:
        print("Passwords do not match.")
        return
    role = ask_text("Enter role (Host or Driver): ").capitalize()
    full_name = ask_text("Enter your full name: ")
    email = ask_text("Enter your email (optional): ")
    phone = ask_text("Enter your phone (optional): ")
    vehicle_type = ""
    vehicle_plate = ""
    if role == "Driver":
        vehicle_type = ask_text("Vehicle type (Car, Motorcycle, SUV, Van): ").capitalize()
        vehicle_plate = ask_text("Vehicle plate (optional): ")
    try:
        create_user(username, password, role, full_name, email, phone, vehicle_type, vehicle_plate)
    except ValueError as error:
        print(error)
        return
    print("Registration successful! Please log in to continue.")


def login():
    line()
    print("Login")
    username = ask_text("Enter username: ")
    password = ask_text("Enter password: ")
    user = authenticate(username, password)
    if user is None:
        print("Invalid username or password.")
        return
    data_store.current_user = user
    data_store.log_event(user.username + " logged in")
    print("Login successful! Welcome,", user.username, "(" + user.role + ")")


def logout():
    if data_store.current_user is not None:
        data_store.log_event(data_store.current_user.username + " logged out")
        print("Goodbye,", data_store.current_user.username)
    data_store.current_user = None
