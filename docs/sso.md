# Single sign-on

People can sign in with **Google**, **Microsoft** or any other **OpenID Connect** provider (Okta, Keycloak, Auth0 …) instead of a password, and organizations can let everyone at their company's email domain join automatically.

## Setting it up

1. In the provider's console, create an OAuth / OpenID Connect web application. Set its redirect URI to:

   ```
   https://<your server>/auth/sso/<id>/callback
   ```

   where `<id>` is the name you give the provider below, for example `google`.

2. Configure the server:

   | Variable | Example | Meaning |
   |---|---|---|
   | `PUBLIC_URL` | `https://hub.example.com` | The address people use, used to build redirect URIs. Set it behind a proxy; without it, the request's own address is used |
   | `SSO_PROVIDERS` | `google,microsoft` | The providers to offer, in order |
   | `SSO_<ID>_CLIENT_ID`, `SSO_<ID>_CLIENT_SECRET` | | From the provider's console |
   | `SSO_<ID>_ISSUER` | `https://acme.okta.com` | Needed for providers other than `google` and `microsoft` |
   | `SSO_<ID>_NAME` | `Okta` | The button text, *Continue with …*. Defaults to `Google`, `Microsoft`, or the id |

   For `microsoft`, the default issuer accepts work and school accounts from any Microsoft Entra tenant, and personal accounts. To admit a single tenant, set `SSO_MICROSOFT_ISSUER=https://login.microsoftonline.com/<tenant id>/v2.0`.

A provider missing its client id, secret or issuer is skipped, with a warning in the log.

## What happens when someone signs in

- **A provider account that's already connected** signs in as its user.
- **A new person** gets a new account. Its name comes from their email (`ana@acme.com` becomes `ana`, or `ana-2` if that's taken). The account has no password, so it signs in only through the provider.
  - **Closed registration:** with `ALLOW_REGISTRATION=false`, new accounts are only created for people whose verified email domain an organization has claimed. Everyone else is refused.
  - **First account:** the server's first account, its administrator, is always created with a username, a password and the setup token, never through SSO.
- **No account is taken over because an email address matches.** Someone who already has a password account connects the provider on their **Account** page while signed in. Linking accounts by email alone would let anyone who can get a provider to vouch for an address sign in as that address's owner.

After signing in, the browser returns to the page the person was heading for, for example an organization invite link.

## Verified email addresses

An email address counts as verified only when the provider vouches for it:
- the `email_verified` claim, for Google and most providers
- Microsoft's `xms_edov` claim (*email domain owner verified*)

Microsoft lets each tenant's administrators put any address in the plain `email` claim, so that claim alone proves nothing. To get `xms_edov`, add it as an optional claim in the app registration.

Only verified addresses are stored on the account (shown on the **Account** page) and used for domains.

## Joining an organization by email domain

On the **Organizations** page, an owner can let everyone with a verified address at their company's domain join automatically. The rules:

- **Owner's own domain only:** an owner can claim only the domain of their own verified email. That means they've signed in with SSO at least once.
- **No public mail services:** gmail.com, outlook.com and the like can't be claimed, because anyone can have an address there.
- **One organization per domain:** a domain claimed by one organization can't be claimed by another.
- **Joining:** anyone who then signs in with SSO and a verified address at the domain joins as a member. That includes people signing in for the first time.
- **No re-adding:** someone who left or was removed isn't added back. They need an invite link.
- **Removing a domain** stops new people joining; those who already joined stay.

## Security details

- **Protocol:** the authorization code flow with **PKCE**, a `state` value and a `nonce`.
- **Login state:** the state, nonce and PKCE verifier are kept in a signed, HttpOnly, SameSite=Lax cookie scoped to `/auth/sso`, valid for 10 minutes. Any app instance can finish a sign-in another one started.
- **Token checks:** the ID token's signature is checked against the provider's published keys, which are re-fetched when a new key appears. Its issuer, audience, expiry and nonce are checked too.
- **Session token:** the app's session token reaches the browser in the URL fragment of `/sso/complete`. Browsers never send the fragment to a server, and the page removes it from the address bar and history immediately.
- **Connecting a provider** to an existing account uses a ticket that's valid for two minutes and works for that one provider only.
- **Disconnecting:** a provider can be disconnected unless it's the account's only way to sign in.
