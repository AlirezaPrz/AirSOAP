"""Password hashing with Argon2id (argon2-cffi), and the password policy."""

import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

MIN_PASSWORD_LENGTH = 12
# An upper bound keeps one request from making the server hash megabytes of input.
MAX_PASSWORD_LENGTH = 128


class Passwords:
    """Hashes and verifies passwords. The default parameters are argon2-cffi's RFC 9106 profile."""

    def __init__(self, hasher: PasswordHasher | None = None) -> None:
        self._hasher = hasher or PasswordHasher()
        self._dummy_hash: str | None = None

    def hash(self, password: str) -> str:
        return self._hasher.hash(password)

    def verify(self, stored_hash: str, password: str) -> bool:
        try:
            return self._hasher.verify(stored_hash, password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False

    def needs_rehash(self, stored_hash: str) -> bool:
        try:
            return self._hasher.check_needs_rehash(stored_hash)
        except InvalidHashError:
            return True

    def burn(self, password: str) -> None:
        """Do the same work as a real check, so an unknown account takes as long as a known one."""
        if self._dummy_hash is None:
            self._dummy_hash = self._hasher.hash(secrets.token_urlsafe(16))
        self.verify(self._dummy_hash, password)


def policy_problem(password: str, email: str) -> str | None:
    """A user-facing reason the password is not acceptable, or None."""
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Use at least {MIN_PASSWORD_LENGTH} characters."
    if len(password) > MAX_PASSWORD_LENGTH:
        return f"Use at most {MAX_PASSWORD_LENGTH} characters."
    if password.strip().casefold() in {email.casefold(), email.split("@")[0].casefold()}:
        return "The password must not be your email address."
    return None


def generate_temporary_password() -> str:
    """16 URL-safe characters (96 bits of randomness), shown once and changed at first sign-in."""
    return secrets.token_urlsafe(12)
