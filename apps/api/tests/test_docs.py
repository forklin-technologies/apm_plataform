import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.helpers import make_settings


def _client(env: str) -> TestClient:
    return TestClient(create_app(make_settings(env=env)))


@pytest.mark.parametrize("path", ["/api/docs", "/api/redoc"])
def test_docs_ui_is_available_in_development(path: str) -> None:
    assert _client("development").get(path).status_code == 200


@pytest.mark.parametrize("path", ["/api/docs", "/api/redoc"])
def test_docs_ui_is_disabled_in_production(path: str) -> None:
    assert _client("production").get(path).status_code == 404


@pytest.mark.parametrize("env", ["development", "production"])
def test_openapi_is_served_under_api_prefix(env: str) -> None:
    response = _client(env).get("/api/openapi.json")

    assert response.status_code == 200
    assert "/api/health/ready" in response.json()["paths"]
