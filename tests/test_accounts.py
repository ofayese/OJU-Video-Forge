import sys

import pytest

from app import accounts


def test_bootstrap_hides_password_validation_details(monkeypatch, capsys):
    password = "x" * 129

    class EmptyDatabase:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def scalar(self, query):
            return None

    monkeypatch.setattr(accounts, "SessionLocal", EmptyDatabase)
    monkeypatch.setattr(accounts.getpass, "getpass", lambda prompt: password)
    monkeypatch.setattr(sys, "argv", ["forge-account", "bootstrap", "admin"])

    with pytest.raises(SystemExit) as failure:
        accounts.main()

    assert str(failure.value) == "Invalid account credentials"
    assert password not in str(failure.value)
    captured = capsys.readouterr()
    assert password not in captured.out
    assert password not in captured.err
