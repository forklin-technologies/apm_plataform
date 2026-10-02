"""N1: a Session is bound to ONE tenant context for its whole life.

The review proved that a session could be re-bound to tenant B inside a SAVEPOINT: the database
kept running the transaction as tenant A (the rolled back SAVEPOINT undid the setting applied
inside it) while the session believed it acted for B. These tests pin the rule that closes it.
"""

from typing import Any

import pytest
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.db.tenant import TenantContext, TenantContextConflict, bind_tenant, tenant_session
from app.models import School
from tests.dbsupport import Tenants


def _db_org(session: Session) -> Any:
    """The organization the DATABASE believes this transaction acts for."""
    return session.execute(text("SELECT current_setting('app.organization_id', true)")).scalar_one()


def _school_ids(session: Session, tenants: Tenants) -> set[Any]:
    wanted = [tenants.school_a1, tenants.school_a2, tenants.school_b1]
    return set(session.scalars(select(School.id).where(School.id.in_(wanted))))


def test_binding_a_different_context_raises(app_engine: Engine, tenants: Tenants) -> None:
    with tenant_session(sessionmaker(app_engine), TenantContext(tenants.org_a)) as session:
        with pytest.raises(TenantContextConflict):
            bind_tenant(session, TenantContext(tenants.org_b))

        assert _db_org(session) == str(tenants.org_a)
        assert _school_ids(session, tenants) == {tenants.school_a1, tenants.school_a2}


def test_a_different_school_of_the_same_organization_is_also_a_different_context(
    app_engine: Engine, tenants: Tenants
) -> None:
    context = TenantContext(tenants.org_a, tenants.school_a1)
    with tenant_session(sessionmaker(app_engine), context) as session:
        with pytest.raises(TenantContextConflict):
            bind_tenant(session, TenantContext(tenants.org_a, tenants.school_a2))
        with pytest.raises(TenantContextConflict):
            bind_tenant(session, TenantContext(tenants.org_a))

        assert _school_ids(session, tenants) == {tenants.school_a1}


def test_binding_the_same_context_again_is_a_no_op(app_engine: Engine, tenants: Tenants) -> None:
    context = TenantContext(tenants.org_a)
    with tenant_session(sessionmaker(app_engine), context) as session:
        bind_tenant(session, TenantContext(tenants.org_a))  # equal value, another instance
        bind_tenant(session, context)

        assert _db_org(session) == str(tenants.org_a)
        assert _school_ids(session, tenants) == {tenants.school_a1, tenants.school_a2}


def test_rebinding_inside_a_savepoint_is_refused_and_the_rollback_changes_nothing(
    app_engine: Engine, tenants: Tenants
) -> None:
    """The exact review scenario: bind A, open a SAVEPOINT, try B inside it, roll back."""
    with tenant_session(sessionmaker(app_engine), TenantContext(tenants.org_a)) as session:
        savepoint = session.begin_nested()
        with pytest.raises(TenantContextConflict):
            bind_tenant(session, TenantContext(tenants.org_b))
        savepoint.rollback()

        # The database and the session still agree on A.
        assert _db_org(session) == str(tenants.org_a)
        assert session.info["tenant_context"] == TenantContext(tenants.org_a)
        assert _school_ids(session, tenants) == {tenants.school_a1, tenants.school_a2}


def test_the_same_context_is_idempotent_inside_a_savepoint(
    app_engine: Engine, tenants: Tenants
) -> None:
    context = TenantContext(tenants.org_a)
    with tenant_session(sessionmaker(app_engine), context) as session:
        with session.begin_nested():
            bind_tenant(session, context)
            assert _db_org(session) == str(tenants.org_a)
        assert _db_org(session) == str(tenants.org_a)


def test_a_first_bind_inside_a_savepoint_is_refused(app_engine: Engine, tenants: Tenants) -> None:
    """Applied inside a SAVEPOINT the setting would vanish on rollback: bind before opening one."""
    with sessionmaker(app_engine)() as session:
        session.execute(text("SELECT 1"))  # open the outer transaction
        with session.begin_nested():
            with pytest.raises(TenantContextConflict):
                bind_tenant(session, TenantContext(tenants.org_a))
        assert "tenant_context" not in session.info
        assert _school_ids(session, tenants) == set()  # still unbound: fails closed


def test_a_first_bind_in_a_session_already_in_a_transaction_still_works(
    app_engine: Engine, tenants: Tenants
) -> None:
    with sessionmaker(app_engine)() as session:
        assert _school_ids(session, tenants) == set()
        bind_tenant(session, TenantContext(tenants.org_a))
        assert _school_ids(session, tenants) == {tenants.school_a1, tenants.school_a2}


def test_the_identity_map_never_holds_objects_of_two_tenants(
    app_engine: Engine, tenants: Tenants
) -> None:
    with tenant_session(sessionmaker(app_engine), TenantContext(tenants.org_a)) as session:
        schools = list(session.scalars(select(School)))
        assert {s.id for s in schools if s.id in {tenants.school_a1, tenants.school_b1}} == {
            tenants.school_a1
        }

        with pytest.raises(TenantContextConflict):
            bind_tenant(session, TenantContext(tenants.org_b))
        # Even after the refused attempt, nothing of B can be loaded into this session.
        assert session.get(School, tenants.school_b1) is None
        organization_ids = {obj.organization_id for obj in session.identity_map.values()}
        assert organization_ids <= {tenants.org_a}


def test_a_closed_session_keeps_its_tenant_and_cannot_be_reused_for_another(
    app_engine: Engine, tenants: Tenants
) -> None:
    session = sessionmaker(app_engine)()
    try:
        bind_tenant(session, TenantContext(tenants.org_a))
        assert _school_ids(session, tenants) == {tenants.school_a1, tenants.school_a2}
        session.close()

        with pytest.raises(TenantContextConflict):
            bind_tenant(session, TenantContext(tenants.org_b))
        # It is still usable for the tenant it was bound to (the listener re-applies A).
        assert _school_ids(session, tenants) == {tenants.school_a1, tenants.school_a2}
    finally:
        session.close()


def test_every_tenant_session_gets_its_own_binding(app_engine: Engine, tenants: Tenants) -> None:
    factory = sessionmaker(app_engine)
    with tenant_session(factory, TenantContext(tenants.org_a)) as first:
        first_ids = _school_ids(first, tenants)
    with tenant_session(factory, TenantContext(tenants.org_b)) as second:
        second_ids = _school_ids(second, tenants)

    assert first_ids == {tenants.school_a1, tenants.school_a2}
    assert second_ids == {tenants.school_b1}
