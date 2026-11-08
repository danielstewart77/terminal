"""The terminal signs in against the hive's store, and a change there lands.

The requirements this guards are the ones the move was for. The terminal
used to open `hive.db` itself, with its own `sqlite3` calls and its own
signing key minted into that file, while the console moved onto a store
named by `HIVE_DATABASE_URL`. Two halves of one account store then disagree,
and what that looks like from a chair is being signed out of the terminal
and nowhere else.

So: an account made where the console makes it can sign in here, a password
reset done where the console does it ends the session held here, and the
store answering is whichever one this site's own variable names — on
Postgres as well as on the file, because the file half passing proves
nothing about the half the hive is moving to.

The Postgres tests need a server to create a scratch database on and are
skipped without `HIVE_DATA_TEST_ADMIN_DSN`, which has no default because a
default would be a credential in a tracked file.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from starlette.testclient import TestClient

import auth
import main
import store
from hive_site import testing as accounts


def _client() -> TestClient:
    return TestClient(main.app, base_url="https://testserver")


def _sign_in(client: TestClient, username: str, password: str):
    return client.post(
        "/login",
        data={"username": username, "password": password, "next": "/terminal"},
        follow_redirects=False,
    )


# ── the file half, which every other test in this suite runs on ─────────


def test_an_account_made_where_the_console_makes_it_signs_in_here(
        builtin_terminal):
    """One store, not two.

    The account is created through the shared store — the operation the
    console's admin page calls — and then signed in at this site's own login
    page. A terminal holding a store of its own answers "invalid username or
    password" here, which is the truthful-looking message for entirely the
    wrong reason.
    """
    accounts.run(auth.get_store().create_user(
        "sloan", temp_password="sloans-temporary"))  # secret-guard: allow

    response = _sign_in(_client(), "sloan", "sloans-temporary")

    assert response.status_code == 303, response.text
    # Straight to the password page, because the temporary one must change.
    assert response.headers["location"] == "/password"
    assert auth.SESSION_COOKIE_NAME in response.cookies


def test_a_password_reset_where_the_console_does_it_ends_the_session_here(
        builtin_terminal):
    """The defect this story exists to close, driven end to end.

    The reset moves `session_version` in the store. A terminal reading that
    column out of a different file honours the old value, keeps serving the
    browser, and the operator sees one site signed out and the other not.
    """
    signed_in = builtin_terminal.settled_user("sloan", "sloans-own-pass")
    assert signed_in.get("/terminal", follow_redirects=False).status_code == 200

    accounts.run(auth.get_store().reset_password("sloan", "a-reset-password"))

    turned_away = signed_in.get("/terminal", follow_redirects=False)
    assert turned_away.status_code == 303
    assert turned_away.headers["location"].startswith("/login")
    assert signed_in.get("/api/minds").status_code == 401


# ── the half the hive is moving to ──────────────────────────────────────


def _scratch_postgres():
    """A database of this site's own on the named server, and its DSN."""
    import asyncpg

    if not accounts.ADMIN_DSN:
        pytest.skip(accounts.NO_SERVER)
    name = f"terminal_site_{uuid.uuid4().hex[:12]}"

    async def create() -> str:
        admin = await asyncpg.connect(accounts.ADMIN_DSN)
        try:
            await admin.execute(f'CREATE DATABASE "{name}"')
        finally:
            await admin.close()
        base, _, _ = accounts.ADMIN_DSN.rpartition("/")
        dsn = f"{base}/{name}"
        target = await asyncpg.connect(dsn)
        try:
            for schema in accounts.schemas():
                await target.execute(schema)
        finally:
            await target.close()
        return dsn

    async def drop() -> None:
        admin = await asyncpg.connect(accounts.ADMIN_DSN)
        try:
            await admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = $1 AND pid <> pg_backend_pid()", name)
            await admin.execute(f'DROP DATABASE IF EXISTS "{name}"')
        finally:
            await admin.close()

    return asyncio.run(create()), drop


@pytest.fixture
def postgres_terminal(monkeypatch, fake_comms, tmp_path):
    """This site on a scratch Postgres, signed in through its own pages.

    `TestClient` as a context manager, so the app's own lifespan opens the
    pool on the portal's loop and every request in the test runs on that
    same loop. A pool opened anywhere else belongs to a loop the requests
    are not on, which is why the rest of this suite stays on the file.
    """
    dsn, drop = _scratch_postgres()
    monkeypatch.setenv("GATEWAY_API_URL", fake_comms.url)
    monkeypatch.setenv("HIVE_AUTH_BACKEND", "builtin")
    monkeypatch.setenv("HIVE_DATABASE_URL", dsn)
    # Named, and pointing at a file that does not exist: if anything in this
    # test reaches SQLite, it must fail rather than quietly answer.
    monkeypatch.setenv("HIVE_DB_PATH", str(tmp_path / "must-not-be-used.db"))
    monkeypatch.setenv("HIVE_SECRET_KEY", "postgres-half-test-secret")
    monkeypatch.setenv("HIVE_SEED_USERNAME", "daniel")
    monkeypatch.setenv("HIVE_SEED_PASSWORD", "daniels-own-password")
    auth.forget_secret_key()
    try:
        yield dsn
    finally:
        asyncio.run(drop())


def test_the_terminal_signs_in_against_postgres(postgres_terminal):
    """Requirement one, on the store the hive is cutting over to.

    The account is the one this site's lifespan seeds, so the whole path is
    the real one: seed, sign in, hold a session, reach a signed-in route.
    """
    with _client() as client:
        signed_in = _sign_in(client, "daniel", "daniels-own-password")
        assert signed_in.status_code == 303, signed_in.text
        assert signed_in.headers["location"] == "/terminal"

        page = client.get("/terminal", follow_redirects=False)
        assert page.status_code == 200

        signed_out = client.post("/logout", follow_redirects=False)
        assert signed_out.status_code == 303
        assert client.get("/terminal", follow_redirects=False).status_code == 303


def test_a_version_bump_in_postgres_ends_the_terminals_session(
        postgres_terminal):
    """Requirement three, on Postgres.

    The bump is written straight to the database, standing in for the
    console making it from its own process — which is the case the file
    version of this test cannot represent, because the two sites would be on
    one file and this one would not be.
    """
    import asyncpg

    with _client() as client:
        assert _sign_in(client, "daniel",
                        "daniels-own-password").status_code == 303
        assert client.get("/terminal",
                          follow_redirects=False).status_code == 200

        async def bump() -> None:
            conn = await asyncpg.connect(postgres_terminal)
            try:
                await conn.execute(
                    "UPDATE core.users SET session_version = "
                    "session_version + 1 WHERE username = 'daniel'")
            finally:
                await conn.close()

        asyncio.run(bump())

        turned_away = client.get("/terminal", follow_redirects=False)
        assert turned_away.status_code == 303
        assert turned_away.headers["location"].startswith("/login")


# ── which store answers, and the way back ───────────────────────────────


def test_a_terminal_pointed_at_a_database_it_cannot_reach_refuses_to_start(
        tmp_path, monkeypatch):
    """It must not come up on the file beside it.

    A site that silently fell back would serve from a store nothing else
    reads, show an empty hive, and report itself healthy — the exact failure
    the fence in 1.2.18 was built for, arrived at from the other direction.
    """
    monkeypatch.setenv(
        "HIVE_DATABASE_URL",
        "postgresql://nobody:nothing@127.0.0.1:1/not-a-database")
    # A path of its own, not the one the suite's own fixture has already
    # opened a store on: the question is whether this open creates a file.
    fallback = tmp_path / "fallback" / "hive.db"
    monkeypatch.setenv("HIVE_DB_PATH", str(fallback))

    with pytest.raises(Exception) as refused:
        asyncio.run(store.open())

    assert not isinstance(refused.value, AssertionError)
    assert not fallback.exists(), "it created the file it was told not to use"


def test_unsetting_the_variable_returns_this_site_to_the_file(
        tmp_path, monkeypatch):
    """Requirement four: the revert, and the two sites pointed apart.

    The choice is read when this process opens its store and from this
    process's own environment, which is what lets the terminal be put back
    on the file while the console stays on Postgres.
    """
    monkeypatch.setenv("HIVE_DATABASE_URL", "")
    monkeypatch.setenv("HIVE_DB_PATH", str(tmp_path / "hive.db"))

    asyncio.run(store.open())
    try:
        assert not store.on_postgres()
        assert store.sqlite_path() == tmp_path / "hive.db"
        # And it is a working store, not merely a chosen one.
        made = accounts.make_user("sloan", "sloans-own-pass")
        assert accounts.run(store.get_user("sloan"))["id"] == made["id"]
    finally:
        asyncio.run(store.close())
