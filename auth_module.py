
import data_store

print("Authentication Module ")

print("[Register]")
reg_username = input("Enter a new username: ")
reg_role = input("Enter role (Host or Driver): ")

data_store.users_db[reg_username] = {
    "username": reg_username, 
    "role": reg_role
}
print("Registration successful!")

print("\n[Login]")
login_username = input("Enter username to login: ")

is_logged_in = False
current_user = {}

if login_username in data_store.users_db:
    print("Login successful! Welcome,", login_username)
    is_logged_in = True
    current_user = data_store.users_db[login_username]
else:
    print("User not found.")