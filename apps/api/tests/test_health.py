from fastapi.testclient import TestClient


def test_liveness_returns_ok(client: TestClient) -> None:
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness_returns_200_with_database_up(db_client: TestClient) -> None:
    response = db_client.get("/api/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_readiness_returns_503_with_database_down(db_down_client: TestClient) -> None:
    response = db_down_client.get("/api/health/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}


def test_liveness_stays_ok_with_database_down(db_down_client: TestClient) -> None:
    assert db_down_client.get("/api/health").status_code == 200


def test_health_is_only_served_under_api_prefix(client: TestClient) -> None:
    assert client.get("/health").status_code == 404
