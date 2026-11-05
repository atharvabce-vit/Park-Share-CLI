import data_store
from models import User
from utils_module import ask_text, line


def register_user():
    line()
    print("Register New User")
    username = ask_text("Enter a new username: ")
    if not username:
        print("Username cannot be empty.")
        return
    if username in data_store.users_db:
        print("That username is already taken.")
        return
    password = ask_text("Create a password: ")
    if not password:
        print("Password cannot be empty.")
        return
    confirm = ask_text("Confirm your password: ")
    if confirm != password:
        print("Passwords do not match.")
        return
    role = ask_text("Enter role (Host or Driver): ").capitalize()
    if role not in ("Host", "Driver"):
        print("Role must be either Host or Driver.")
        return

    data_store.users_db[username] = User(username, password, role)
    print("Registration successful! Please log in to continue.")


def login():
    line()
    print("Login")
    username = ask_text("Enter username: ")
    password = ask_text("Enter password: ")
    user = data_store.users_db.get(username)
    if user is None or user.password != password:
        print("Invalid username or password.")
        return
    data_store.current_user = user
    print("Login successful! Welcome,", user.username, "(" + user.role + ")")


def logout():
    if data_store.current_user is not None:
        print("Goodbye,", data_store.current_user.username)
    data_store.current_user = None
