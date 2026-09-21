"""Tests for the vertical-aware permission service."""

from __future__ import annotations

import pytest

from egregore.governance.permissions import Action, PermissionService
from egregore.models.user import UserIdentity, VerticalGrant


@pytest.fixture
def admin_identity() -> UserIdentity:
    return UserIdentity(
        tenant_id="default",
        user_id="user-admin",
        username="admin",
        email=None,
        roles=["admin"],
        vertical_grants=[],
        status="active",
    )


@pytest.fixture
def user_identity() -> UserIdentity:
    return UserIdentity(
        tenant_id="default",
        user_id="user-alice",
        username="alice",
        email="alice@example.com",
        roles=["user"],
        vertical_grants=[VerticalGrant("user-alice", "sweng_python", "write")],
        status="active",
    )


@pytest.fixture
def guest_identity() -> UserIdentity:
    return UserIdentity(
        tenant_id="default",
        user_id="user-guest",
        username="guest",
        email=None,
        roles=["guest"],
        vertical_grants=[],
        status="active",
    )


@pytest.fixture
def disabled_identity() -> UserIdentity:
    return UserIdentity(
        tenant_id="default",
        user_id="user-disabled",
        username="disabled",
        email=None,
        roles=["user"],
        vertical_grants=[],
        status="disabled",
    )


def test_admin_can_do_everything(admin_identity):
    svc = PermissionService()
    assert svc.can(admin_identity, Action.USER_MANAGE).ok
    assert svc.can(admin_identity, Action.SYSTEM_FREEZE).ok
    assert svc.can(admin_identity, Action.CHAT_ADMIN).ok
    assert svc.can(admin_identity, Action.VERTICAL_WRITE, "any_cell").ok


def test_user_can_ask_and_dossier(user_identity):
    svc = PermissionService()
    assert svc.can(user_identity, Action.CHAT_ASK).ok
    assert svc.can(user_identity, Action.CHAT_DOSSIER).ok


def test_user_cannot_admin(user_identity):
    svc = PermissionService()
    assert not svc.can(user_identity, Action.USER_MANAGE).ok
    assert not svc.can(user_identity, Action.CHAT_ADMIN).ok
    assert not svc.can(user_identity, Action.SYSTEM_FREEZE).ok


def test_user_vertical_write_grant(user_identity):
    svc = PermissionService()
    assert svc.can(user_identity, Action.VERTICAL_WRITE, "sweng_python").ok
    assert svc.can(user_identity, Action.VERTICAL_READ, "sweng_python").ok


def test_user_vertical_write_denied_elsewhere(user_identity):
    svc = PermissionService()
    assert not svc.can(user_identity, Action.VERTICAL_WRITE, "medicine_diagnosis").ok


def test_guest_read_only(guest_identity):
    svc = PermissionService()
    assert svc.can(guest_identity, Action.CHAT_ASK).ok
    assert not svc.can(guest_identity, Action.CHAT_DOSSIER).ok
    assert not svc.can(guest_identity, Action.CHAT_AGENTS).ok
    assert not svc.can(guest_identity, Action.VERTICAL_WRITE, "any_cell").ok


def test_disabled_user_blocked(disabled_identity):
    svc = PermissionService()
    assert not svc.can(disabled_identity, Action.CHAT_ASK).ok
    assert not svc.can(disabled_identity, Action.VERTICAL_WRITE, "any_cell").ok


def test_unauthenticated_blocked():
    svc = PermissionService()
    assert not svc.can(None, Action.CHAT_ASK).ok
