"""Single sign-on: the OpenID Connect flow against a simulated provider, accounts, connecting, domains."""
from __future__ import annotations

import json
import time
import uuid
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, unquote, urlparse

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.services import sso
from tests.helpers import create_org

ISSUER = "https://idp.example.test"
CLIENT_ID = "kh-client"


def _jwk(key, kid: str) -> Dict[str, Any]:
    data = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    return {**data, "kid": kid, "alg": "RS256", "use": "sig"}


class FakeIdP:
    """An OpenID provider: discovery, keys, and a token endpoint answering with whatever ID token a test sets."""

    def __init__(self) -> None:
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.issuer = ISSUER
        self.discovery_issuer = ISSUER
        self.claims: Dict[str, Any] = {}
        self.signing_key = self.key
        self.token_requests: list = []

    def metadata(self, url: str) -> Dict[str, Any]:
        if url.endswith("/.well-known/openid-configuration"):
            return {"issuer": self.discovery_issuer, "authorization_endpoint": f"{ISSUER}/authorize",
                    "token_endpoint": f"{ISSUER}/token", "jwks_uri": f"{ISSUER}/jwks"}
        if url == f"{ISSUER}/jwks":
            return {"keys": [_jwk(self.key, "k1")]}
        raise AssertionError(url)

    def id_token(self, nonce: str) -> str:
        now = int(time.time())
        claims = {"iss": self.issuer, "aud": CLIENT_ID, "iat": now, "exp": now + 300, "nonce": nonce,
                  "sub": "subject-1", "email": "ana@acme-corp.test", "email_verified": True, **self.claims}
        return jwt.encode({k: v for k, v in claims.items() if v is not None}, self.signing_key,
                          algorithm="RS256", headers={"kid": "k1"})


@pytest.fixture
def idp(monkeypatch):
    provider = FakeIdP()
    monkeypatch.setenv("SSO_PROVIDERS", "acme,broken")
    monkeypatch.setenv("SSO_ACME_ISSUER", ISSUER)
    monkeypatch.setenv("SSO_ACME_NAME", "Acme ID")
    monkeypatch.setenv("SSO_ACME_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("SSO_ACME_CLIENT_SECRET", "shh")
    monkeypatch.delenv("PUBLIC_URL", raising=False)
    sso.forget_metadata()
    nonces: Dict[str, str] = {}

    async def get_json(url):
        return provider.metadata(url)

    async def post_form(url, data):
        provider.token_requests.append(data)
        assert url == f"{ISSUER}/token" and data["client_secret"] == "shh" and data["code_verifier"]
        return 200, {"id_token": provider.id_token(nonces[data["code"]])}

    monkeypatch.setattr(sso, "_get_json", get_json)
    monkeypatch.setattr(sso, "_post_form", post_form)
    provider.nonces = nonces
    yield provider
    sso.forget_metadata()


def start(client, url="/auth/sso/acme/start?next=/join/abc") -> Dict[str, str]:
    r = client.get(url, follow_redirects=False)
    assert r.status_code == 303, r.text
    location = urlparse(r.headers["location"])
    assert location.netloc == "idp.example.test", r.headers["location"]
    return {k: v[0] for k, v in parse_qs(location.query).items()}


def finish(client, idp, params, code: Optional[str] = None, state: Optional[str] = None):
    code = code or f"code-{uuid.uuid4().hex[:8]}"
    idp.nonces[code] = params["nonce"]
    return client.get("/auth/sso/acme/callback", params={"code": code, "state": state or params["state"]},
                      follow_redirects=False)


def sign_in(client, idp, **claims) -> Dict[str, str]:
    """The whole flow; returns auth headers."""
    idp.claims = claims
    r = finish(client, idp, start(client))
    assert r.status_code == 303, r.text
    location = r.headers["location"]
    assert location.startswith("/sso/complete#"), location
    fragment = parse_qs(location.split("#", 1)[1])
    return {"Authorization": f"Bearer {fragment['token'][0]}"}


def error_of(r) -> str:
    assert r.status_code == 303
    location = urlparse(r.headers["location"])
    assert location.path == "/login", r.headers["location"]
    return parse_qs(location.query)["sso_error"][0]


def unique_email(domain="acme-corp.test") -> str:
    return f"u{uuid.uuid4().hex[:8]}@{domain}"


# ── the flow ─────────────────────────────────────────────────────────────────

def test_providers_are_listed_publicly_and_misconfigured_ones_skipped(client, idp):
    assert client.get("/auth/sso/providers").json() == [{"id": "acme", "name": "Acme ID"}]


def test_start_sends_the_browser_to_the_provider_with_pkce(client, idp):
    r = client.get("/auth/sso/acme/start?next=/join/abc", follow_redirects=False)
    params = {k: v[0] for k, v in parse_qs(urlparse(r.headers["location"]).query).items()}
    assert params["client_id"] == CLIENT_ID and params["response_type"] == "code"
    assert params["redirect_uri"] == "http://testserver/auth/sso/acme/callback"
    assert params["code_challenge_method"] == "S256" and params["state"] and params["nonce"]
    cookie = r.headers["set-cookie"]
    assert "kh_sso=" in cookie and "HttpOnly" in cookie and "Path=/auth/sso" in cookie and "samesite=lax" in cookie.lower()


def test_first_sign_in_creates_an_account_with_the_verified_email(client, idp):
    email = unique_email()
    headers = sign_in(client, idp, sub=f"s-{uuid.uuid4()}", email=email)
    me = client.get("/auth/me", headers=headers).json()
    assert me["email"] == email and me["username"] == email.split("@")[0] and me["role"] == "member"
    # no password: it can't be guessed into
    assert client.post("/auth/token", data={"username": me["username"], "password": ""}).status_code in (401, 422)
    assert client.get("/auth/sso/identities", headers=headers).json()["has_password"] is False


def test_signing_in_again_is_the_same_account_and_the_next_page_is_kept(client, idp):
    sub = f"s-{uuid.uuid4()}"
    first = client.get("/auth/me", headers=sign_in(client, idp, sub=sub, email=unique_email())).json()
    idp.claims = {"sub": sub, "email": unique_email()}
    r = finish(client, idp, start(client, "/auth/sso/acme/start?next=/join/xyz"))
    fragment = parse_qs(r.headers["location"].split("#", 1)[1])
    assert unquote(fragment["next"][0]) == "/join/xyz"
    again = client.get("/auth/me", headers={"Authorization": f"Bearer {fragment['token'][0]}"}).json()
    assert again["id"] == first["id"]


def test_next_page_must_stay_in_the_app(client, idp):
    idp.claims = {"sub": f"s-{uuid.uuid4()}", "email": unique_email()}
    r = finish(client, idp, start(client, "/auth/sso/acme/start?next=//evil.example"))
    assert parse_qs(r.headers["location"].split("#", 1)[1])["next"][0] == "/knowledge"


def test_an_existing_account_is_never_taken_over_by_a_matching_email(client, idp, alice, db_query):
    alice_id = client.get("/auth/me", headers=alice).json()["id"]
    email = unique_email()
    db_query("UPDATE User SET email = :e WHERE id = :id", e=email, id=alice_id)
    someone = client.get("/auth/me", headers=sign_in(client, idp, sub=f"s-{uuid.uuid4()}", email=email)).json()
    assert someone["id"] != alice_id


@pytest.mark.parametrize("tamper, message", [
    ({"aud": "someone-else"}, "couldn't be verified"),
    ({"iss": "https://other-issuer.test"}, "wrong issuer"),
    ({"exp": int(time.time()) - 3600}, "couldn't be verified"),
    ({"nonce": "replayed"}, "replayed"),
])
def test_tampered_id_tokens_are_refused(client, idp, tamper, message):
    idp.claims = {"sub": f"s-{uuid.uuid4()}", **tamper}      # tamper overrides what the provider would sign
    assert message in error_of(finish(client, idp, start(client)))


def test_a_token_signed_by_another_key_is_refused(client, idp):
    idp.signing_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    idp.claims = {"sub": f"s-{uuid.uuid4()}"}
    assert "couldn't be verified" in error_of(finish(client, idp, start(client)))


def test_state_and_cookie_are_checked(client, idp):
    idp.claims = {"sub": f"s-{uuid.uuid4()}"}
    params = start(client)
    assert "couldn't be verified" in error_of(finish(client, idp, params, state="forged"))
    fresh = start(client)
    client.cookies.clear()                                      # e.g. finished in another browser
    assert "another browser" in error_of(finish(client, idp, fresh))
    assert "cancelled" in error_of(client.get("/auth/sso/acme/callback?error=access_denied", follow_redirects=False))
    assert "isn't available" in error_of(client.get("/auth/sso/nope/start", follow_redirects=False))


def test_unverified_emails_are_not_stored(client, idp):
    headers = sign_in(client, idp, sub=f"s-{uuid.uuid4()}", email=unique_email(), email_verified=False)
    assert client.get("/auth/me", headers=headers).json()["email"] is None


def test_microsoft_style_multi_tenant_issuer_and_edov(client, idp):
    idp.discovery_issuer = "https://login.example.test/{tenantid}/v2.0"
    idp.issuer = "https://login.example.test/tenant-42/v2.0"
    email = unique_email()
    headers = sign_in(client, idp, sub=f"s-{uuid.uuid4()}", tid="tenant-42", email=email,
                      email_verified=None, xms_edov=True)
    assert client.get("/auth/me", headers=headers).json()["email"] == email
    idp.issuer = "https://login.example.test/another-tenant/v2.0"
    idp.claims = {"sub": f"s-{uuid.uuid4()}", "tid": "tenant-42"}
    assert "wrong issuer" in error_of(finish(client, idp, start(client)))


def test_no_usable_model_ends_the_sign_in_with_the_reason(client, idp, ollama):
    ollama.reachable = False
    idp.claims = {"sub": f"s-{uuid.uuid4()}", "email": unique_email()}
    assert "Ollama" in error_of(finish(client, idp, start(client)))


def test_the_first_account_is_never_created_with_sso(client, idp, monkeypatch):
    import app.routers.sso as sso_router

    async def unclaimed(session):
        return False
    monkeypatch.setattr(sso_router, "admin_claimed", unclaimed)
    idp.claims = {"sub": f"s-{uuid.uuid4()}"}
    assert "administrator" in error_of(finish(client, idp, start(client)))


def test_closed_registration_refuses_unknown_accounts(client, idp, monkeypatch):
    monkeypatch.setenv("ALLOW_REGISTRATION", "false")
    idp.claims = {"sub": f"s-{uuid.uuid4()}", "email": unique_email("elsewhere.test")}
    assert "No account here is connected" in error_of(finish(client, idp, start(client)))


# ── connecting to an existing account ───────────────────────────────────────

def connect(client, idp, headers, **claims):
    url = client.post("/auth/sso/acme/link", headers=headers).json()["url"]
    assert url.startswith("/auth/sso/acme/start?ticket=")
    idp.claims = claims
    return finish(client, idp, start(client, url))


def test_connecting_a_provider_then_signing_in_with_it(client, idp, alice):
    sub = f"s-{uuid.uuid4()}"
    r = connect(client, idp, alice, sub=sub, email=unique_email())
    assert r.status_code == 303 and r.headers["location"].startswith("/account?connected=")
    mine = client.get("/auth/sso/identities", headers=alice).json()
    assert [(i["provider"], i["name"]) for i in mine["identities"]] == [("acme", "Acme ID")] and mine["has_password"]
    alice_id = client.get("/auth/me", headers=alice).json()["id"]
    assert client.get("/auth/me", headers=sign_in(client, idp, sub=sub)).json()["id"] == alice_id


def test_a_provider_account_connects_to_one_account_only(client, idp, alice, bob):
    sub = f"s-{uuid.uuid4()}"
    connect(client, idp, alice, sub=sub)
    assert "another account" in error_of(connect(client, idp, bob, sub=sub))


def test_link_tickets_are_short_lived_and_bound_to_the_provider(client, idp, alice):
    from app.auth import ALGORITHM, SECRET_KEY
    stale = jwt.encode({"typ": "sso-link", "u": "x", "p": "acme", "exp": int(time.time()) - 10}, SECRET_KEY,
                       algorithm=ALGORITHM)
    assert "expired" in error_of(client.get(f"/auth/sso/acme/start?ticket={stale}", follow_redirects=False))
    other = jwt.encode({"typ": "sso-link", "u": "x", "p": "other", "exp": int(time.time()) + 60}, SECRET_KEY,
                       algorithm=ALGORITHM)
    assert "isn't valid" in error_of(client.get(f"/auth/sso/acme/start?ticket={other}", follow_redirects=False))
    assert client.post("/auth/sso/nope/link", headers=alice).status_code == 404


def test_disconnecting_keeps_a_way_to_sign_in(client, idp, alice):
    sso_only = sign_in(client, idp, sub=f"s-{uuid.uuid4()}", email=unique_email())
    assert client.delete("/auth/sso/identities/acme", headers=sso_only).status_code == 409
    connect(client, idp, alice, sub=f"s-{uuid.uuid4()}")
    assert client.delete("/auth/sso/identities/acme", headers=alice).status_code == 204       # alice has a password
    assert client.get("/auth/sso/identities", headers=alice).json()["identities"] == []
    assert client.delete("/auth/sso/identities/acme", headers=alice).status_code == 404


# ── organizations by email domain ───────────────────────────────────────────

def domain_owner(client, idp, domain):
    """An SSO user with a verified address at `domain`, owning a new organization."""
    owner = sign_in(client, idp, sub=f"s-{uuid.uuid4()}", email=unique_email(domain))
    return owner, create_org(client, owner, name=f"Org {domain}")


def test_owners_claim_their_own_domain_and_colleagues_join_on_sign_in(client, idp, db_query):
    domain = f"{uuid.uuid4().hex[:8]}.test"
    owner, org = domain_owner(client, idp, domain)
    r = client.post(f"/organizations/{org}/domains", headers=owner, json={"domain": domain.upper()})
    assert r.status_code == 201 and r.json() == {"domain": domain}
    assert [d["domain"] for d in client.get(f"/organizations/{org}/domains", headers=owner).json()] == [domain]

    colleague = sign_in(client, idp, sub=f"s-{uuid.uuid4()}", email=unique_email(domain))
    assert {o["id"]: o["role"] for o in client.get("/organizations", headers=colleague).json()}[org] == "member"
    # an unverified address at the domain doesn't join
    stranger = sign_in(client, idp, sub=f"s-{uuid.uuid4()}", email=unique_email(domain), email_verified=False)
    assert client.get("/organizations", headers=stranger).json() == []


def test_someone_removed_is_not_pulled_back_in_by_their_domain(client, idp):
    domain = f"{uuid.uuid4().hex[:8]}.test"
    owner, org = domain_owner(client, idp, domain)
    client.post(f"/organizations/{org}/domains", headers=owner, json={"domain": domain})
    sub = f"s-{uuid.uuid4()}"
    colleague = sign_in(client, idp, sub=sub, email=unique_email(domain))
    colleague_id = client.get("/auth/me", headers=colleague).json()["id"]
    client.delete(f"/organizations/{org}/members/{colleague_id}", headers=owner)
    again = sign_in(client, idp, sub=sub, email=unique_email(domain))
    assert client.get("/organizations", headers=again).json() == []


def test_closed_registration_still_admits_a_claimed_domain(client, idp, monkeypatch):
    domain = f"{uuid.uuid4().hex[:8]}.test"
    owner, org = domain_owner(client, idp, domain)
    client.post(f"/organizations/{org}/domains", headers=owner, json={"domain": domain})
    monkeypatch.setenv("ALLOW_REGISTRATION", "false")
    newcomer = sign_in(client, idp, sub=f"s-{uuid.uuid4()}", email=unique_email(domain))
    assert [o["id"] for o in client.get("/organizations", headers=newcomer).json()] == [org]


def test_domain_claims_are_limited(client, idp, alice, bob):
    domain = f"{uuid.uuid4().hex[:8]}.test"
    owner, org = domain_owner(client, idp, domain)
    # not your own domain
    assert client.post(f"/organizations/{org}/domains", headers=owner, json={"domain": "other.test"}).status_code == 403
    # no verified email: password-only owners can't claim anything
    alice_org = create_org(client, alice, bob)
    assert client.post(f"/organizations/{alice_org}/domains", headers=alice, json={"domain": domain}).status_code == 403
    # members and admins can't; another organization can't take a claimed domain
    client.post(f"/organizations/{org}/domains", headers=owner, json={"domain": domain})
    second = create_org(client, owner, name="Second")
    assert client.post(f"/organizations/{second}/domains", headers=owner, json={"domain": domain}).status_code == 409
    assert client.post(f"/organizations/{alice_org}/domains", headers=bob, json={"domain": domain}).status_code == 403
    # public mail services
    gmail_owner, gmail_org = domain_owner(client, idp, "gmail.com")
    r = client.post(f"/organizations/{gmail_org}/domains", headers=gmail_owner, json={"domain": "gmail.com"})
    assert r.status_code == 400 and "public" in r.json()["detail"]
    # removing
    assert client.delete(f"/organizations/{org}/domains/{domain}", headers=owner).status_code == 204
    assert client.get(f"/organizations/{org}/domains", headers=owner).json() == []
