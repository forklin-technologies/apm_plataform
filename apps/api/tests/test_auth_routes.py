"""A5: every route declares who may call it, and a test that walks all of them proves it. Plus the
OpenAPI document: valid, with the problem schema, the permission of each route and no password."""

import json
import re
from collections.abc import Callable
from typing import Any

import pytest
from fastapi import Depends, FastAPI
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute, RouteContext, iter_route_contexts
from openapi_spec_validator import validate
from starlette.requests import Request
from starlette.responses import PlainTextResponse

from app.auth.csrf import SAFE_METHODS
from app.auth.deps import authenticated, public, require
from app.auth.permissions import (
    PUBLIC_FRAMEWORK_PATHS,
    PUBLIC_ROUTES,
    AuthenticatedMarker,
    Permission,
    PermissionRequirement,
    PublicMarker,
)
from app.core.middleware import ORIGIN_EXEMPT_PATHS
from app.main import create_app
from tests.authsupport import Api, ApiFactory
from tests.helpers import make_settings

OPENAPI = "/api/openapi.json"


def markers_of(dependant: Dependant) -> list[Any]:
    """Every permission marker the route depends on, at any depth."""
    found: list[Any] = []
    for dependency in dependant.dependencies:
        if isinstance(dependency.call, PermissionRequirement | AuthenticatedMarker | PublicMarker):
            found.append(dependency.call)
        found.extend(markers_of(dependency))
    return found


def effective_routes(app: FastAPI) -> list[RouteContext]:
    """Every route the application serves, with its real path (routers included under prefixes)."""
    return list(iter_route_contexts(app.router.routes))


def scan_routes(app: FastAPI) -> list[str]:
    """What is wrong with the way the routes of `app` declare their access (empty = nothing)."""
    problems: list[str] = []
    seen_public: set[tuple[str, str]] = set()
    for context in effective_routes(app):
        path = context.path or ""
        if not isinstance(context.original_route, APIRoute):
            if path not in PUBLIC_FRAMEWORK_PATHS:
                problems.append(f"unknown framework route {path}")
            continue
        for method in sorted((context.methods or set()) - {"HEAD", "OPTIONS"}):
            where = f"{method} {path}"
            markers = markers_of(context.dependant)
            if len(markers) != 1:
                problems.append(f"{where}: {len(markers)} markers (needs exactly one)")
                continue
            marker = markers[0]
            listed = (method, path) in PUBLIC_ROUTES
            if isinstance(marker, PublicMarker):
                seen_public.add((method, path))
                if not listed:
                    problems.append(f"{where}: public but not in PUBLIC_ROUTES")
            elif listed:
                problems.append(f"{where}: in PUBLIC_ROUTES but needs a session")
            if isinstance(marker, PermissionRequirement):
                declared = (context.openapi_extra or {}).get("x-permission")
                if declared != marker.permission.value:
                    problems.append(f"{where}: x-permission {declared!r} != {marker.permission}")
    problems.extend(
        f"stale PUBLIC_ROUTES entry {entry}" for entry in sorted(PUBLIC_ROUTES.keys() - seen_public)
    )
    return problems


def real_app() -> FastAPI:
    return create_app(make_settings(env="development"), verify_posture=False)


def test_every_route_of_the_application_declares_exactly_one_access_marker() -> None:
    assert scan_routes(real_app()) == []


def test_the_walk_actually_sees_the_routes() -> None:
    app = real_app()
    paths = {(m, c.path) for c in effective_routes(app) for m in (c.methods or set())}

    assert ("POST", "/api/v1/auth/login") in paths
    assert ("POST", "/api/v1/invitations") in paths
    assert ("GET", "/api/health") in paths
    assert len(paths) >= 9


# The scan must FAIL when a route forgets to say who may call it (or says it twice, or is public
# without being listed): these are routes a developer might add by mistake.


def _unmarked(app: FastAPI) -> None:
    app.add_api_route("/api/v1/oops", lambda: {}, methods=["GET"])


def _two_markers(app: FastAPI) -> None:
    app.add_api_route(
        "/api/v1/both",
        lambda: {},
        methods=["GET"],
        dependencies=[Depends(public()), Depends(authenticated())],
    )


def _public_but_unlisted(app: FastAPI) -> None:
    app.add_api_route("/api/v1/open", lambda: {}, methods=["GET"], dependencies=[Depends(public())])


def _wrong_x_permission(app: FastAPI) -> None:
    app.add_api_route(
        "/api/v1/mislabelled",
        lambda: {},
        methods=["POST"],
        dependencies=[Depends(require(Permission.MONTHS_CLOSE))],
        openapi_extra={"x-permission": Permission.MONTHS_REOPEN.value},
    )


async def _internal(_request: Request) -> PlainTextResponse:
    return PlainTextResponse("internal")


def _secret_framework_route(app: FastAPI) -> None:
    app.add_route("/api/internal", _internal)


@pytest.mark.parametrize(
    ("add", "expected"),
    [
        (_unmarked, "GET /api/v1/oops: 0 markers"),
        (_two_markers, "GET /api/v1/both: 2 markers"),
        (_public_but_unlisted, "GET /api/v1/open: public but not in PUBLIC_ROUTES"),
        (_wrong_x_permission, "POST /api/v1/mislabelled: x-permission"),
        (_secret_framework_route, "unknown framework route /api/internal"),
    ],
    ids=["unmarked", "two-markers", "public-unlisted", "wrong-x-permission", "framework-route"],
)
def test_the_walk_fails_for_a_route_that_does_not_declare_its_access(
    add: Callable[[FastAPI], None], expected: str
) -> None:
    app = real_app()
    add(app)

    problems = scan_routes(app)

    assert any(problem.startswith(expected) for problem in problems), problems


def test_the_walk_fails_for_a_stale_entry_in_the_public_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(PUBLIC_ROUTES, ("GET", "/api/v1/gone"), "was removed")

    assert scan_routes(real_app()) == ["stale PUBLIC_ROUTES entry ('GET', '/api/v1/gone')"]


def test_the_walk_fails_for_a_route_marked_authenticated_that_is_listed_as_public(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(PUBLIC_ROUTES, ("GET", "/api/v1/auth/me"), "by mistake")

    problems = scan_routes(real_app())

    assert "GET /api/v1/auth/me: in PUBLIC_ROUTES but needs a session" in problems


def test_every_route_that_is_not_public_answers_401_without_a_session(api: Api) -> None:
    checked = 0
    for context in effective_routes(api.client.app):  # type: ignore[arg-type]
        if not isinstance(context.original_route, APIRoute):
            continue
        for method in sorted((context.methods or set()) - {"HEAD", "OPTIONS"}):
            if (method, context.path) in PUBLIC_ROUTES:
                continue
            response = api.client.request(
                method, context.path or "", json={}, headers={"Origin": api.origin}
            )
            assert response.status_code == 401, f"{method} {context.path}: {response.status_code}"
            assert response.json()["code"] == "unauthenticated"
            checked += 1
    assert checked >= 4


# The Origin check is global middleware, so it covers a route nobody thought of. The test derives
# the routes from the walk instead of from a hand-written list, so a new one is covered by itself.
FOREIGN_ORIGINS: list[dict[str, str]] = [
    {},
    {"Origin": "null"},
    {"Origin": "https://evil.example"},
]


def state_changing_routes(app: FastAPI) -> list[tuple[str, str]]:
    """(method, path) of every route that changes state, with path parameters filled in (the
    Origin check answers before the path is matched, so any value will do)."""
    return [
        (method, re.sub(r"\{[^}]+\}", "x", context.path or ""))
        for context in effective_routes(app)
        if isinstance(context.original_route, APIRoute)
        for method in sorted((context.methods or set()) - SAFE_METHODS)
        if (context.path or "") not in ORIGIN_EXEMPT_PATHS  # the webhooks: a server calls them
    ]


def assert_origin_is_required(api: Api, routes: list[tuple[str, str]]) -> None:
    for method, path in routes:
        for headers in FOREIGN_ORIGINS:
            response = api.client.request(method, path, json={}, headers=headers)
            where = f"{method} {path} with {headers}"
            assert response.status_code == 403, where
            assert response.json()["code"] == "origin_not_allowed", where
            assert "set-cookie" not in response.headers, where


def test_every_route_that_changes_state_refuses_a_request_without_the_origin_of_the_site(
    api: Api,
) -> None:
    routes = state_changing_routes(api.client.app)  # type: ignore[arg-type]

    assert ("POST", "/api/v1/auth/login") in routes
    assert ("POST", "/api/v1/invitations/accept") in routes
    assert len(routes) >= 6
    assert not any(method in SAFE_METHODS for method, _path in routes)
    assert_origin_is_required(api, routes)


def test_a_route_added_later_is_covered_without_anybody_listing_it(apis: ApiFactory) -> None:
    def add(app: FastAPI) -> None:
        app.add_api_route(
            "/api/v1/later/{thing}",
            lambda thing: {},
            methods=["POST", "PUT", "PATCH", "DELETE"],
            dependencies=[Depends(public())],
        )

    api = apis.make(configure=add)

    routes = state_changing_routes(api.client.app)  # type: ignore[arg-type]

    for method in ("POST", "PUT", "PATCH", "DELETE"):
        assert (method, "/api/v1/later/x") in routes
    assert_origin_is_required(api, routes)


def test_the_public_routes_are_the_ones_listed_and_nothing_else_works_anonymously(
    api: Api,
) -> None:
    assert set(PUBLIC_ROUTES) == {
        ("GET", "/api/health"),
        ("GET", "/api/health/ready"),
        ("POST", "/api/v1/auth/login"),
        ("POST", "/api/v1/auth/logout"),
        ("POST", "/api/v1/invitations/accept"),
        # the public contribution flow, the webhooks of the Pix provider and the dev sandbox
        ("GET", "/api/v1/public/schools/{slug}"),
        ("POST", "/api/v1/public/schools/{slug}/contributions"),
        ("GET", "/api/v1/public/schools/{slug}/contributions/{token}/charge"),
        ("POST", "/api/v1/public/schools/{slug}/contributions/{token}/charges"),
        ("GET", "/api/v1/public/schools/{slug}/contributions/{token}/receipt"),
        ("POST", "/api/v1/webhooks/pix/sandbox"),
        ("POST", "/api/v1/webhooks/pix/bb"),
        ("POST", "/api/v1/dev/sandbox/pix/{txid}/pay"),
    }
    assert all(reason for reason in PUBLIC_ROUTES.values())  # each one says why


def test_only_the_webhooks_are_exempt_from_the_origin_check_and_they_need_their_secret(
    api: Api,
) -> None:
    assert {"/api/v1/webhooks/pix/sandbox", "/api/v1/webhooks/pix/bb"} == ORIGIN_EXEMPT_PATHS
    for path in sorted(ORIGIN_EXEMPT_PATHS):
        body = {"event_id": "e1", "txid": "a" * 32}
        response = api.client.post(path, json=body)  # no Origin, no secret
        assert response.status_code == 401, path
        assert response.json()["code"] == "webhook_unauthorized", path


# --- OpenAPI ------------------------------------------------------------------------------------


@pytest.fixture
def spec(api: Api) -> dict[str, Any]:
    document: dict[str, Any] = api.get(OPENAPI).json()
    return document


def test_the_openapi_document_is_valid(spec: dict[str, Any]) -> None:
    validate(spec)  # raises on an invalid OpenAPI 3.1 document

    assert spec["info"]["title"] == "APM Digital API"
    assert spec["openapi"].startswith("3.1")


def test_every_operation_has_a_unique_operation_id_and_documents_its_errors(
    spec: dict[str, Any],
) -> None:
    ids: list[str] = []
    for path, item in spec["paths"].items():
        for method, operation in item.items():
            if path.startswith("/api/health"):
                continue
            ids.append(operation["operationId"])
            if (method.upper(), path) not in PUBLIC_ROUTES:
                assert "401" in operation["responses"], (method, path)
            documented = (
                "429"
                if path.endswith(("login", "accept", "password"))
                else "401"
                if "401" in operation["responses"]
                else "403"
                if "403" in operation["responses"]
                else "404"  # a public route that only answers "not found" (ADR-018)
            )
            assert "application/problem+json" in operation["responses"][documented]["content"], (
                method,
                path,
            )
    assert len(ids) == len(set(ids)) and "auth_login" in ids and "invitations_create" in ids


def test_errors_are_documented_as_problem_json_and_there_is_no_framework_422(
    spec: dict[str, Any],
) -> None:
    schemas = spec["components"]["schemas"]

    assert "Problem" in schemas and "HTTPValidationError" not in schemas
    problem = spec["paths"]["/api/v1/auth/me"]["get"]["responses"]["401"]
    assert "application/problem+json" in problem["content"]
    assert spec["paths"]["/api/v1/auth/login"]["post"]["responses"]["429"]["content"]
    assert set(schemas["Problem"]["properties"]) >= {
        "type",
        "title",
        "status",
        "code",
        "request_id",
    }


def test_the_permission_of_each_protected_route_is_in_the_document(spec: dict[str, Any]) -> None:
    invite = spec["paths"]["/api/v1/invitations"]["post"]

    assert invite["x-permission"] == "invitations:create"
    assert {"sessionCookie", "csrfHeader"} <= set(spec["components"]["securitySchemes"])
    assert spec["components"]["securitySchemes"]["sessionCookie"]["in"] == "cookie"
    assert spec["components"]["securitySchemes"]["csrfHeader"]["name"] == "X-CSRF-Token"


def test_the_document_has_no_password_example_hash_or_token(api: Api, spec: dict[str, Any]) -> None:
    text_form = json.dumps(spec).lower()

    assert "argon2" not in text_form and "token_hash" not in text_form
    assert "password_hash" not in text_form
    assert api.settings.auth_secret.get_secret_value().lower() not in text_form
    assert "example" not in json.dumps(spec["components"]["schemas"]["LoginRequest"]).lower()


def test_the_requests_refuse_unknown_fields_in_the_schema(spec: dict[str, Any]) -> None:
    for name in ("LoginRequest", "ContextRequest", "PasswordRequest", "InvitationRequest"):
        assert spec["components"]["schemas"][name]["additionalProperties"] is False, name


def test_production_serves_no_interactive_documentation(apis: ApiFactory) -> None:
    app = create_app(make_settings(env="production"), verify_posture=False)

    paths = {context.path for context in effective_routes(app)}

    assert "/api/docs" not in paths and "/api/redoc" not in paths
    assert scan_routes(app) == []
