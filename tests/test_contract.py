import ast
from pathlib import Path


def test_python_parses():
    for path in Path('app').glob('*.py'):
        ast.parse(path.read_text())


def test_review_is_not_automatically_approved():
    worker = Path('app/worker.py').read_text()
    assert "'awaiting_review'" in worker
    assert "'published'" not in worker


def test_api_authentication_roles_and_openapi(monkeypatch):
    from fastapi.testclient import TestClient

    from app import auth
    from app.api import app, db_session
    from app.auth import Actor, get_current_actor
    from app.config import Settings

    monkeypatch.setattr(
        auth,
        "get_settings",
        lambda: Settings(forge_openwebui_token="valid-test-token"),
    )

    def unused_session():
        yield None

    monkeypatch.setitem(app.dependency_overrides, db_session, unused_session)
    with TestClient(app) as client:
        assert client.get("/health/live").status_code == 200
        assert client.get("/v1/models").status_code == 401
        assert client.get(
            "/v1/models",
            headers={"Authorization": "Bearer invalid-test-token"},
        ).status_code == 401
        assert client.get(
            "/v1/models",
            headers={"Authorization": "Bearer valid-test-token"},
        ).status_code == 200

        schema = app.openapi()
        assert schema["paths"]["/v1/jobs"]["post"]["operationId"] == "submitVideoJob"
        assert "/v1/projects/{project_id}/workflows" in schema["paths"]
        assert "/v1/projects/{project_id}/artifacts/{artifact_id}" in schema["paths"]

        monkeypatch.setitem(
            app.dependency_overrides,
            get_current_actor,
            lambda: Actor(name="viewer", roles=["viewer"]),
        )
        response = client.post(
            "/v1/jobs",
            json={"operation": "render_test"},
        )
        assert response.status_code == 403


def test_multipart_upload_parsing():
    from fastapi import FastAPI, File, UploadFile
    from fastapi.testclient import TestClient

    app = FastAPI()

    @app.post("/upload")
    async def upload(file: UploadFile = File()):
        return {"filename": file.filename, "content": (await file.read()).decode()}

    with TestClient(app) as client:
        response = client.post(
            "/upload",
            files={"file": ("sample.txt", b"upload-ok", "text/plain")},
        )

    assert response.status_code == 200
    assert response.json() == {"filename": "sample.txt", "content": "upload-ok"}
