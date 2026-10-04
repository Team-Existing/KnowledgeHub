# Accounts, roles and spaces

**Every account is a normal user.** On top of that there are two separate kinds of "admin":

| | Who | What they can do |
|---|---|---|
| **Server admin** | The first account created on the server, plus anyone a server admin promotes | Create accounts and change roles (**Users** page), install or remove models on the shared Ollama, use folder and git-ADR connectors (they read the server's disk), see and run the inactivity clean-up |
| **Group admin** | Whoever creates a group | Invite and remove members, rename or delete that group |

Set `ALLOW_REGISTRATION=false` to make server admins create every account. Usernames are unique ignoring case (`Alice` and `alice` are the same name); new names use letters, digits, `.`, `_` and `-`, 3 to 60 characters.

## Spaces

- **Your personal space** is private: only you can see it.
- **Each group you join has its own space.** Everyone in it can view and work on everything there: sources, knowledge items, reviews, decisions and their history, playbooks, connectors, search and GraphRAG. Item history records who did what.
- **The switcher at the top** picks the active space, and a banner shows when you're working in a group.
- **Share a source into a group:** on an item's page, **Share this source to…** copies its source, with all its knowledge items, review state and statuses, into another space you belong to. Sharing again updates the copy rather than duplicating it.

## Groups

- **Creating:** anyone can create a group on the **Groups** page and becomes its admin.
- **Inviting:** the group admin searches users by name and invites them. The invited user sees a badge on **Groups** and accepts or declines. Until they accept, they have no access.
- **Managing (group admin):** rename the group, remove members or cancel invitations (what a removed member added stays in the group), or delete the group, which deletes its space for everyone.
- **Leaving:** members can leave. The group admin can't; they delete the group instead.

**How it works:** the frontend sends the active space in an `X-Space` header. The backend ([`backend/app/spaces.py`](../backend/app/spaces.py)) checks you're an active member, then scopes every query to that space. A request for a group you're not in, or for another user's personal space, gets a 403.
