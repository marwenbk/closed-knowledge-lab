from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import AdminSession, AdminUser, AdminUserRole

ADMIN_SESSION_COOKIE = "topmed_admin_session"
ADMIN_CSRF_COOKIE = "topmed_admin_csrf"
TAKEOVER_ROLES = frozenset({"ADMIN", "SUPERVISOR", "SUPPORT_AGENT"})
KNOWLEDGE_ROLES = frozenset(
    {"ADMIN", "SUPERVISOR", "SUPPORT_AGENT", "HUMAN_REVIEWER", "KNOWLEDGE_EDITOR", "AUDITOR"}
)
KNOWLEDGE_EDITOR_ROLES = frozenset({"ADMIN", "SUPERVISOR", "KNOWLEDGE_EDITOR"})
KNOWLEDGE_PUBLISHER_ROLES = frozenset({"ADMIN", "SUPERVISOR"})
TUNING_ROLES = frozenset({"ADMIN", "SUPERVISOR", "HUMAN_REVIEWER", "KNOWLEDGE_EDITOR", "AUDITOR"})
TUNING_EDITOR_ROLES = frozenset({"ADMIN", "SUPERVISOR", "KNOWLEDGE_EDITOR"})
TUNING_PUBLISHER_ROLES = frozenset({"ADMIN", "SUPERVISOR"})
REVIEW_ROLES = frozenset({"ADMIN", "SUPERVISOR", "HUMAN_REVIEWER"})

PASSWORD_HASHER = PasswordHasher(
    time_cost=2,
    memory_cost=19_456,
    parallelism=1,
    hash_len=32,
    salt_len=16,
)
DUMMY_PASSWORD_HASH = PASSWORD_HASHER.hash("topmed-dummy-password-not-used-for-login")


class AdminAuthError(RuntimeError):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


@dataclass(frozen=True)
class AdminPrincipal:
    user_id: UUID
    session_id: UUID
    email: str
    display_name: str
    roles: frozenset[str]
    expires_at: datetime
    csrf_hash: str


@dataclass(frozen=True)
class AdminLoginResult:
    principal: AdminPrincipal
    session_token: str
    csrf_token: str


@dataclass(frozen=True)
class AdminBootstrapResult:
    user_id: UUID
    email: str
    created: bool


def _now() -> datetime:
    return datetime.now(UTC)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def normalize_email(value: str) -> str:
    return value.strip().casefold()


def validate_bootstrap_password(password: str) -> None:
    if not 12 <= len(password) <= 128:
        raise AdminAuthError(
            400,
            "INVALID_ADMIN_PASSWORD",
            "The bootstrap password must contain between 12 and 128 characters",
        )
    if password.casefold() in {
        "password",
        "password1234",
        "topmed",
        "topmed-admin",
        "change-me",
        "changeme1234",
    }:
        raise AdminAuthError(
            400,
            "INVALID_ADMIN_PASSWORD",
            "The bootstrap password must not be a known local default",
        )


def bootstrap_admin(
    engine: Engine,
    *,
    email: str,
    display_name: str,
    password: str,
) -> AdminBootstrapResult:
    normalized_email = normalize_email(email)
    if not normalized_email or "@" not in normalized_email or len(normalized_email) > 320:
        raise AdminAuthError(400, "INVALID_ADMIN_EMAIL", "The bootstrap email is invalid")
    clean_name = display_name.strip()
    if not clean_name or len(clean_name) > 200:
        raise AdminAuthError(400, "INVALID_ADMIN_NAME", "The bootstrap display name is invalid")
    validate_bootstrap_password(password)
    with Session(engine) as session, session.begin():
        user = session.scalar(select(AdminUser).where(AdminUser.email == normalized_email))
        if user is not None:
            if session.get(AdminUserRole, (user.id, "ADMIN")) is None:
                session.add(AdminUserRole(user_id=user.id, role="ADMIN"))
            return AdminBootstrapResult(user.id, user.email, False)
        user = AdminUser(
            id=uuid4(),
            email=normalized_email,
            display_name=clean_name,
            password_hash=PASSWORD_HASHER.hash(password),
            status="ACTIVE",
        )
        session.add_all((user, AdminUserRole(user_id=user.id, role="ADMIN")))
        return AdminBootstrapResult(user.id, user.email, True)


def _principal(session: Session, admin_session: AdminSession, user: AdminUser) -> AdminPrincipal:
    roles = frozenset(
        session.scalars(select(AdminUserRole.role).where(AdminUserRole.user_id == user.id)).all()
    )
    return AdminPrincipal(
        user_id=user.id,
        session_id=admin_session.id,
        email=user.email,
        display_name=user.display_name,
        roles=roles,
        expires_at=admin_session.expires_at,
        csrf_hash=admin_session.csrf_hash,
    )


def login_admin(
    engine: Engine,
    settings: Settings,
    *,
    email: str,
    password: str,
) -> AdminLoginResult:
    invalid = AdminAuthError(401, "INVALID_ADMIN_CREDENTIALS", "Invalid email or password")
    now = _now()
    with Session(engine) as session, session.begin():
        user = session.scalar(select(AdminUser).where(AdminUser.email == normalize_email(email)))
        try:
            verified = PASSWORD_HASHER.verify(
                user.password_hash if user is not None else DUMMY_PASSWORD_HASH,
                password,
            )
        except (InvalidHashError, VerificationError) as exc:
            raise invalid from exc
        if user is None or user.status != "ACTIVE" or not verified:
            raise invalid
        if PASSWORD_HASHER.check_needs_rehash(user.password_hash):
            user.password_hash = PASSWORD_HASHER.hash(password)
            user.updated_at = now
        session_token = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(32)
        admin_session = AdminSession(
            id=uuid4(),
            user_id=user.id,
            token_hash=_digest(session_token),
            csrf_hash=_digest(csrf_token),
            expires_at=now + timedelta(seconds=settings.admin_session_ttl_seconds),
        )
        session.add(admin_session)
        session.flush()
        return AdminLoginResult(
            principal=_principal(session, admin_session, user),
            session_token=session_token,
            csrf_token=csrf_token,
        )


def authenticate_admin(engine: Engine, token: str | None) -> AdminPrincipal:
    invalid = AdminAuthError(401, "ADMIN_AUTH_REQUIRED", "Administrator authentication required")
    if not token:
        raise invalid
    now = _now()
    with Session(engine) as session:
        admin_session = session.scalar(
            select(AdminSession).where(AdminSession.token_hash == _digest(token))
        )
        if (
            admin_session is None
            or admin_session.revoked_at is not None
            or admin_session.expires_at <= now
        ):
            raise invalid
        user = session.get(AdminUser, admin_session.user_id)
        if user is None or user.status != "ACTIVE":
            raise invalid
        return _principal(session, admin_session, user)


def require_takeover_role(principal: AdminPrincipal) -> None:
    require_any_role(principal, TAKEOVER_ROLES)


def require_any_role(principal: AdminPrincipal, roles: frozenset[str]) -> None:
    if principal.roles.isdisjoint(roles):
        raise AdminAuthError(403, "ADMIN_PERMISSION_DENIED", "Permission denied")


def verify_csrf(
    principal: AdminPrincipal,
    cookie_token: str | None,
    header_token: str | None,
) -> None:
    if not cookie_token or not header_token or not hmac.compare_digest(cookie_token, header_token):
        raise AdminAuthError(403, "CSRF_VALIDATION_FAILED", "CSRF validation failed")
    if not hmac.compare_digest(_digest(header_token), principal.csrf_hash):
        raise AdminAuthError(403, "CSRF_VALIDATION_FAILED", "CSRF validation failed")


def revoke_admin_session(engine: Engine, principal: AdminPrincipal) -> None:
    with Session(engine) as session, session.begin():
        admin_session = session.get(AdminSession, principal.session_id, with_for_update=True)
        if admin_session is not None and admin_session.revoked_at is None:
            admin_session.revoked_at = _now()
