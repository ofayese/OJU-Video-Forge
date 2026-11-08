"""Trusted local account administration; credentials never appear in arguments."""

import argparse
import getpass
import uuid

from pydantic import ValidationError
from sqlalchemy import select

from .auth import AccountInput, hash_password, set_account_disabled
from .core import SecurityEvent, SessionLocal, User


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["bootstrap", "disable", "enable"])
    parser.add_argument("username")
    args = parser.parse_args()
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.username == args.username.casefold()))
        if args.action == "bootstrap":
            if db.scalar(select(User.id).where(User.administrator.is_(True))):
                raise SystemExit("An administrator already exists")
            if user:
                raise SystemExit("Username already exists")
            password = getpass.getpass("Administrator password: ")
            if password != getpass.getpass("Confirm password: "):
                raise SystemExit("Passwords do not match")
            credentials = {"username": args.username}
            credentials["password"] = password
            try:
                AccountInput.model_validate(credentials)
            except ValidationError:
                raise SystemExit("Invalid account credentials") from None
            account = AccountInput(username=args.username, password=password)
            user = User(id=str(uuid.uuid4()), username=account.username,
                        password_hash=hash_password(account.password), administrator=True)
            db.add(user)
            db.flush()
            db.add(SecurityEvent(user_id=user.id, action="administrator_bootstrapped"))
            db.commit()
        else:
            if user is None:
                raise SystemExit("Account not found")
            set_account_disabled(db, user, args.action == "disable")
    print("Account operation completed")


if __name__ == "__main__":
    main()
