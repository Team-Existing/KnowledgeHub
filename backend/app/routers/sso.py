"""
Sign in with an identity provider (services/sso.py).

  GET    /auth/sso/providers               the configured providers (public: the sign-in page lists them)
  GET    /auth/sso/{provider}/start        browser navigation: off to the provider
  GET    /auth/sso/{provider}/callback     the provider sends the browser back here
  GET    /auth/sso/identities              the signed-in user's connected providers
  POST   /auth/sso/{provider}/link         a short-lived start URL that connects a provider to this account
  DELETE /auth/sso/identities/{provider}   disconnect (not the last way to sign in)

What happens at the callback:
  - a provider account already connected to a user signs in as that user;
  - connecting (from the Account page) attaches it to the signed-in user;
  - otherwise a new account is created, if registration is open or the
    verified email's domain belongs to an organization. An existing account is
    never taken over because an email address matches: its owner connects the
    provider while signed in.
Then the user joins the organization that claimed their verified email's
domain, if any, and the browser goes to /sso/complete with the session token
in the URL fragment (never sent to a server, and removed from the address bar).

The state, nonce and PKCE verifier travel in a short-lived signed cookie, so
any app instance can finish a sign-in another one started.
"""
from __future__ import annotations

import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urlencode

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse

from app.arcadedb import ArcadeSession
from app.auth import ALGORITHM, SECRET_KEY, get_current_user
from app.db import User, get_session
from app.repositories import UserRepository
from app.repositories.organizations import OrganizationRepository
from app.routers.auth import admin_claimed, create_user, registration_open, sign_in
from app.services import sso

router = APIRouter(prefix="/auth/sso", tags=["auth"])

COOKIE = "kh_sso"
FLOW_MINUTES = 10


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _public_url(request: Request) -> str:
    """PUBLIC_URL (what users type in their browser); else the request's own address."""
    return (os.getenv("PUBLIC_URL", "").strip() or str(request.base_url)).rstrip("/")


def _redirect_uri(request: Request, provider_id: str) -> str:
    return f"{_public_url(request)}/auth/sso/{provider_id}/callback"


def _safe_next(url: Optional[str]) -> str:
    return url if url and url.startswith("/") and not url.startswith("//") and "\\" not in url else "/knowledge"


def _fail(message: str) -> RedirectResponse:
    response = RedirectResponse(f"/login?{urlencode({'sso_error': message})}", status_code=303)
    response.delete_cookie(COOKIE, path="/auth/sso")
    return response


# ── listing ────────────────────────────────────────────────────────────────

@router.get("/providers")
async def list_providers() -> List[Dict[str, str]]:
    return sso.public_list()


@router.get("/identities")
async def my_identities(
    current_user: User = Depends(get_current_user),
    session: ArcadeSession = Depends(get_session),
) -> Dict[str, Any]:
    rows = await session.query(
        "SELECT provider, email, created_at, last_used_at FROM SsoIdentity WHERE user_id = :u ORDER BY created_at",
        {"u": current_user.id})
    names = {p["id"]: p["name"] for p in sso.public_list()}
    user = await UserRepository(session).get(current_user.id)
    return {"email": user.get("email"), "has_password": bool(user.get("hashed_password")),
            "identities": [{**r, "name": names.get(r["provider"], r["provider"])} for r in rows]}


# ── the flow ───────────────────────────────────────────────────────────────

@router.get("/{provider_id}/start")
async def start(
    provider_id: str,
    request: Request,
    next: Optional[str] = None,
    ticket: Optional[str] = None,
) -> RedirectResponse:
    """Browser navigation. `ticket` (from POST /link) connects the provider to the signed-in account."""
    try:
        provider = sso.get_provider(provider_id)
        link_user = None
        if ticket:
            try:
                claims = jwt.decode(ticket, SECRET_KEY, algorithms=[ALGORITHM], options={"require": ["exp"]})
            except jwt.PyJWTError:
                raise sso.SsoError("That link has expired. Start again from your Account page.")
            if claims.get("typ") != "sso-link" or claims.get("p") != provider_id:
                raise sso.SsoError("That link isn't valid. Start again from your Account page.")
            link_user = claims["u"]
        state, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
        verifier, challenge = sso.new_pkce()
        url = await sso.authorization_url(provider, _redirect_uri(request, provider_id), state, nonce, challenge)
    except sso.SsoError as exc:
        return _fail(str(exc))
    flow = jwt.encode({"typ": "sso-flow", "p": provider_id, "s": state, "n": nonce, "v": verifier,
                       "r": _safe_next(next), "u": link_user, "exp": _now() + timedelta(minutes=FLOW_MINUTES)},
                      SECRET_KEY, algorithm=ALGORITHM)
    response = RedirectResponse(url, status_code=303)
    # Lax: the provider's redirect back is a top-level GET, which Lax cookies accompany
    response.set_cookie(COOKIE, flow, max_age=FLOW_MINUTES * 60, path="/auth/sso", httponly=True, samesite="lax",
                        secure=_public_url(request).startswith("https://"))
    return response


@router.get("/{provider_id}/callback")
async def callback(
    provider_id: str,
    request: Request,
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
    session: ArcadeSession = Depends(get_session),
) -> RedirectResponse:
    if error:
        return _fail("Sign-in was cancelled" if error == "access_denied" else f"The sign-in provider said: {error}")
    try:
        flow = jwt.decode(request.cookies.get(COOKIE, ""), SECRET_KEY, algorithms=[ALGORITHM],
                          options={"require": ["exp"]})
    except jwt.PyJWTError:
        return _fail("The sign-in took too long or was started in another browser. Try again.")
    if flow.get("typ") != "sso-flow" or flow.get("p") != provider_id or not state \
            or not secrets.compare_digest(state, flow.get("s", "")) or not code:
        return _fail("The sign-in couldn't be verified. Try again.")
    try:
        provider = sso.get_provider(provider_id)
        id_token = await sso.exchange_code(provider, code, flow["v"], _redirect_uri(request, provider_id))
        claims = await sso.verify_id_token(provider, id_token, flow["n"])
        user = await _account_for(session, provider, claims, flow.get("u"))
    except sso.SsoError as exc:
        return _fail(str(exc))

    if flow.get("u"):          # connecting from the Account page: already signed in
        response = RedirectResponse(f"/account?{urlencode({'connected': provider.name})}", status_code=303)
    else:
        try:
            token = await sign_in(session, user)
        except HTTPException as exc:      # no usable AI model
            return _fail(str(exc.detail))
        response = RedirectResponse(f"/sso/complete#token={quote(token)}&next={quote(flow['r'])}", status_code=303)
    response.delete_cookie(COOKIE, path="/auth/sso")
    return response


async def _account_for(session: ArcadeSession, provider: sso.Provider, claims: Dict[str, Any],
                       link_user_id: Optional[str]) -> Dict[str, Any]:
    """The user this sign-in is for: connected, being connected, or new. Commits."""
    users, orgs = UserRepository(session), OrganizationRepository(session)
    subject = str(claims["sub"])
    email = sso.verified_email(provider, claims)
    domain = sso.email_domain(email)
    identity_id = sso.identity_id(provider.id, subject)
    identity = await session.query_one("SELECT user_id FROM SsoIdentity WHERE id = :id", {"id": identity_id})
    stamp = _now().isoformat()

    if link_user_id:
        if identity and identity["user_id"] != link_user_id:
            raise sso.SsoError(f"That {provider.name} account is already connected to another account")
        user = await users.get(link_user_id)
        if not user:
            raise sso.SsoError("Your session ended. Sign in and try again.")
    elif identity:
        user = await users.get(identity["user_id"])
        if not user:                       # the account was deleted; its identity row is stale
            await session.execute("DELETE FROM SsoIdentity WHERE id = :id", {"id": identity_id})
            identity = None
    if not link_user_id and not identity:
        user = await _new_account(session, provider, claims, email, domain)

    if not identity:
        await session.execute("INSERT INTO SsoIdentity CONTENT :doc", {"doc": {
            "id": identity_id, "user_id": user["id"], "provider": provider.id, "subject": subject,
            "email": email, "created_at": stamp, "last_used_at": stamp}})
    else:
        await session.execute("UPDATE SsoIdentity SET last_used_at = :t, email = :e WHERE id = :id",
                              {"t": stamp, "e": email, "id": identity_id})
    if email and not user.get("email"):
        await session.execute("UPDATE User SET email = :e WHERE id = :id", {"e": email, "id": user["id"]})
        user = {**user, "email": email}
    await orgs.join_by_domain(user["id"], domain)
    await session.commit()
    return user


async def _new_account(session: ArcadeSession, provider: sso.Provider, claims: Dict[str, Any],
                       email: Optional[str], domain: Optional[str]) -> Dict[str, Any]:
    if not await admin_claimed(session):
        raise sso.SsoError("This server's first account, its administrator, is created with a username and password")
    claimed = bool(domain and await OrganizationRepository(session).domain_owner(domain))
    if not registration_open() and not claimed:
        raise sso.SsoError(f"No account here is connected to that {provider.name} account. Sign in with your "
                           f"password and connect it on your Account page, or ask an administrator.")
    users = UserRepository(session)
    base = sso.suggested_username(claims, email)
    name = base
    for n in range(2, 200):
        if not await users.get_by_username(name):
            break
        name = f"{base[:50]}-{n}"
    try:
        return await create_user(session, name, None, extra={"email": email})
    except ValueError as exc:            # shouldn't happen: suggested_username follows the rules
        raise sso.SsoError(str(exc)) from exc


# ── connecting and disconnecting ───────────────────────────────────────────

@router.post("/{provider_id}/link")
async def link_url(provider_id: str, current_user: User = Depends(get_current_user)) -> Dict[str, str]:
    """A start URL, valid for two minutes, that connects the provider to the signed-in account."""
    if provider_id not in sso.providers():
        raise HTTPException(status_code=404, detail="That sign-in method isn't available on this server")
    ticket = jwt.encode({"typ": "sso-link", "u": current_user.id, "p": provider_id,
                         "exp": _now() + timedelta(minutes=2)}, SECRET_KEY, algorithm=ALGORITHM)
    return {"url": f"/auth/sso/{provider_id}/start?{urlencode({'ticket': ticket, 'next': '/account'})}"}


@router.delete("/identities/{provider_id}", status_code=204)
async def unlink(
    provider_id: str,
    current_user: User = Depends(get_current_user),
    session: ArcadeSession = Depends(get_session),
) -> Response:
    rows = await session.query("SELECT id, provider FROM SsoIdentity WHERE user_id = :u", {"u": current_user.id})
    target = [r for r in rows if r["provider"] == provider_id]
    if not target:
        raise HTTPException(status_code=404, detail="That sign-in method isn't connected")
    user = await UserRepository(session).get(current_user.id)
    if not user.get("hashed_password") and len(rows) <= 1:
        raise HTTPException(status_code=409, detail="It's your only way to sign in. Connect another first.")
    for r in target:
        await session.execute("DELETE FROM SsoIdentity WHERE id = :id", {"id": r["id"]})
    await session.commit()
    return Response(status_code=204)

