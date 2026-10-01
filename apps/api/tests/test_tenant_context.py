"""T5: the tenant context is per transaction, comes only from an explicit argument, and never leaks
through the connection pool. Plus the ORM behaviour that depends on it."""

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, func, inspect, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import AdminSettings
from app.db.tenant import TenantContext, apply_tenant_context, bind_tenant, tenant_session
from app.models import Membership, School, User
from app.routers.deps import get_tenant_db
from tests.dbsupport import Tenants

SCHOOLS_IN_A = 2


def _school_count(session: Session, tenants: Tenants) -> int:
    return (
        session.scalar(
            select(func.count())
            .select_from(School)
            .where(School.id.in_([tenants.school_a1, tenants.school_a2, tenants.school_b1]))
        )
        or 0
    )


@pytest.fixture
def single_connection_engine(admin_settings: AdminSettings) -> Iterator[Engine]:
    """A pool of exactly one connection, so a second session is forced to reuse the first."""
    engine = create_engine(
        admin_settings.database_url.get_secret_value(), pool_size=1, max_overflow=0
    )
    yield engine
    engine.dispose()


def test_context_must_be_built_from_uuids() -> None:
    with pytest.raises(TypeError):
        TenantContext(organization_id="a-string")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        TenantContext(organization_id=uuid.uuid4(), school_id="x")  # type: ignore[arg-type]


def test_a_session_that_was_never_bound_sees_nothing(app_engine: Engine, tenants: Tenants) -> None:
    with sessionmaker(app_engine)() as session:
        assert _school_count(session, tenants) == 0


def test_tenant_session_sees_only_its_tenant(app_engine: Engine, tenants: Tenants) -> None:
    factory = sessionmaker(app_engine)

    with tenant_session(factory, TenantContext(tenants.org_a)) as session:
        assert _school_count(session, tenants) == SCHOOLS_IN_A
    with tenant_session(factory, TenantContext(tenants.org_a, tenants.school_a1)) as session:
        assert _school_count(session, tenants) == 1
    with tenant_session(factory, TenantContext(tenants.org_b)) as session:
        assert _school_count(session, tenants) == 1


def test_context_survives_a_commit_inside_the_session(app_engine: Engine, tenants: Tenants) -> None:
    """The listener re-applies the context at the start of each transaction of a bound session."""
    with tenant_session(sessionmaker(app_engine), TenantContext(tenants.org_a)) as session:
        assert _school_count(session, tenants) == SCHOOLS_IN_A
        session.commit()
        assert _school_count(session, tenants) == SCHOOLS_IN_A
        session.rollback()
        assert _school_count(session, tenants) == SCHOOLS_IN_A


def test_binding_a_session_that_is_already_in_a_transaction_applies_at_once(
    app_engine: Engine, tenants: Tenants
) -> None:
    with sessionmaker(app_engine)() as session:
        assert _school_count(session, tenants) == 0  # transaction open, no context
        bind_tenant(session, TenantContext(tenants.org_a))
        assert _school_count(session, tenants) == SCHOOLS_IN_A


def test_context_does_not_leak_to_the_next_user_of_the_same_pooled_connection(
    single_connection_engine: Engine, tenants: Tenants
) -> None:
    factory = sessionmaker(single_connection_engine)
    with tenant_session(factory, TenantContext(tenants.org_a, tenants.school_a1)) as first:
        first_pid: Any = first.execute(text("SELECT pg_backend_pid()")).scalar_one()
        assert _school_count(first, tenants) == 1

    with factory() as second:  # no context bound
        second_pid: Any = second.execute(text("SELECT pg_backend_pid()")).scalar_one()
        organization_setting: Any = second.execute(
            text("SELECT current_setting('app.organization_id', true)")
        ).scalar_one()
        school_setting: Any = second.execute(
            text("SELECT current_setting('app.school_id', true)")
        ).scalar_one()
        visible = _school_count(second, tenants)

    assert second_pid == first_pid, "the test needs the very same connection to prove anything"
    assert organization_setting in (None, "")
    assert school_setting in (None, "")
    assert visible == 0


def test_context_is_gone_after_an_error_and_a_rollback(
    single_connection_engine: Engine, tenants: Tenants
) -> None:
    factory = sessionmaker(single_connection_engine)
    with (
        pytest.raises(RuntimeError),
        tenant_session(factory, TenantContext(tenants.org_a)) as session,
    ):
        assert _school_count(session, tenants) == SCHOOLS_IN_A
        raise RuntimeError("boom")

    with factory() as after:
        assert _school_count(after, tenants) == 0


def test_the_setting_is_transaction_local_not_session_wide(
    single_connection_engine: Engine, tenants: Tenants
) -> None:
    with single_connection_engine.connect() as connection:
        with connection.begin():
            apply_tenant_context(connection, TenantContext(tenants.org_a))
            inside: Any = connection.execute(
                text("SELECT current_setting('app.organization_id', true)")
            ).scalar_one()
        after: Any = connection.execute(
            text("SELECT current_setting('app.organization_id', true)")
        ).scalar_one()

    assert inside == str(tenants.org_a)
    assert after in (None, "")


def test_concurrent_sessions_keep_their_own_context(app_engine: Engine, tenants: Tenants) -> None:
    factory = sessionmaker(app_engine)
    with (
        tenant_session(factory, TenantContext(tenants.org_a)) as in_a,
        tenant_session(factory, TenantContext(tenants.org_b)) as in_b,
    ):
        assert _school_count(in_a, tenants) == SCHOOLS_IN_A
        assert _school_count(in_b, tenants) == 1
        assert _school_count(in_a, tenants) == SCHOOLS_IN_A


# --- ORM: password_hash is deferred, RLS applies to ORM writes ----------------------------------


def test_session_get_user_works_without_reading_password_hash(
    app_engine: Engine, tenants: Tenants
) -> None:
    with tenant_session(sessionmaker(app_engine), TenantContext(tenants.org_a)) as session:
        user = session.get(User, tenants.user_a1)

        assert user is not None
        assert user.email == tenants.emails[0]
        assert "password_hash" in inspect(user).unloaded  # never selected


def test_loading_password_hash_is_refused_by_the_database(
    app_engine: Engine, tenants: Tenants
) -> None:
    with tenant_session(sessionmaker(app_engine), TenantContext(tenants.org_a)) as session:
        user = session.get(User, tenants.user_a1)
        assert user is not None
        with pytest.raises(DBAPIError):
            _ = user.password_hash
        session.rollback()


def test_session_get_does_not_find_users_of_other_tenants(
    app_engine: Engine, tenants: Tenants
) -> None:
    with tenant_session(sessionmaker(app_engine), TenantContext(tenants.org_a)) as session:
        assert session.get(User, tenants.user_b1) is None
        assert session.get(User, tenants.user_free) is None
    with sessionmaker(app_engine)() as no_context:
        assert no_context.get(User, tenants.user_a1) is None


def test_orm_insert_in_own_organization_works_and_in_another_is_refused(
    app_engine: Engine, tenants: Tenants
) -> None:
    factory = sessionmaker(app_engine)
    with factory() as session:
        bind_tenant(session, TenantContext(tenants.org_a))
        session.add(
            School(organization_id=tenants.org_a, name="ORM", slug=f"t3-orm-{uuid.uuid4().hex[:8]}")
        )
        session.flush()  # allowed
        session.rollback()

        session.add(
            School(organization_id=tenants.org_b, name="Hop", slug=f"t3-hop-{uuid.uuid4().hex[:8]}")
        )
        with pytest.raises(DBAPIError):
            session.flush()  # row-level security: WITH CHECK
        session.rollback()


def test_orm_membership_listing_is_scoped(app_engine: Engine, tenants: Tenants) -> None:
    with tenant_session(sessionmaker(app_engine), TenantContext(tenants.org_a)) as session:
        ids = set(session.scalars(select(Membership.id)))
    with tenant_session(
        sessionmaker(app_engine), TenantContext(tenants.org_a, tenants.school_a2)
    ) as session:
        school_ids = set(session.scalars(select(Membership.id)))

    assert {tenants.membership_a1, tenants.membership_a2, tenants.membership_admin_a} <= ids
    assert tenants.membership_b1 not in ids
    assert tenants.membership_a2 in school_ids
    assert tenants.membership_a1 not in school_ids
    assert tenants.membership_admin_a not in school_ids  # org-wide rows need an org-wide context


# --- the example FastAPI dependency -------------------------------------------------------------


class _NoDatabase:
    def __call__(self) -> Session:
        raise AssertionError("the database must not be touched before the tenant is known")


def _example_app() -> FastAPI:
    app = FastAPI()
    app.state.session_factory = _NoDatabase()

    @app.get("/example")
    def example(db: Session = Depends(get_tenant_db)) -> dict[str, int]:  # noqa: B008
        return {"schools": db.scalar(select(func.count()).select_from(School)) or 0}

    return app


@pytest.mark.parametrize(
    ("headers", "params"),
    [
        ({}, {}),
        ({"X-Organization-Id": str(uuid.uuid4())}, {}),
        ({"X-Tenant": str(uuid.uuid4()), "X-School-Id": str(uuid.uuid4())}, {}),
        ({}, {"organization_id": str(uuid.uuid4()), "school_id": str(uuid.uuid4())}),
        ({"Authorization": "Bearer anything", "Cookie": "session=anything"}, {}),
    ],
    ids=["nothing", "org-header", "tenant-headers", "query", "fake-credentials"],
)
def test_example_dependency_answers_401_and_ignores_what_the_client_sends(
    headers: dict[str, str], params: dict[str, str]
) -> None:
    response = TestClient(_example_app()).get("/example", headers=headers, params=params)

    assert response.status_code == 401
