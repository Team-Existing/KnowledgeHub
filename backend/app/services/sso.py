"""
Single sign-on with OpenID Connect (authorization code flow with PKCE).

Providers are configured by the server operator:

  SSO_PROVIDERS=google,microsoft
  SSO_GOOGLE_CLIENT_ID=…   SSO_GOOGLE_CLIENT_SECRET=…
  SSO_MICROSOFT_CLIENT_ID=… SSO_MICROSOFT_CLIENT_SECRET=…

`google` and `microsoft` know their issuer; any other OpenID Connect provider
(Okta, Keycloak, Auth0 …) needs SSO_<ID>_ISSUER, and optionally SSO_<ID>_NAME.

An email address only counts as verified when the provider says so:
`email_verified` (Google, most providers), or Microsoft's `xms_edov` claim
(Microsoft lets tenant admins set any address, so its plain `email` claim
proves nothing). Only verified addresses are stored, and only they are used
to join organizations by domain.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os
import secrets
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

import httpx
import jwt

logger = logging.getLogger(__name__)

_KNOWN = {
    "google": ("Google", "https://accounts.google.com"),
    "microsoft": ("Microsoft", "https://login.microsoftonline.com/common/v2.0"),
}
_DISCOVERY_TTL = 3600.0
_ALGORITHMS = ["RS256", "RS384", "RS512", "ES256", "ES384", "PS256"]


class SsoError(Exception):
    """A sign-in that can't complete; the message is shown to the user."""


@dataclass(frozen=True)
class Provider:
    id: str
    name: str
    issuer: str
    client_id: str
    client_secret: str


def providers() -> Dict[str, Provider]:
    """The configured providers, by id. One missing its client id, secret or issuer is skipped (logged)."""
    out: Dict[str, Provider] = {}
    for pid in [p.strip().lower() for p in os.getenv("SSO_PROVIDERS", "").split(",") if p.strip()]:
        env = pid.upper().replace("-", "_")
        known_name, known_issuer = _KNOWN.get(pid, (pid.title(), ""))
        provider = Provider(
            id=pid,
            name=os.getenv(f"SSO_{env}_NAME", "").strip() or known_name,
            issuer=(os.getenv(f"SSO_{env}_ISSUER", "").strip() or known_issuer).rstrip("/"),
            client_id=os.getenv(f"SSO_{env}_CLIENT_ID", "").strip(),
            client_secret=os.getenv(f"SSO_{env}_CLIENT_SECRET", "").strip(),
        )
        if not (provider.issuer and provider.client_id and provider.client_secret):
            logger.warning("SSO provider '%s' needs SSO_%s_CLIENT_ID, SSO_%s_CLIENT_SECRET%s; skipped",
                           pid, env, env, "" if known_issuer else f" and SSO_{env}_ISSUER")
            continue
        out[pid] = provider
    return out


def get_provider(provider_id: str) -> Provider:
    provider = providers().get(provider_id)
    if not provider:
        raise SsoError("That sign-in method isn't available on this server")
    return provider


# ── provider metadata ────────────────────────────────────────────────────────

_cache: Dict[str, tuple[float, Any]] = {}


async def _get_json(url: str) -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(url)
        r.raise_for_status()
        return r.json()


async def _post_form(url: str, data: Dict[str, str]) -> tuple[int, Dict[str, Any]]:
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(url, data=data, headers={"Accept": "application/json"})
        try:
            return r.status_code, r.json()
        except ValueError:
            return r.status_code, {"error": r.text[:300]}


async def _cached(url: str, refresh: bool = False) -> Dict[str, Any]:
    hit = _cache.get(url)
    if hit and not refresh and hit[0] > time.monotonic():
        return hit[1]
    try:
        value = await _get_json(url)
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("SSO: could not fetch %s: %s", url, exc)
        raise SsoError("The sign-in provider can't be reached right now. Try again shortly.") from exc
    _cache[url] = (time.monotonic() + _DISCOVERY_TTL, value)
    return value


async def discovery(provider: Provider) -> Dict[str, Any]:
    return await _cached(f"{provider.issuer}/.well-known/openid-configuration")


def forget_metadata() -> None:
    _cache.clear()


# ── the flow ─────────────────────────────────────────────────────────────────

def new_pkce() -> tuple[str, str]:
    """(verifier, S256 challenge)"""
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


async def authorization_url(provider: Provider, redirect_uri: str, state: str, nonce: str, challenge: str) -> str:
    meta = await discovery(provider)
    params = {"response_type": "code", "client_id": provider.client_id, "redirect_uri": redirect_uri,
              "scope": "openid email profile", "state": state, "nonce": nonce,
              "code_challenge": challenge, "code_challenge_method": "S256", "prompt": "select_account"}
    return f"{meta['authorization_endpoint']}?{urlencode(params)}"


async def exchange_code(provider: Provider, code: str, verifier: str, redirect_uri: str) -> str:
    """The ID token for an authorization code."""
    meta = await discovery(provider)
    try:
        status, body = await _post_form(meta["token_endpoint"], {
            "grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri,
            "client_id": provider.client_id, "client_secret": provider.client_secret, "code_verifier": verifier,
        })
    except (httpx.HTTPError, ValueError) as exc:
        raise SsoError("The sign-in provider can't be reached right now. Try again shortly.") from exc
    if status != 200:
        logger.warning("SSO %s: token endpoint answered HTTP %d: %s", provider.id, status, str(body)[:300])
        raise SsoError("The sign-in provider didn't accept the sign-in. Try again.")
    id_token = (body or {}).get("id_token")
    if not id_token:
        raise SsoError("The sign-in provider didn't return an identity")
    return id_token


async def _signing_key(provider: Provider, kid: Optional[str]) -> Any:
    meta = await discovery(provider)
    for refresh in (False, True):            # a key we haven't seen: the provider may have rotated its keys
        keys = (await _cached(meta["jwks_uri"], refresh=refresh)).get("keys", [])
        for jwk in keys:
            if kid is None or jwk.get("kid") == kid:
                return jwt.PyJWK(jwk).key
    raise SsoError("The sign-in couldn't be verified (unknown signing key)")


async def verify_id_token(provider: Provider, id_token: str, nonce: str) -> Dict[str, Any]:
    """The token's claims, after checking its signature, issuer, audience, expiry and nonce."""
    try:
        header = jwt.get_unverified_header(id_token)
        if header.get("alg") not in _ALGORITHMS:
            raise SsoError("The sign-in couldn't be verified (unsupported signature)")
        key = await _signing_key(provider, header.get("kid"))
        claims = jwt.decode(id_token, key, algorithms=_ALGORITHMS, audience=provider.client_id,
                            options={"require": ["exp", "iat", "sub", "iss", "aud"], "verify_iss": False},
                            leeway=60)
    except jwt.PyJWTError as exc:
        logger.warning("SSO %s: rejected ID token: %s", provider.id, exc)
        raise SsoError("The sign-in couldn't be verified") from exc
    expected = (await discovery(provider))["issuer"]
    # Microsoft's multi-tenant metadata names the issuer with a {tenantid} placeholder
    if "{tenantid}" in expected:
        expected = expected.replace("{tenantid}", str(claims.get("tid", "")))
    if claims.get("iss") != expected:
        logger.warning("SSO %s: issuer %r, expected %r", provider.id, claims.get("iss"), expected)
        raise SsoError("The sign-in couldn't be verified (wrong issuer)")
    if not nonce or not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
        raise SsoError("The sign-in couldn't be verified (replayed or mixed-up sign-in)")
    return claims


def _true(value: Any) -> bool:
    return value is True or (isinstance(value, str) and value.lower() == "true")


def verified_email(provider: Provider, claims: Dict[str, Any]) -> Optional[str]:
    """The email address, lower-cased, only if the provider vouches for it."""
    email = str(claims.get("email") or "").strip().lower()
    if not email or "@" not in email:
        return None
    if _true(claims.get("email_verified")) or _true(claims.get("xms_edov")):
        return email
    return None


def email_domain(email: Optional[str]) -> Optional[str]:
    return email.rsplit("@", 1)[1] if email and "@" in email else None


# Mail services anyone can sign up to: owning an address there says nothing about
# belonging to a company, so organizations can't claim them for auto-join.
PUBLIC_EMAIL_DOMAINS = frozenset("""
gmail.com googlemail.com outlook.com hotmail.com live.com msn.com yahoo.com yahoo.co.uk ymail.com
icloud.com me.com mac.com aol.com proton.me protonmail.com pm.me gmx.com gmx.de gmx.net web.de
mail.com zoho.com yandex.com yandex.ru mail.ru qq.com 163.com 126.com fastmail.com hey.com tutanota.com
""".split())


def suggested_username(claims: Dict[str, Any], email: Optional[str]) -> str:
    """A starting point for a new account's name: the email's local part, else the name, else the provider's id."""
    import re
    raw = (email or "").split("@")[0] or str(claims.get("preferred_username") or "").split("@")[0] \
        or str(claims.get("name") or "")
    name = re.sub(r"[^A-Za-z0-9._-]+", ".", raw).strip("._-")[:50]
    if len(name) < 3:
        name = f"user-{hashlib.sha256(str(claims.get('sub')).encode()).hexdigest()[:8]}"
    return name


def identity_id(provider_id: str, subject: str) -> str:
    return "sso_" + hashlib.sha256(f"{provider_id}:{subject}".encode()).hexdigest()[:24]


def public_list() -> List[Dict[str, str]]:
    return [{"id": p.id, "name": p.name} for p in providers().values()]
