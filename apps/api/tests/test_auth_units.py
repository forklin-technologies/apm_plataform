"""Units of the authentication module that need no database: passwords, tokens, the outbox, and
a static look at the code for habits that would leak (SQL from strings, log messages with data)."""

import ast
import re
from pathlib import Path

import pytest
from argon2 import PasswordHasher

from app.auth import passwords
from app.auth.emailer import FileOutbox, Message, invitation_message, password_changed_message
from app.auth.passwords import MAX_LENGTH, MIN_LENGTH
from app.auth.tokens import b64url, hash_token, keyed_digest, looks_like_a_token, new_token
from tests.dbsupport import API_DIR
from tests.helpers import TEST_SECRET, make_settings

# --- passwords --------------------------------------------------------------------------------


def test_a_password_hash_is_argon2id_with_the_owasp_parameters() -> None:
    stored = passwords.hash_password("a long enough password")

    assert stored.startswith("$argon2id$v=19$m=19456,t=2,p=1$")
    assert "a long enough password" not in stored
    assert passwords.hash_password("a long enough password") != stored  # a salt each time


def test_verification_is_true_only_for_the_right_password() -> None:
    stored = passwords.hash_password("a long enough password")

    assert passwords.verify_hash(stored, "a long enough password") is True
    assert passwords.verify_hash(stored, "a long enough passwor") is False
    assert passwords.verify_hash(stored, "") is False
    assert passwords.verify_hash(stored, "A long enough password") is False


@pytest.mark.parametrize(
    "garbage", ["", "x", "$argon2id$garbage", "plain text", "$2b$12$abcdefghijklmnopqrstuv", "\x00"]
)
def test_a_stored_value_that_is_not_a_hash_verifies_as_false_never_as_an_error(
    garbage: str,
) -> None:
    assert passwords.verify_hash(garbage, "whatever password") is False


def test_checking_without_a_stored_hash_still_verifies_once_and_never_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []
    real = passwords.verify_hash

    def spy(stored_hash: str, password: str) -> bool:
        seen.append(stored_hash)
        return real(stored_hash, password)

    monkeypatch.setattr(passwords, "verify_hash", spy)

    assert passwords.check_password(None, "whatever password") is False
    assert seen == [passwords._DUMMY_HASH]  # noqa: SLF001


def test_the_dummy_hash_costs_the_same_as_a_real_one_and_matches_no_known_password() -> None:
    dummy = passwords._DUMMY_HASH  # noqa: SLF001
    real = passwords.hash_password("a long enough password")

    parameters = re.compile(r"^\$argon2id\$v=19\$m=(\d+),t=(\d+),p=(\d+)\$")
    assert parameters.match(dummy) is not None
    assert parameters.match(dummy).groups() == parameters.match(real).groups()  # type: ignore[union-attr]
    for guess in ("", "password", "a long enough password", "0" * 12):
        assert passwords.verify_hash(dummy, guess) is False


def test_a_hash_with_weaker_parameters_asks_to_be_rehashed() -> None:
    weak = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1).hash("a long enough password")

    assert passwords.needs_rehash(weak) is True
    assert passwords.needs_rehash(passwords.hash_password("a long enough password")) is False
    assert passwords.needs_rehash("not a hash") is False


@pytest.mark.parametrize(
    ("password", "problem"),
    [
        ("x" * (MIN_LENGTH - 1), "too_short"),
        ("", "too_short"),
        ("x" * MIN_LENGTH, None),
        ("x" * MAX_LENGTH, None),
        ("x" * (MAX_LENGTH + 1), "too_long"),
        (" " * 20, "blank"),
        ("\t\n " * 6, "blank"),
        ("ünïcödé pässwörd", None),
        ("pass word with spaces", None),
    ],
)
def test_the_password_policy(password: str, problem: str | None) -> None:
    assert passwords.password_problem(password) == problem


# --- tokens -------------------------------------------------------------------------------------


def test_tokens_are_256_random_bits_and_unique() -> None:
    tokens = {new_token() for _ in range(2000)}

    assert len(tokens) == 2000
    for token in list(tokens)[:50]:
        assert len(token) == 43 and looks_like_a_token(token)


@pytest.mark.parametrize(
    "value",
    [
        "",
        "x",
        "A" * 42,
        "A" * 44,
        "A" * 42 + "=",
        "A" * 42 + " ",
        "A" * 42 + "\n",
        "é" * 43,
        "A" * 21 + "/" + "A" * 21,
    ],
)
def test_only_a_43_character_url_safe_value_looks_like_a_token(value: str) -> None:
    assert looks_like_a_token(value) is False


def test_the_stored_form_of_a_token_is_its_sha256_and_not_the_token() -> None:
    token = new_token()

    stored = hash_token(token)

    assert len(stored) == 32 and token.encode() != stored
    assert hash_token(token) == stored and hash_token(new_token()) != stored


def test_keyed_digests_are_deterministic_and_separated_by_purpose() -> None:
    assert keyed_digest(TEST_SECRET, "a", "v") == keyed_digest(TEST_SECRET, "a", "v")
    assert keyed_digest(TEST_SECRET, "a", "v") != keyed_digest(TEST_SECRET, "b", "v")
    assert keyed_digest(TEST_SECRET, "a", b"v") == keyed_digest(TEST_SECRET, "a", "v")
    assert b64url(b"\xff\xfe") == "__4"  # url-safe and unpadded


# --- the outbox --------------------------------------------------------------------------------


def test_a_message_is_a_private_file_in_a_private_directory(tmp_path: Path) -> None:
    directory = tmp_path / "nested" / "outbox"

    FileOutbox(str(directory)).send(
        Message("to@example.test", "Subject", "Body text", "invitation")
    )

    (written,) = list(directory.iterdir())
    assert written.stat().st_mode & 0o777 == 0o600
    assert directory.stat().st_mode & 0o777 == 0o700
    assert written.read_text() == "To: to@example.test\nSubject: Subject\n\nBody text\n"
    assert re.fullmatch(r"\d{8}T\d{6}-invitation-[0-9a-f]{8}\.txt", written.name)


def test_messages_never_overwrite_each_other(tmp_path: Path) -> None:
    outbox = FileOutbox(str(tmp_path))

    for _ in range(25):
        outbox.send(Message("to@example.test", "S", "B", "invitation"))

    assert len(list(tmp_path.iterdir())) == 25


def test_the_invitation_message_points_to_the_web_app_and_carries_the_token() -> None:
    settings = make_settings(public_base_url="https://app.example.test/")
    token = new_token()

    message = invitation_message(settings, "to@example.test", token, "Rede Demo")

    assert f"https://app.example.test/accept-invitation?token={token}" in message.body
    assert "Rede Demo" in message.subject and message.kind == "invitation"
    assert "//accept-invitation" not in message.body  # no doubled slash from the trailing one


def test_the_password_notice_has_no_secret_in_it() -> None:
    message = password_changed_message("to@example.test")

    assert message.kind == "password-changed"
    assert not re.search(r"token|argon2|password:|senha:", message.body, re.IGNORECASE)


# --- habits that leak, looked for in the code -----------------------------------------------------

APP = API_DIR / "app"


def _python_files() -> list[Path]:
    return sorted(path for path in APP.rglob("*.py"))


def test_no_sql_is_built_from_strings() -> None:
    """Every statement is a constant given to text(); values travel as bound parameters. An
    f-string, a concatenation or a .format() near SQL is how an injection (and with it the
    accepted risk of the forgeable settings) would get in."""
    offenders: list[str] = []
    for path in _python_files():
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", getattr(node.func, "attr", ""))
            if name in ("exec_driver_sql", "execute") and node.args:
                first = node.args[0]
                if isinstance(first, ast.JoinedStr | ast.BinOp) or (
                    isinstance(first, ast.Call) and getattr(first.func, "attr", "") == "format"
                ):
                    offenders.append(f"{path.relative_to(API_DIR)}:{node.lineno} ({name})")
            if name == "text" and node.args:
                first = node.args[0]
                if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
                    offenders.append(f"{path.relative_to(API_DIR)}:{node.lineno} (text)")
    assert offenders == []


def test_log_messages_of_the_auth_code_are_constants_or_carry_only_an_id_and_a_class() -> None:
    """Whatever a request carried (an e-mail, a password, a token) can only reach a log through an
    argument, so the call sites are looked at one by one."""
    allowed_extra = {  # (file, first-argument text) -> why an argument is acceptable
        ("app/core/errors.py", "unhandled error on request %s: %s"): "request id and class name",
    }
    found: list[tuple[str, str, int]] = []
    for path in _python_files():
        relative = str(path.relative_to(API_DIR))
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and getattr(node.func.value, "id", "") in ("logger", "logging")
                and node.func.attr in ("debug", "info", "warning", "error", "exception", "critical")
            ):
                first = node.args[0]
                assert isinstance(first, ast.Constant), f"{relative}:{node.lineno} not a constant"
                found.append((relative, str(first.value), len(node.args) - 1))
                has_arguments = len(node.args) > 1
                if has_arguments:
                    assert (relative, str(first.value)) in allowed_extra, (
                        f"{relative}:{node.lineno} logs an argument"
                    )
                assert not any(k.arg in ("exc_info", "stack_info") for k in node.keywords) or (
                    relative == "app/routers/health.py"
                ), f"{relative}:{node.lineno} logs a traceback"
    assert found, "the scan found no logging calls at all"


def test_nothing_prints_to_the_console_but_the_command_line_tools() -> None:
    printers = {
        str(path.relative_to(API_DIR))
        for path in _python_files()
        if re.search(r"^\s*print\(", path.read_text(), re.MULTILINE)
    }

    assert printers <= {"app/seed.py", "app/posture.py", "app/db/posture.py"}


def test_a_random_secret_is_not_a_constant_anywhere_in_the_code() -> None:
    """No long hex or base64-looking literal (a pasted key) in the application code."""
    pattern = re.compile(r"[\"'][A-Za-z0-9+/_=-]{40,}[\"']")
    hits = [
        f"{path.relative_to(API_DIR)}: {match.group(0)[:20]}…"
        for path in _python_files()
        for match in pattern.finditer(path.read_text())
        # a key mixes digits with both cases; identifiers (constraint names) do not
        if re.search(r"\d", match.group(0))
        and re.search(r"[A-Z]", match.group(0))
        and re.search(r"[a-z]", match.group(0))
    ]
    assert hits == []
