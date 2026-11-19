from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import auth
from app.api import app
from app.auth import hash_session_token
from app.config import Settings
from app.core import (
    AuthSession,
    Base,
    Job,
    ProjectMembership,
    SecurityEvent,
    User,
    get_db,
)


@pytest.fixture
def api_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[TestClient, sessionmaker]]:
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    test_session = sessionmaker(engine, expire_on_commit=False)

    def override_db():
        with test_session() as db:
            yield db

    monkeypatch.setattr(auth, "get_settings", Settings)
    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as client:
        yield client, test_session
    app.dependency_overrides.clear()
    engine.dispose()


def register(client: TestClient, username: str) -> None:
    response = client.post(
        "/v1/auth/register",
        json={"username": username, "password": "a-long-test-password"},
    )
    assert response.status_code == 201


def login(client: TestClient, username: str) -> str:
    response = client.post(
        "/v1/auth/session",
        json={"username": username, "password": "a-long-test-password"},
    )
    assert response.status_code == 200
    return response.json()["access_token"]


def authorization(token: str) -> dict[str, str]:
    return {"Authorization": "Bear" + "er " + token}


def test_local_session_authentication_hashes_secrets_and_revokes(
    api_client: tuple[TestClient, sessionmaker],
) -> None:
    client, test_session = api_client
    assert client.get("/v1/projects").status_code == 401
    assert client.post(
        "/v1/auth/session",
        json={"username": "nobody", "password": "a-long-test-password"},
    ).status_code == 401

    register(client, "Alice")
    invalid = client.post(
        "/v1/auth/session",
        json={"username": "ALICE", "password": "incorrect-password"},
    )
    assert invalid.status_code == 401
    token = login(client, "ALICE")
    headers = authorization(token)

    assert client.get("/v1/auth/session", headers=headers).json()["username"] == "alice"
    with test_session() as db:
        user = db.scalar(select(User).where(User.username == "alice"))
        stored_session = db.scalar(
            select(AuthSession).where(
                AuthSession.token_hash == hash_session_token(token)
            )
        )
        assert user.password_hash != "a-long-test-password"
        assert user.password_hash.startswith("scrypt$")
        assert stored_session.token_hash == hash_session_token(token)
        assert stored_session.token_hash != token

    expired_token = login(client, "alice")
    with test_session() as db:
        expired_session = db.scalar(
            select(AuthSession).where(
                AuthSession.token_hash == hash_session_token(expired_token)
            )
        )
        expired_session.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.commit()
    assert client.get(
        "/v1/auth/session",
        headers=authorization(expired_token),
    ).status_code == 401

    assert client.delete("/v1/auth/session", headers=headers).status_code == 204
    assert client.get("/v1/projects", headers=headers).status_code == 401
    with test_session() as db:
        actions = set(db.scalars(select(SecurityEvent.action)))
        assert {"account_registered", "login_succeeded", "login_failed", "logout"} <= actions


def test_disablement_revokes_sessions_and_blocks_login(api_client):
    client, sessions = api_client
    register(client, "disabled-user")
    token = login(client, "disabled-user")
    with sessions() as db:
        user = db.scalar(select(User).where(User.username == "disabled-user"))
        auth.set_account_disabled(db, user, True)
    assert client.get("/v1/auth/session", headers=authorization(token)).status_code == 401
    assert client.post("/v1/auth/session", json={
        "username": "disabled-user", "password": "a-long-test-password",
    }).status_code == 401


def test_local_user_cannot_read_service_owned_job(
    api_client: tuple[TestClient, sessionmaker],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, test_session = api_client
    monkeypatch.setattr(
        auth,
        "get_settings",
        lambda: Settings(forge_openwebui_token="service-token"),
    )
    register(client, "openwebui")
    user_headers = authorization(login(client, "openwebui"))
    job_id = "service-owned-job"
    with test_session() as db:
        db.add(
            Job(
                id=job_id,
                owner="openwebui",
                operation="wan22_t2v",
                status="failed",
                spec={},
                artifact="/private/artifact.mp4",
                error="private service error",
            )
        )
        db.commit()

    assert client.get(f"/v1/jobs/{job_id}", headers=user_headers).status_code == 404
    assert client.get(
        f"/v1/jobs/{job_id}/reviews", headers=user_headers
    ).status_code == 404
    service_response = client.get(
        f"/v1/jobs/{job_id}",
        headers=authorization("service-token"),
    )
    assert service_response.status_code == 200
    assert service_response.json()["status"] == "failed"
    assert service_response.json()["error"] == "private service error"
    assert service_response.json()["artifact"] is None


def test_service_token_cannot_access_local_user_project(
    api_client: tuple[TestClient, sessionmaker],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _ = api_client
    monkeypatch.setattr(
        auth,
        "get_settings",
        lambda: Settings(forge_openwebui_token="service-token"),
    )
    register(client, "openwebui")
    user_headers = authorization(login(client, "openwebui"))
    service_headers = authorization("service-token")
    project = client.post(
        "/v1/projects",
        headers=user_headers,
        json={"name": "Private local project"},
    ).json()

    assert client.get(
        f"/v1/projects/{project['id']}",
        headers=user_headers,
    ).status_code == 200
    assert client.get(
        f"/v1/projects/{project['id']}",
        headers=service_headers,
    ).status_code == 404
    assert client.get("/v1/projects", headers=service_headers).json() == []
    assert client.post(
        "/v1/jobs",
        headers=service_headers,
        json={"operation": "render_test", "project_id": project["id"]},
    ).status_code == 404


def test_project_membership_roles_and_isolation(
    api_client: tuple[TestClient, sessionmaker],
) -> None:
    client, _ = api_client
    register(client, "owner")
    register(client, "member")
    register(client, "outsider")
    owner_token = login(client, "owner")
    member_token = login(client, "member")
    outsider_token = login(client, "outsider")
    owner_headers = authorization(owner_token)
    member_headers = authorization(member_token)
    outsider_headers = authorization(outsider_token)

    created = client.post(
        "/v1/projects",
        headers=owner_headers,
        json={"name": "  Private project  "},
    )
    assert created.status_code == 201
    project = created.json()
    assert project["name"] == "Private project"
    assert project["role"] == "owner"
    project_id = project["id"]

    assert len(client.get("/v1/projects", headers=owner_headers).json()) == 1
    assert client.get("/v1/projects", headers=outsider_headers).json() == []
    assert client.put(
        f"/v1/projects/{project_id}/members/owner",
        headers=owner_headers,
        json={"role": "viewer"},
    ).status_code == 409
    assert client.delete(
        f"/v1/projects/{project_id}/members/owner",
        headers=owner_headers,
    ).status_code == 409
    assert client.get(
        f"/v1/projects/{project_id}",
        headers=outsider_headers,
    ).status_code == 404
    assert client.patch(
        f"/v1/projects/{project_id}",
        headers=outsider_headers,
        json={"name": "stolen"},
    ).status_code == 404

    member_path = f"/v1/projects/{project_id}/members/member"
    assert client.put(
        member_path,
        headers=owner_headers,
        json={"role": "viewer"},
    ).status_code == 200
    assert client.get(
        f"/v1/projects/{project_id}",
        headers=member_headers,
    ).json()["role"] == "viewer"
    assert client.patch(
        f"/v1/projects/{project_id}",
        headers=member_headers,
        json={"name": "unauthorized edit"},
    ).status_code == 403
    assert client.put(
        f"/v1/projects/{project_id}/members/outsider",
        headers=member_headers,
        json={"role": "editor"},
    ).status_code == 403

    assert client.put(
        member_path,
        headers=owner_headers,
        json={"role": "editor"},
    ).status_code == 200
    updated = client.patch(
        f"/v1/projects/{project_id}",
        headers=member_headers,
        json={"name": "Edited by member"},
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "Edited by member"


@pytest.mark.parametrize(
    ("concurrent_role", "expected_status"),
    [("viewer", 200), ("owner", 409)],
)
def test_project_member_creation_retries_unique_conflict(
    api_client: tuple[TestClient, sessionmaker],
    monkeypatch: pytest.MonkeyPatch,
    concurrent_role: str,
    expected_status: int,
) -> None:
    client, test_session = api_client
    register(client, "owner")
    register(client, "member")
    owner_headers = authorization(login(client, "owner"))
    created = client.post(
        "/v1/projects",
        headers=owner_headers,
        json={"name": "Race project"},
    )
    project_id = created.json()["id"]
    with test_session() as db:
        member = db.scalar(select(User).where(User.username == "member"))

    original_commit = Session.commit
    race_created = False

    def commit_with_concurrent_membership(db: Session) -> None:
        nonlocal race_created
        pending = next(
            (item for item in db.new if isinstance(item, ProjectMembership)),
            None,
        )
        if pending is not None and not race_created:
            race_created = True
            db.rollback()
            with test_session() as concurrent_db:
                concurrent_db.add(
                    ProjectMembership(
                        project_id=project_id,
                        user_id=member.id,
                        role=concurrent_role,
                    )
                )
                original_commit(concurrent_db)
            db.add(pending)
        original_commit(db)

    monkeypatch.setattr(Session, "commit", commit_with_concurrent_membership)
    response = client.put(
        f"/v1/projects/{project_id}/members/member",
        headers=owner_headers,
        json={"role": "editor"},
    )

    assert response.status_code == expected_status
    with test_session() as db:
        membership = db.scalar(
            select(ProjectMembership).where(
                ProjectMembership.project_id == project_id,
                ProjectMembership.user_id == member.id,
            )
        )
        assert membership.role == (
            "editor" if concurrent_role == "viewer" else "owner"
        )


def test_password_hash_verification_rejects_malformed_hash() -> None:
    encoded = auth.hash_password("correct horse battery staple")
    assert auth.verify_password("correct horse battery staple", encoded)
    assert not auth.verify_password("wrong password", encoded)
    assert not auth.verify_password("anything", "not-a-password-hash")
