# Accounts, organizations and spaces

**Every account is a normal user.** People work together in **organizations**: each has a shared space and groups inside it. Separately, the server itself has admins.

| | Who | What they can do |
|---|---|---|
| **Server admin** | The first account created on the server, plus anyone a server admin promotes | Run the server: create accounts and change server roles (**Users** page), install or remove models on the shared Ollama, use folder and git-ADR connectors (they read the server's disk), see and run the inactivity clean-up |
| **Organization owner** | Whoever creates an organization, plus anyone an owner promotes | Everything in the organization: rename it, change anyone's role, invite admins and members, remove anyone, delete the organization |
| **Organization admin** | Promoted by an owner, or joined through an admin invite link | Rename the organization, create and revoke member invite links, remove members |
| **Group admin** | Whoever creates a group | Invite the organization's members to the group, remove them, rename or delete the group |

People can also sign in with Google, Microsoft or another OpenID Connect provider, and organizations can admit everyone at their email domain: see [Single sign-on](sso.md). Set `ALLOW_REGISTRATION=false` to make server admins create every account. Usernames are unique ignoring case (`Alice` and `alice` are the same name). New names use letters, digits, `.`, `_` and `-`, and are 3 to 60 characters long.

## Spaces

- **Your personal space** is private: only you can see it.
- **Each organization you're in has a space** everyone in it shares.
- **Each group you join has its own space**, shared only with the group's members.
- **What a shared space covers:** sources, knowledge items, reviews, decisions and their history, playbooks, connectors, search and GraphRAG. Item history records who did what.
- **The switcher at the top** picks the active space. It lists your space, then each organization with your groups in it. A banner shows when you're working in a shared space.
- **Share a source into another space:** on an item's page, **Share this source to…** copies its source into another space you belong to, with all its knowledge items, review state and statuses. Sharing again updates the copy instead of duplicating it.

## Organizations

- **Creating:** anyone signed in can create one on the **Organizations** page and becomes its owner.
- **Inviting:** owners and admins create an **invite link** and send it to the people they want.
  - **Settings:** who joins as a member (or as an admin, owners only), how long the link works (1, 7 or 30 days), and optionally how many people can use it.
  - **Shown once:** the link appears only when it's created. Only a hash of its token is stored, so nobody can read it back later, a server admin included.
  - **Revoking:** a link can be revoked at any time.
  - **Joining:** whoever opens the link signs in or creates an account, sees which organization it is, and joins.
- **Roles:**
  - Owners change roles.
  - Owners remove anyone. Admins remove members.
  - An organization always keeps at least one owner. The last owner can't be demoted, removed or leave: they make someone else an owner first, or delete the organization.
- **Leaving or being removed** also ends your memberships of the organization's groups.
  - What you added stays with the organization.
  - Groups you administered pass to their longest-standing remaining member. If nobody else is in such a group, it passes to an owner.
  - Someone who was removed can't rejoin through a link created before their removal. They need a new link. Someone who left can rejoin with any valid link.
- **Deleting** an organization (owners only, after typing its name) deletes its space, all its groups and everything in them, for every member.

Server admins don't see inside organizations they aren't members of.

## Groups

- **Creating:** any member of an organization can create a group in it on the **Groups** page, and becomes its admin.
- **Inviting:** the group admin searches the organization's members by name and invites them. Only members of the same organization can be found or invited; nobody can browse the server's accounts. The invited user sees a badge on **Groups** and accepts or declines. Until they accept, they have no access.
- **Managing (group admin):** rename the group, remove members or cancel invitations (what a removed member added stays in the group), or delete the group, which deletes its space for everyone.
- **Leaving:** members can leave. The group admin can't; they delete the group instead.

## Upgrading from before organizations

Before organizations, every account on a server could find and invite every other. On upgrade, the server keeps that arrangement:
- All existing accounts join one organization, "Our organization". Server admins become its owners.
- Every existing group moves into it.

Owners can rename it. A new server starts with no organization.

**How it works:** the frontend sends the active space in an `X-Space` header. The backend ([`backend/app/spaces.py`](../backend/app/spaces.py)) checks that you're an active member, of the organization, or of the group *and* its organization, then scopes every query to that space. A request for a space you're not in, or for another user's personal space, gets a 403.
