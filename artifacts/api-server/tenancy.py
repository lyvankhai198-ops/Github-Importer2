"""
Multi-tenant scoping machinery.

The core idea: every request (or background-worker iteration) runs with a
"current tenant" set in a contextvar. A SQLAlchemy `do_orm_execute` event
listener transparently injects a `tenant_id == current_tenant` filter into
every SELECT against a TenantScopedMixin model — including legacy
`db.query(Model)...` calls, which is what this codebase uses everywhere
(do_orm_execute fires for both the 1.x Query API and 2.0-style `select()`
in SQLAlchemy 1.4+/2.0). A companion `before_flush` listener auto-fills
`tenant_id` on any newly-created TenantScopedMixin row that doesn't already
have one set.

This means existing routers/services do NOT need to be individually
rewritten to filter by tenant_id — as long as the contextvar is set before
they run, every read and write is automatically scoped. See:
  - main.py: TenantContextMiddleware sets the contextvar from the admin
    session on every HTTP request, and enforces rental expiry.
  - Background workers / the bot process: must explicitly use
    `tenant_scope(tenant_id)` around their DB work, since there's no HTTP
    request to derive it from. Today there is only ever one active tenant
    (the owner) running the bot + background workers — see
    get_owner_tenant_id(). Running one bot/worker set per tenant is
    deferred, follow-up work (see replit.md / project tasks).
"""
import logging
from contextvars import ContextVar
from contextlib import contextmanager
from typing import Optional

from sqlalchemy import event
from sqlalchemy.orm import with_loader_criteria, Session as OrmSession

from models import TenantScopedMixin

logger = logging.getLogger(__name__)

_current_tenant_id: ContextVar[Optional[int]] = ContextVar("current_tenant_id", default=None)

# Cached id of the platform owner's AdminUser row (the original single-admin
# account before multi-tenant support existed). Used as the fallback scope
# for anything that runs outside an HTTP request (bot, background workers)
# and hasn't explicitly picked a tenant — see get_owner_tenant_id().
_owner_tenant_id_cache: Optional[int] = None


def set_current_tenant(tenant_id: Optional[int]):
    """Set the current tenant for this context (request/task). Returns a
    token to pass to `reset_current_tenant` when done."""
    return _current_tenant_id.set(tenant_id)


def reset_current_tenant(token):
    _current_tenant_id.reset(token)


def get_current_tenant() -> Optional[int]:
    tenant_id = _current_tenant_id.get()
    if tenant_id is not None:
        return tenant_id
    # Fail SAFE, not open: if nothing set a tenant explicitly (a background
    # worker that forgot to, a webhook, etc), scope to the owner tenant
    # rather than returning None (which would mean "no filter at all" and
    # leak every tenant's data).
    return get_owner_tenant_id()


@contextmanager
def tenant_scope(tenant_id: Optional[int]):
    """Context manager for background workers / the bot process to run a
    block of DB work scoped to a specific tenant."""
    token = set_current_tenant(tenant_id)
    try:
        yield
    finally:
        reset_current_tenant(token)


def get_owner_tenant_id() -> Optional[int]:
    """The platform owner's AdminUser.id — the original admin account.
    Cached after first lookup (it never changes at runtime)."""
    global _owner_tenant_id_cache
    if _owner_tenant_id_cache is not None:
        return _owner_tenant_id_cache
    from database import SessionLocal
    from models import AdminUser
    db = SessionLocal()
    try:
        # execution_options(skip_tenant_filter=True): AdminUser isn't even a
        # TenantScopedMixin subclass, but resolving the owner id is *itself*
        # called from inside the do_orm_execute filter (as the fallback when
        # no tenant is set yet) — without this the query below would
        # re-trigger the same filter -> call get_current_tenant() again ->
        # infinite recursion before the cache is populated.
        owner = (
            db.query(AdminUser)
            .execution_options(skip_tenant_filter=True)
            .filter(AdminUser.is_owner == True)
            .order_by(AdminUser.id.asc())
            .first()
        )
        if not owner:
            # Pre-multi-tenant DB: fall back to the very first admin account.
            owner = (
                db.query(AdminUser)
                .execution_options(skip_tenant_filter=True)
                .order_by(AdminUser.id.asc())
                .first()
            )
        if owner:
            _owner_tenant_id_cache = owner.id
            return owner.id
    finally:
        db.close()
    return None


def _register_events():
    @event.listens_for(OrmSession, "do_orm_execute")
    def _apply_tenant_filter(orm_execute_state):
        if not orm_execute_state.is_select:
            return
        if orm_execute_state.execution_options.get("skip_tenant_filter"):
            return
        tenant_id = get_current_tenant()
        orm_execute_state.statement = orm_execute_state.statement.options(
            with_loader_criteria(
                TenantScopedMixin,
                lambda cls: cls.tenant_id == tenant_id,
                include_aliases=True,
            )
        )

    @event.listens_for(OrmSession, "before_flush")
    def _assign_tenant_on_insert(session, flush_context, instances):
        tenant_id = _current_tenant_id.get()
        if tenant_id is None:
            tenant_id = get_owner_tenant_id()
        for obj in session.new:
            if isinstance(obj, TenantScopedMixin) and getattr(obj, "tenant_id", None) is None:
                obj.tenant_id = tenant_id

    logger.info("TENANCY_EVENTS_REGISTERED")


_register_events()
