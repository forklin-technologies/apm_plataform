"""A5: the per-request settings the database reads (audit: app.request_id, app.actor_type; sessions:
app.session_id, app.user_id) are transaction-local, set once per Session, and never reach the next
user of a pooled connection."""

import hashlib
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import Depends, FastAPI
from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from app.auth.deps import Db, authenticated, public
from app.core.config import AdminSettings
from app.db.request_context import (
    ACTOR_SETTING,
    REQUEST_SETTING,
    SESSION_SETTING,
    USER_SETTING,
    RequestContext,
    bind_request_context,
)
from app.db.session import build_engine, build_session_factory
from app.db.tenant import TenantContextConflict
from tests.authsupport import ApiFactory, UserFactory
from tests.helpers import make_settings

READ = text(
    f"SELECT current_setting('{SESSION_SETTING}', true), current_setting('{USER_SETTING}', true), "
    f"current_setting('{REQUEST_SETTING}', true), current_setting('{ACTOR_SETTING}', true)"
)
EMPTY = ("", "", "", "")
SESSION_ID = hashlib.sha256(b"a session").digest()


def read(session: Session) -> tuple[Any, ...]:
    return tuple(session.execute(READ).one())


def blank(values: tuple[Any, ...]) -> tuple[Any, ...]:
    """Postgres answers '' or NULL for a setting that was never set at session level."""
    return tuple(value or "" for value in values)


@pytest.fixture
def one_connection(admin_settings: AdminSettings) -> Iterator[Engine]:
    """A pool of exactly one connection, so the next session reuses the previous one's."""
    engine = create_engine(
        admin_settings.database_url.get_secret_value(), pool_size=1, max_overflow=0
    )
    yield engine
    engine.dispose()


# --- the four settings --------------------------------------------------------------------------


def test_the_four_settings_are_visible_inside_the_transaction(one_connection: Engine) -> None:
    user = uuid.uuid4()
    with sessionmaker(one_connection)() as session:
        bind_request_context(
            session,
            request_id="req-12345678",
            actor_type="USER",
            session_id=SESSION_ID,
            user_id=user,
        )

        assert read(session) == (SESSION_ID.hex(), str(user), "req-12345678", "USER")


def test_nothing_is_visible_before_anything_is_bound(one_connection: Engine) -> None:
    with sessionmaker(one_connection)() as session:
        assert blank(read(session)) == EMPTY


@pytest.mark.parametrize("end", ["commit", "rollback", "close"])
def test_the_settings_do_not_survive_the_transaction_on_a_reused_connection(
    one_connection: Engine, end: str
) -> None:
    """Transaction-local on its own, with no pool hook at all: the next session of the only
    connection starts from nothing."""
    with sessionmaker(one_connection)() as first:
        bind_request_context(
            first,
            request_id="req-12345678",
            actor_type="USER",
            session_id=SESSION_ID,
            user_id=uuid.uuid4(),
        )
        assert read(first)[2] == "req-12345678"
        if end != "close":
            getattr(first, end)()

    with sessionmaker(one_connection)() as second:
        assert blank(read(second)) == EMPTY


def test_a_session_keeps_its_settings_in_every_transaction_it_opens(
    one_connection: Engine,
) -> None:
    with sessionmaker(one_connection)() as session:
        bind_request_context(session, request_id="req-12345678", actor_type="PUBLIC")
        session.commit()  # the first transaction is over

        assert read(session)[2:] == ("req-12345678", "PUBLIC")  # the next one carries them again


def test_settings_can_be_added_later_but_never_changed(one_connection: Engine) -> None:
    user = uuid.uuid4()
    with sessionmaker(one_connection)() as session:
        bind_request_context(session, request_id="req-12345678")
        bind_request_context(session, actor_type="PUBLIC")
        bind_request_context(session, user_id=user)
        assert read(session) == ("", str(user), "req-12345678", "PUBLIC")

        bind_request_context(session, request_id="req-12345678")  # the same value: fine
        for field, other in (
            ("request_id", "req-87654321"),
            ("actor_type", "USER"),
            ("user_id", uuid.uuid4()),
            ("session_id", SESSION_ID),
        ):
            if field == "session_id":
                bind_request_context(session, session_id=SESSION_ID)
                other = hashlib.sha256(b"another").digest()
            with pytest.raises(TenantContextConflict):
                bind_request_context(session, **{field: other})
        assert read(session) == (SESSION_ID.hex(), str(user), "req-12345678", "PUBLIC")


def test_binding_inside_a_savepoint_is_refused(one_connection: Engine) -> None:
    with sessionmaker(one_connection)() as session:
        session.execute(text("SELECT 1"))
        with session.begin_nested(), pytest.raises(TenantContextConflict):
            bind_request_context(session, request_id="req-12345678")


@pytest.mark.parametrize(
    ("values", "error"),
    [
        ({"request_id": "short"}, ValueError),
        ({"request_id": "x" * 65}, ValueError),
        ({"request_id": "has space in it"}, ValueError),
        ({"request_id": "semi;colon-1234"}, ValueError),
        ({"request_id": "quote'quote-1234"}, ValueError),
        ({"actor_type": "ADMIN"}, ValueError),
        ({"actor_type": "user"}, ValueError),
        ({"session_id": b"short"}, ValueError),
        ({"session_id": "a" * 64}, ValueError),
        ({"user_id": "not-a-uuid"}, TypeError),
    ],
)
def test_a_value_that_is_not_what_the_database_expects_is_refused(
    values: dict[str, Any], error: type[Exception]
) -> None:
    with pytest.raises(error):
        RequestContext(**values)


def test_the_values_are_bound_never_written_into_the_statement(one_connection: Engine) -> None:
    seen: list[str] = []
    event.listen(
        one_connection,
        "before_cursor_execute",
        lambda _c, _cur, statement, _p, _ctx, _many: seen.append(statement),
    )
    with sessionmaker(one_connection)() as session:
        bind_request_context(session, request_id="req-12345678", actor_type="USER")
        session.execute(text("SELECT 1"))

    sets = [statement for statement in seen if "set_config" in statement]
    assert sets and all("req-12345678" not in s and "USER" not in s for s in sets)


# --- through the API ------------------------------------------------------------------------------


def _routes(app: FastAPI) -> None:
    def gucs(db: Session) -> dict[str, str]:
        values = read(db)
        return dict(zip(("session_id", "user_id", "request_id", "actor_type"), values, strict=True))

    @app.get("/api/test/gucs", dependencies=[Depends(authenticated())])
    def authenticated_route(db: Db) -> dict[str, str]:
        return gucs(db)

    @app.get("/api/test/gucs-public", dependencies=[Depends(public())])
    def public_route(db: Db) -> dict[str, str]:
        return gucs(db)


def test_an_authenticated_request_carries_the_session_the_user_the_request_and_the_actor(
    apis: ApiFactory, users: UserFactory
) -> None:
    api = apis.make(configure=_routes)
    user = users.make("staff")
    api.login(user)
    token = api.session_token
    assert token is not None

    response = api.get("/api/test/gucs")

    assert response.json() == {
        "session_id": hashlib.sha256(token.encode()).hexdigest(),
        "user_id": str(user.id),
        "request_id": response.headers["x-request-id"],
        "actor_type": "USER",
    }


def test_an_anonymous_request_carries_only_its_request_id(apis: ApiFactory) -> None:
    api = apis.make(configure=_routes)

    response = api.get("/api/test/gucs-public")

    assert response.json() == {
        "session_id": "",
        "user_id": "",
        "request_id": response.headers["x-request-id"],
        "actor_type": "",  # never defaulted: whoever audits must treat "" as "not declared"
    }


def test_every_request_gets_its_own_id_and_never_one_the_client_chose(apis: ApiFactory) -> None:
    api = apis.make(configure=_routes)

    first = api.get("/api/test/gucs-public", headers={"X-Request-ID": "chosen-by-attacker"})
    second = api.get("/api/test/gucs-public")

    assert first.headers["x-request-id"] != "chosen-by-attacker"
    assert first.json()["request_id"] != second.json()["request_id"]
    assert len(first.headers["x-request-id"]) == 32


def test_the_login_runs_as_the_public_actor_with_the_request_id(
    apis: ApiFactory, users: UserFactory
) -> None:
    api = apis.make()
    engine = api.client.app.state.session_factory.kw["bind"]  # type: ignore[attr-defined]
    bound: list[dict[str, Any]] = []

    def capture(_c: Any, _cur: Any, statement: str, parameters: Any, _ctx: Any, _m: Any) -> None:
        if "set_config" in statement:
            bound.append(dict(parameters) if not isinstance(parameters, dict) else parameters)

    event.listen(engine, "before_cursor_execute", capture)
    user = users.make("staff")

    response = api.login(user)

    assert response.status_code == 200
    assert bound, "the context was never applied"
    assert {entry["actor_type"] for entry in bound} == {"PUBLIC"}
    assert {entry["request_id"] for entry in bound} == {response.headers["x-request-id"]}
    assert any(entry["user_id"] == str(user.id) for entry in bound)  # after the password matched


def test_a_connection_reused_by_the_next_request_carries_nothing_over(
    apis: ApiFactory, users: UserFactory, admin_settings: AdminSettings
) -> None:
    """One pooled connection serves an authenticated request and then an anonymous one."""
    api = apis.make(configure=_routes)
    pool = build_engine(api.settings, pool_size=1, max_overflow=0)
    api.client.app.state.session_factory = build_session_factory(pool)  # type: ignore[attr-defined]
    try:
        api.login(users.make("staff"))
        assert api.get("/api/test/gucs").json()["user_id"] != ""
        other = apis.make(configure=_routes)
        other.client.app.state.session_factory = build_session_factory(pool)  # type: ignore[attr-defined]

        anonymous = other.get("/api/test/gucs-public").json()
    finally:
        pool.dispose()

    assert anonymous["user_id"] == "" and anonymous["session_id"] == ""
    assert anonymous["actor_type"] == ""


def test_the_engine_hides_statement_parameters_in_errors(admin_settings: AdminSettings) -> None:
    engine = build_engine(
        make_settings(database_url=admin_settings.database_url.get_secret_value())
    )
    try:
        assert engine.hide_parameters is True
    finally:
        engine.dispose()
