# ruff: noqa: E501  (SQL text)
"""The scene of the statement tests: a school with a ledger worked out by hand, people of every
role who can really log in, and a second organization to prove the isolation.

Everything is COMMITTED (the API runs in its own connections) and removed again by `drop_scene`,
which keys on the e-mail suffix the school builder put on every user it made.
"""

import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass

import pytest
from sqlalchemy import Connection, Engine, text

from tests.authsupport import PASSWORD_HASH, Api, ApiFactory, TestUser
from tests.dbsupport import purge_financial
from tests.financial.support import (
    Fresh,
    add_bank_fee,
    add_cash_contribution,
    add_collaborator_expense,
    add_expense,
    add_refund,
    check_consistency,
    make_school,
    utc,
)

MARCH = "2025-03"
# The ledger of the scene, March 2025 (America/Sao_Paulo, UTC-3), settled in this order. The
# running balance after each entry, from an opening of 10000: 15000, 18000, 16000, 12000, 11650, 12150.
OPENING = 10000
RUNNING = [15000, 18000, 16000, 12000, 11650, 12150]


@dataclass
class Scene:
    fresh: Fresh
    suffix: str
    users: dict[str, TestUser]
    entries: dict[str, uuid.UUID]  # the ledger rows of March, by what they are
    other: Fresh  # a school of ANOTHER organization, with a ledger of its own
    other_users: dict[str, TestUser]
    sibling_school: uuid.UUID  # a second school of the SAME organization as `fresh`


def _add_member(
    conn: Connection, fresh: Fresh, suffix: str, label: str, role: str, *, org_wide: bool
) -> TestUser:
    user, membership = uuid.uuid4(), uuid.uuid4()
    email = f"t5-{label}-{suffix}@example.test"
    conn.execute(
        text(
            "INSERT INTO users (id, email, full_name, password_hash) "
            "VALUES (:id, :email, :name, :hash)"
        ),
        {"id": user, "email": email, "name": f"T5 {label}", "hash": PASSWORD_HASH},
    )
    conn.execute(
        text(
            "INSERT INTO memberships (id, user_id, organization_id, school_id, role, status) "
            "VALUES (:id, :user, :org, :school, :role, 'active')"
        ),
        {
            "id": membership,
            "user": user,
            "org": fresh.org,
            "school": None if org_wide else fresh.school,
            "role": role,
        },
    )
    return TestUser(user, email, (membership,))


def _school_people(conn: Connection, fresh: Fresh, suffix: str) -> dict[str, TestUser]:
    """The three users the builder made get a password; a school admin and a viewer are added."""
    users: dict[str, TestUser] = {}
    for label, user_id, role in (
        ("admin", fresh.admin, "organization_admin"),
        ("treasurer", fresh.treasurer, "treasurer"),
        ("staff", fresh.staff, "staff"),
    ):
        conn.execute(
            text("UPDATE users SET password_hash = :h WHERE id = :id"),
            {"h": PASSWORD_HASH, "id": user_id},
        )
        email: str = conn.execute(
            text("SELECT email FROM users WHERE id = :id"), {"id": user_id}
        ).scalar_one()
        membership: uuid.UUID = conn.execute(
            text("SELECT id FROM memberships WHERE user_id = :u AND role = :r"),
            {"u": user_id, "r": role},
        ).scalar_one()
        users[label] = TestUser(user_id, email, (membership,))
    users["school_admin"] = _add_member(
        conn, fresh, suffix, "school_admin", "school_admin", org_wide=False
    )
    users["viewer"] = _add_member(conn, fresh, suffix, "viewer", "viewer", org_wide=False)
    return users


def _suffix_of(conn: Connection, fresh: Fresh) -> str:
    slug: str = conn.execute(
        text("SELECT slug FROM schools WHERE id = :s"), {"s": fresh.school}
    ).scalar_one()
    return slug.removeprefix("t5-sch-")


def build_march(conn: Connection, f: Fresh) -> dict[str, uuid.UUID]:
    """The ledger worked out by hand (see the module header). Before it, an opening of 10000 in
    February; around it, things that must NOT count: a pending reimbursement, a Pix in review, a
    contribution of April."""
    e: dict[str, uuid.UUID] = {}
    e["opening"] = add_cash_contribution(
        conn, f, OPENING, utc(2025, 2, 20, 12), guardian="Ana Antiga"
    )
    e["maria"] = add_cash_contribution(conn, f, 5000, utc(2025, 3, 3, 12), guardian="Maria Exemplo")
    e["joao"] = add_cash_contribution(
        conn,
        f,
        3000,
        utc(2025, 3, 5, 12),
        guardian="João Teste",
        method="TRANSFER",
        category=f.cat_other,
    )
    e["material"] = add_expense(
        conn,
        f,
        2000,
        status="PAID",
        settled_at=utc(2025, 3, 10, 12),
        occurred_at=utc(2025, 3, 10, 12),
        description="Material de pintura",
    )
    e["collab"], e["reimbursement"] = add_collaborator_expense(
        conn,
        f,
        4000,
        reimbursed_at=utc(2025, 3, 12, 15),
        occurred_at=utc(2025, 3, 12, 12),
        description="Tinta",
    )
    e["fee"] = add_bank_fee(conn, f, 350, settled_at=utc(2025, 3, 15, 12))
    e["refund"] = add_refund(conn, f, 500, status="CONFIRMED", settled_at=utc(2025, 3, 20, 12))
    # Outside the balance of March:
    e["pending_collab"], e["pending_reimbursement"] = add_collaborator_expense(
        conn, f, 1500, occurred_at=utc(2025, 3, 25, 12), description="Cola"
    )
    e["april"] = add_cash_contribution(conn, f, 700, utc(2025, 4, 2, 12), guardian="Beatriz Abril")
    return e


@pytest.fixture
def scene(admin_engine: Engine) -> Iterator[Scene]:
    with admin_engine.begin() as conn:
        fresh = make_school(conn)
        suffix = _suffix_of(conn, fresh)
        users = _school_people(conn, fresh, suffix)
        entries = build_march(conn, fresh)
        other = make_school(conn)
        other_suffix = _suffix_of(conn, other)
        other_users = _school_people(conn, other, other_suffix)
        add_cash_contribution(conn, other, 99900, utc(2025, 3, 8, 12), guardian="Pessoa Alheia")
        sibling = uuid.uuid4()
        conn.execute(
            text(
                "INSERT INTO schools (id, organization_id, name, slug) VALUES (:id, :org, 'Sibling', :slug)"
            ),
            {"id": sibling, "org": fresh.org, "slug": f"t5-sib-{suffix}"},
        )
        conn.exec_driver_sql("SET CONSTRAINTS ALL IMMEDIATE")
    try:
        yield Scene(fresh, suffix, users, entries, other, other_users, sibling)
    finally:
        drop_scene(admin_engine, [(fresh, suffix), (other, other_suffix)])


def drop_scene(engine: Engine, schools: list[tuple[Fresh, str]]) -> None:
    with engine.begin() as conn:
        orgs = [fresh.org for fresh, _ in schools]
        patterns = [f"t5-%-{suffix}@example.test" for _, suffix in schools]
        purge_financial(conn, orgs)
        conn.execute(
            text(
                "DELETE FROM sessions WHERE user_id IN (SELECT id FROM users WHERE email LIKE ANY(:p))"
            ),
            {"p": patterns},
        )
        conn.execute(text("DELETE FROM memberships WHERE organization_id = ANY(:o)"), {"o": orgs})
        conn.execute(text("DELETE FROM users WHERE email LIKE ANY(:p)"), {"p": patterns})
        conn.execute(text("DELETE FROM schools WHERE organization_id = ANY(:o)"), {"o": orgs})
        conn.execute(text("DELETE FROM organizations WHERE id = ANY(:o)"), {"o": orgs})


Login = Callable[[TestUser], Api]


@pytest.fixture
def login(apis: ApiFactory) -> Login:
    """`login(user)` is a browser that is logged in as that user (with the only membership it has)."""

    def make(user: TestUser) -> Api:
        api = apis.make()
        response = api.login(user)
        assert response.status_code == 200, response.text
        return api

    return make


def statement_path(school: uuid.UUID, tail: str = "") -> str:
    return f"/api/v1/schools/{school}/statement{tail}"


# --- the contributions of a month, for the contributions report -----------------------------------
# March 2025, in the cash ledger (group CONTRIBUTIONS, section A), in this order:
#   02/03 Pix pela plataforma 2000 (no name: anonymous)    03/03 dinheiro 2000 Maria Exemplo (aluno, turma)
#   04/03 Pix direto 3000 João Teste                       05/03 transferência 3000 Carla Teste
#   06/03 dinheiro 4000 Pedro Quatro                       07/03 dinheiro 1500 Ana Quinze
# Section B (another income group): 08/03 a donation of 10000 in cash from Doador Generoso.
# Outside March: 5000 in February and 700 in April. Then, NOW (the current month): a late
# adjustment (a cash contribution of March settled after March was closed: booked now, flagged), and
# three that are not in the ledger: a Pix waiting for payment (2500), one in review (3000, 3500
# received) and a cancelled one (900).
LATE_AMOUNT = 700
OUTSIDE_NOW = (2500, 3000, 900)


def build_contributions(conn: Connection, f: Fresh) -> dict[str, uuid.UUID]:
    from tests.financial.support import (
        add_direct_pix,
        add_pix_contribution,
        add_pix_review,
        set_status,
    )

    e: dict[str, uuid.UUID] = {}
    add_cash_contribution(conn, f, 5000, utc(2025, 2, 10, 12), guardian="Ana Antiga")
    e["pix"], _ = add_pix_contribution(conn, f, 2000, paid_at=utc(2025, 3, 2, 12))
    e["maria"] = add_cash_contribution(
        conn,
        f,
        2000,
        utc(2025, 3, 3, 12),
        guardian="Maria Exemplo",
        student="Aluno Um",
        class_name="5º A",
    )
    e["direct"] = add_direct_pix(
        conn, f, 3000, settled_at=utc(2025, 3, 4, 12), guardian="João Teste"
    )
    e["carla"] = add_cash_contribution(
        conn, f, 3000, utc(2025, 3, 5, 12), guardian="Carla Teste", method="TRANSFER"
    )
    e["pedro"] = add_cash_contribution(conn, f, 4000, utc(2025, 3, 6, 12), guardian="Pedro Quatro")
    e["ana"] = add_cash_contribution(conn, f, 1500, utc(2025, 3, 7, 12), guardian="Ana Quinze")
    e["donation"] = add_cash_contribution(
        conn, f, 10000, utc(2025, 3, 8, 12), guardian="Doador Generoso", category=f.cat_other
    )
    add_cash_contribution(conn, f, 700, utc(2025, 4, 2, 12), guardian="Beatriz Abril")
    # Close March, so that the next settlement dated in March is booked late.
    check_consistency(conn)
    conn.execute(
        text(
            "INSERT INTO monthly_closings (organization_id, school_id, period_start, closed_by_user_id) "
            "VALUES (:o, :s, '2025-03-01', :u)"
        ),
        {"o": f.org, "s": f.school, "u": f.treasurer},
    )
    e["late"] = add_cash_contribution(
        conn, f, LATE_AMOUNT, utc(2025, 3, 20, 12), guardian="Tardia Silva"
    )
    e["waiting"], _ = add_pix_contribution(conn, f, OUTSIDE_NOW[0])
    e["review"], _ = add_pix_review(conn, f, OUTSIDE_NOW[1], 3500)
    e["cancelled"], _ = add_pix_contribution(conn, f, OUTSIDE_NOW[2])
    set_status(conn, e["cancelled"], "CANCELLED")
    return e


@pytest.fixture
def contrib_scene(admin_engine: Engine) -> Iterator[Scene]:
    with admin_engine.begin() as conn:
        fresh = make_school(conn)
        suffix = _suffix_of(conn, fresh)
        users = _school_people(conn, fresh, suffix)
        entries = build_contributions(conn, fresh)
        other = make_school(conn)
        other_suffix = _suffix_of(conn, other)
        other_users = _school_people(conn, other, other_suffix)
        add_cash_contribution(conn, other, 99900, utc(2025, 3, 8, 12), guardian="Pessoa Alheia")
        sibling = uuid.uuid4()
        conn.execute(
            text(
                "INSERT INTO schools (id, organization_id, name, slug) VALUES (:id, :org, 'Sibling', :slug)"
            ),
            {"id": sibling, "org": fresh.org, "slug": f"t5-sib-{suffix}"},
        )
        conn.exec_driver_sql("SET CONSTRAINTS ALL IMMEDIATE")
    try:
        yield Scene(fresh, suffix, users, entries, other, other_users, sibling)
    finally:
        drop_scene(admin_engine, [(fresh, suffix), (other, other_suffix)])
