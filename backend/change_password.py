"""
Password change utility — run from the backend folder:

    python change_password.py

You will be prompted to pick an account and enter a new password.
No server restart needed — changes take effect immediately.
"""
import getpass
import sys
import os

# Load .env so DATABASE_URL is available
from dotenv import load_dotenv
load_dotenv()

from database import SessionLocal
from models import User
from auth import get_password_hash

# ── Accounts you can manage ───────────────────────────────────────────────────
MANAGED_ACCOUNTS = {
    "1": ("admin",        "Admin panel"),
    "2": ("kitchen_staff","Kitchen display"),
    "3": ("cashier1",     "Cashier / verify screen"),
}

def main():
    print("\n=== Password Change Utility ===\n")
    print("Which account do you want to update?\n")
    for key, (username, label) in MANAGED_ACCOUNTS.items():
        print(f"  {key}. {username:<20} ({label})")
    print("  0. Enter a custom username")
    print()

    choice = input("Enter number: ").strip()

    if choice == "0":
        username = input("Username: ").strip()
    elif choice in MANAGED_ACCOUNTS:
        username = MANAGED_ACCOUNTS[choice][0]
    else:
        print("Invalid choice.")
        sys.exit(1)

    # Prompt for new password (hidden input, confirmed twice)
    while True:
        new_pass = getpass.getpass(f"New password for '{username}': ")
        if len(new_pass) < 8:
            print("  Password must be at least 8 characters. Try again.")
            continue
        confirm = getpass.getpass("Confirm new password: ")
        if new_pass != confirm:
            print("  Passwords do not match. Try again.")
            continue
        break

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == username).first()
        if not user:
            print(f"\nERROR: User '{username}' not found in the database.")
            sys.exit(1)

        user.password_hash = get_password_hash(new_pass)
        db.commit()
        print(f"\nPassword for '{username}' ({user.role.value}) updated successfully.")
        print("The change takes effect immediately — no restart needed.\n")
    except Exception as e:
        db.rollback()
        print(f"\nERROR: {e}")
        sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()
