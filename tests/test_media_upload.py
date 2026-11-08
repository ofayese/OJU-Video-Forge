import hashlib

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from app.api import MEDIA_TYPES, app, db_session
from app.auth import Actor, get_current_actor
from app.core import Base, MediaAsset


@pytest.fixture
def media_api(tmp_path, monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    test_sessions = sessionmaker(engine, expire_on_commit=False)

    def test_db_session():
        with test_sessions() as db:
            yield db

    actor_state = {
        "actor": Actor(name="owner-one", roles=["producer", "viewer"]),
        "sessions": test_sessions,
    }
    monkeypatch.setitem(app.dependency_overrides, db_session, test_db_session)
    monkeypatch.setitem(app.dependency_overrides, get_current_actor, lambda: actor_state["actor"])
    monkeypatch.setattr("app.api.ARTIFACT_ROOT", tmp_path / "private-media")
    # Transport/storage tests use arbitrary bytes; real media probing is exercised separately.
    monkeypatch.setattr("app.api.RenderEngine._tool", staticmethod(lambda value: value))
    monkeypatch.setattr("app.api.RenderEngine.inspect_media", lambda *_: {})
    with TestClient(app) as client:
        yield client, actor_state, tmp_path / "private-media"
    engine.dispose()


def create_project(client, name="Synthetic project"):
    response = client.post("/v1/projects", json={"name": name})
    assert response.status_code == 201
    return response.json()["id"]


def upload(client, project_id, filename="sample.mp4", data=b"synthetic media", content_type="video/mp4"):
    return client.post(
        f"/v1/projects/{project_id}/media",
        files={"file": (filename, data, content_type)},
    )


def test_artifact_openapi_declares_binary_media_types():
    response_content = app.openapi()["paths"][
        "/v1/projects/{project_id}/artifacts/{artifact_id}"
    ]["get"]["responses"]["200"]["content"]

    assert set(response_content) == set(MEDIA_TYPES.values()) | {
        "application/json", "application/x-subrip", "text/vtt"
    }
    assert all(
        media["schema"] == {"type": "string", "format": "binary"}
        for media in response_content.values()
    )


def test_upload_manifest_retrieval_and_checksum(media_api):
    client, state, root = media_api
    project_id = create_project(client)
    content = b"synthetic media bytes"

    uploaded = upload(client, project_id, data=content)

    assert uploaded.status_code == 201
    manifest = uploaded.json()
    assert manifest == {
        "id": manifest["id"],
        "project_id": project_id,
        "filename": "sample.mp4",
        "content_type": "video/mp4",
        "size_bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }
    with state["sessions"]() as db:
        media_asset = db.get(MediaAsset, manifest["id"])
        assert media_asset is not None
        assert media_asset.project_id == project_id
        assert media_asset.sha256 == manifest["sha256"]
    stored = list(root.rglob(f'{manifest["id"]}.mp4'))
    assert len(stored) == 1
    assert stored[0].read_bytes() == content
    assert stored[0].stat().st_mode & 0o777 == 0o600

    retrieved = client.get(f"/v1/projects/{project_id}/artifacts/{manifest['id']}")
    assert retrieved.status_code == 200
    assert retrieved.content == content
    assert retrieved.headers["x-content-sha256"] == manifest["sha256"]
    assert retrieved.headers["content-type"] == "video/mp4"

    stored[0].write_bytes(b"tampered")
    assert client.get(f"/v1/projects/{project_id}/artifacts/{manifest['id']}").status_code == 409


@pytest.mark.parametrize(("filename", "content_type", "status"), [
    ("../escape.mp4", "video/mp4", 400),
    ("folder/sample.mp4", "video/mp4", 400),
    ("sample.mp4", "text/plain", 415),
    ("sample.exe", "application/octet-stream", 415),
    ("sample..mp4", "video/mp4", 400),
])
def test_rejects_unsafe_names_and_invalid_media_types(media_api, filename, content_type, status):
    client, _, root = media_api
    project_id = create_project(client)

    response = upload(client, project_id, filename=filename, content_type=content_type)

    assert response.status_code == status
    assert not root.exists() or not list(root.rglob("*"))


def test_upload_size_limit_and_empty_file(media_api, monkeypatch):
    from app import api

    client, _, root = media_api
    project_id = create_project(client)
    monkeypatch.setattr(api, "MAX_UPLOAD_BYTES", 4)

    too_large = upload(client, project_id, data=b"12345")
    empty = upload(client, project_id, data=b"")

    assert too_large.status_code == 413
    assert empty.status_code == 400
    assert not root.exists() or not [path for path in root.rglob("*") if path.is_file()]


def test_same_filename_does_not_overwrite_and_projects_are_isolated(media_api):
    client, actor_state, _ = media_api
    first_project = create_project(client)
    first = upload(client, first_project, data=b"first version").json()
    second = upload(client, first_project, data=b"second version").json()

    assert first["id"] != second["id"]
    assert first["sha256"] != second["sha256"]
    assert client.get(
        f"/v1/projects/{first_project}/artifacts/{first['id']}"
    ).content == b"first version"
    assert client.get(
        f"/v1/projects/{first_project}/artifacts/{second['id']}"
    ).content == b"second version"

    actor_state["actor"] = Actor(name="owner-two", roles=["producer", "viewer"])
    second_project = create_project(client, "Other project")
    assert [project["id"] for project in client.get("/v1/projects").json()] == [second_project]

    assert upload(client, first_project).status_code == 404
    denied = client.get(f"/v1/projects/{first_project}/artifacts/{first['id']}")
    wrong_project = client.get(f"/v1/projects/{second_project}/artifacts/{first['id']}")
    assert denied.status_code == 404
    assert wrong_project.status_code == 404


def test_request_body_limit_rejects_oversized_multipart(media_api, monkeypatch):
    from app import api

    client, _, _ = media_api
    project_id = create_project(client)
    monkeypatch.setattr(api, "MAX_UPLOAD_BYTES", 1)
    monkeypatch.setattr(api, "MAX_MULTIPART_OVERHEAD_BYTES", 1)

    response = upload(client, project_id, data=b"more than the complete request limit")

    assert response.status_code == 413


def test_upload_replays_request_body_larger_than_spool_threshold(media_api):
    client, _, _ = media_api
    project_id = create_project(client)
    content = b"x" * (1024 * 1024 + 1)

    uploaded = upload(client, project_id, data=content)

    assert uploaded.status_code == 201
    artifact_id = uploaded.json()["id"]
    retrieved = client.get(
        f"/v1/projects/{project_id}/artifacts/{artifact_id}"
    )
    assert retrieved.status_code == 200
    assert retrieved.content == content
