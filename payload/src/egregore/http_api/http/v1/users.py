from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from egregore.governance.permissions import Action, PermissionService
from egregore.http_api.http.middleware.api_key_middleware import get_user_identity
from egregore.infrastructure.persistence.user_repository import (
    SQLiteUserRepository,
    get_default_user_repository,
)
from egregore.models.user import UserIdentity

router = APIRouter(prefix="/admin/users", tags=["users"])


async def require_admin_identity(
    identity: UserIdentity = Depends(get_user_identity),  # noqa: B008
) -> UserIdentity:
    PermissionService().require(identity, Action.USER_MANAGE)
    return identity


class CreateUserRequest(BaseModel):
    username: str
    email: str | None = None
    role: str = Field(..., pattern=r"^(admin|user|guest)$")
    verticals: list[str] = Field(default_factory=list)


class GrantRequest(BaseModel):
    cell_id: str
    permission: str = Field(..., pattern=r"^(read|write)$")


class RevokeRequest(BaseModel):
    cell_id: str


@router.get("")
def list_users(
    repo: SQLiteUserRepository = Depends(get_default_user_repository),  # noqa: B008
    identity: UserIdentity = Depends(require_admin_identity),  # noqa: B008
):
    return {"users": repo.list_users()}


@router.post("")
def create_user(
    req: CreateUserRequest,
    repo: SQLiteUserRepository = Depends(get_default_user_repository),  # noqa: B008
    identity: UserIdentity = Depends(require_admin_identity),  # noqa: B008
):
    # New users are created in the admin's account.
    account_id = identity.account_id or f"acct-{identity.tenant_id}"
    if repo.get_account(account_id) is None:
        account_id = repo.create_account(
            name=f"{identity.tenant_id}",
            owner_user_id=identity.user_id,
            account_id=account_id,
        )

    user = repo.create_user(
        account_id=account_id,
        username=req.username,
        email=req.email,
        roles=[req.role],
        status="active",
    )
    for cell_id in req.verticals:
        repo.grant_vertical(user.id, cell_id, "write")
    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "roles": user.roles,
        "status": user.status,
    }


@router.get("/{user_id}")
def get_user(
    user_id: str,
    repo: SQLiteUserRepository = Depends(get_default_user_repository),  # noqa: B008
    identity: UserIdentity = Depends(require_admin_identity),  # noqa: B008
):
    user = repo.get_user_with_grants(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@router.post("/{user_id}/verticals")
def grant_vertical(
    user_id: str,
    req: GrantRequest,
    repo: SQLiteUserRepository = Depends(get_default_user_repository),  # noqa: B008
    identity: UserIdentity = Depends(require_admin_identity),  # noqa: B008
):
    user = repo.get_user_by_id(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    repo.grant_vertical(user_id, req.cell_id, req.permission)
    return {
        "ok": True,
        "user_id": user_id,
        "cell_id": req.cell_id,
        "permission": req.permission,
    }


@router.delete("/{user_id}/verticals/{cell_id}")
def revoke_vertical(
    user_id: str,
    cell_id: str,
    repo: SQLiteUserRepository = Depends(get_default_user_repository),  # noqa: B008
    identity: UserIdentity = Depends(require_admin_identity),  # noqa: B008
):
    user = repo.get_user_by_id(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    repo.revoke_vertical(user_id, cell_id)
    return {"ok": True, "user_id": user_id, "cell_id": cell_id}


@router.delete("/{user_id}")
def disable_user(
    user_id: str,
    repo: SQLiteUserRepository = Depends(get_default_user_repository),  # noqa: B008
    identity: UserIdentity = Depends(require_admin_identity),  # noqa: B008
):
    if user_id == identity.user_id:
        raise HTTPException(status_code=400, detail="Cannot disable yourself")
    user = repo.get_user_by_id(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    from egregore.models.user import User as UserModel

    updated = UserModel(
        id=user.id,
        username=user.username,
        email=user.email,
        roles=user.roles,
        vertical=user.vertical,
        status="disabled",
        created_at=user.created_at,
        last_login=user.last_login,
        provenance=user.provenance,
    )
    repo.save_user(updated)
    return {"ok": True, "user_id": user_id, "status": "disabled"}
