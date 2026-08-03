from __future__ import annotations

import secrets
from typing import Protocol

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from egregore.http_api.http.middleware.api_key_middleware import get_user_identity
from egregore.infrastructure.persistence.user_repository import (
    SQLiteUserRepository,
    get_default_user_repository,
)
from egregore.models.user import Invite, User

# ---------------------------------------------------------------------------
# Port protocols (interfaces layer must not depend on application/domain)
# ---------------------------------------------------------------------------


class IUserRepository(Protocol):
    def get_user_by_email(self, email: str) -> User | None: ...
    def create_user(self, **kwargs) -> User: ...
    def get_invite(self, code: str) -> Invite | None: ...
    def save_invite(self, invite: Invite) -> None: ...
    def use_invite(self, code: str) -> None: ...


class IProvenancePort(Protocol):
    async def log(self, event_type: str, payload: dict) -> None: ...


# ---------------------------------------------------------------------------
# DTOs (plain dataclasses, no domain imports)
# ---------------------------------------------------------------------------


class UserDTO(BaseModel):
    id: str
    username: str
    roles: list[str]
    vertical: str | None = None
    status: str
    pgp_fingerprint: str | None = None
    email: str | None = None
    created_at: int = 0
    last_login: int | None = None
    provenance: str | None = None


class InviteDTO(BaseModel):
    code: str
    issued_by: str
    issued_to: str | None = None
    role: str
    vertical: str | None = None
    verticals: list[str] = Field(default_factory=list)
    expires_at: int
    used_at: int | None = None
    status: str = "pending"
    provenance: str | None = None


class UserCreateDTO(BaseModel):
    id: str
    username: str
    roles: list[str]
    vertical: str | None = None
    status: str
    pgp_fingerprint: str | None = None
    email: str | None = None


# ---------------------------------------------------------------------------
# In-memory implementations (kept for tests that import directly)
# ---------------------------------------------------------------------------

_INVITES: dict[str, InviteDTO] = {}
_USERS: dict[str, UserDTO] = {}

# Back-compat exports for facade composition roots that import these directly
USERS = _USERS
INVITES = _INVITES


class _InMemoryUserRepository:
    async def get_by_email(self, email: str) -> UserDTO | None:
        for u in _USERS.values():
            if u.email == email:
                return u
        return None

    async def create(self, user: UserCreateDTO) -> UserDTO:
        dto = UserDTO(**user.model_dump())
        _USERS[dto.id] = dto
        return dto

    async def get_invite(self, code: str) -> InviteDTO | None:
        return _INVITES.get(code)

    async def save_invite(self, invite: InviteDTO) -> None:
        _INVITES[invite.code] = invite

    async def save_user(self, user: UserDTO) -> None:
        _USERS[user.id] = user


class _InMemoryProvenancePort:
    async def log(self, event_type: str, payload: dict) -> None:
        pass


# ---------------------------------------------------------------------------
# Dependency resolvers (runtime composition root)
# ---------------------------------------------------------------------------


async def get_user_repo() -> IUserRepository:
    return get_default_user_repository()


async def get_provenance_port() -> IProvenancePort:
    return _InMemoryProvenancePort()


# ---------------------------------------------------------------------------
# Admin guard
# ---------------------------------------------------------------------------


async def require_admin(
    identity=Depends(get_user_identity),  # noqa: B008
):
    from egregore.governance.permissions import Action, PermissionService

    svc = PermissionService()
    svc.require(identity, Action.USER_MANAGE)
    return identity


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


router = APIRouter()


class InviteRequest(BaseModel):
    role: str = Field(..., pattern=r"^(admin|user|guest)$")
    vertical: str | None = None
    verticals: list[str] = Field(default_factory=list)
    expires_in_seconds: int = 86400
    issued_to: str | None = None


class SignupRequest(BaseModel):
    code: str
    username: str
    pgp_fingerprint: str | None = None
    email: str | None = None


@router.post("/admin/invite")
async def create_invite(
    req: InviteRequest,
    identity=Depends(require_admin),  # noqa: B008
    user_repo: IUserRepository = Depends(get_user_repo),  # noqa: B008
    provenance: IProvenancePort = Depends(get_provenance_port),  # noqa: B008
):
    code = f"INV-{secrets.token_urlsafe(16)}"
    verticals = (
        req.verticals if req.verticals else ([req.vertical] if req.vertical else [])
    )
    invite = Invite(
        code=code,
        issued_by=identity.user_id,
        issued_to=req.issued_to,
        role=req.role,
        vertical=verticals[0] if verticals else None,
        expires_at=time.time_ns()
        + req.expires_in_seconds * int(1e9),
    )
    # Persist via SQLite repository if available.
    if isinstance(user_repo, SQLiteUserRepository):
        user_repo.save_invite(invite)
    await provenance.log(
        "invite_created",
        {"code": code, "role": req.role, "verticals": invite.verticals},
    )
    return {"code": code, "role": req.role, "verticals": invite.verticals}


@router.post("/signup")
async def signup(
    req: SignupRequest,
    user_repo: IUserRepository = Depends(get_user_repo),  # noqa: B008
    provenance: IProvenancePort = Depends(get_provenance_port),  # noqa: B008
):
    sqlite_repo = user_repo if isinstance(user_repo, SQLiteUserRepository) else None
    if sqlite_repo:
        invite = sqlite_repo.get_invite(req.code)
    else:
        invite_dto = await user_repo.get_invite(req.code)
        invite = None
        if invite_dto:
            invite = Invite(
                code=invite_dto.code,
                issued_by=invite_dto.issued_by,
                issued_to=invite_dto.issued_to,
                role=invite_dto.role,
                vertical=invite_dto.vertical,
                expires_at=invite_dto.expires_at,
                used_at=invite_dto.used_at,
                status=invite_dto.status,
                provenance=invite_dto.provenance,
            )

    if not invite or invite.status != "pending":
        raise HTTPException(status_code=400, detail="Invalid or used invite code")
    now = time.time_ns()
    if invite.expires_at < now:
        raise HTTPException(status_code=400, detail="Invite expired")

    if sqlite_repo:
        # Place new users in the same account as the inviter.
        account_id = (
            sqlite_repo.get_account_id_for_user(invite.issued_by) or "acct-default"
        )
        user = sqlite_repo.create_user(
            account_id=account_id,
            username=req.username,
            email=req.email,
            roles=[invite.role],
            status="active",
        )
        sqlite_repo.use_invite(req.code)
        # Grant verticals from invite if role is user.
        if invite.role == "user":
            for cell_id in invite.verticals:
                sqlite_repo.grant_vertical(user.id, cell_id, "write")
    else:
        user_create = UserCreateDTO(
            id=f"user-{req.username}",
            username=req.username,
            roles=[invite.role],
            vertical=invite.vertical,
            status="pending",
            pgp_fingerprint=req.pgp_fingerprint,
            email=req.email,
        )
        user = await user_repo.create(user_create)
        await user_repo.use_invite(req.code)

    await provenance.log(
        "signup", {"user_id": user.id, "username": user.username, "roles": user.roles}
    )

    return {"user_id": user.id, "status": user.status, "role": invite.role}
