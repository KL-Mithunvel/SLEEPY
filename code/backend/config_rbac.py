# RBAC policy — single source of truth for access control.
# Single-user app: one "owner" role with full access.
# Add more roles only when a second user exists.

ROLES = ("owner",)  # ordered most → least privileged

PERMISSIONS = {
    # Projects
    "projects:read":   ("owner",),
    "projects:write":  ("owner",),

    # AI actions
    "ai:suggest":      ("owner",),
    "ai:edit_md":      ("owner",),

    # Logs / briefings
    "logs:read":       ("owner",),
    "logs:write":      ("owner",),

    # Corpus actions
    "corpus:news_watch":      ("owner",),
    "corpus:materialise":     ("owner",),
    "corpus:move_line":       ("owner",),
    "corpus:housekeeping":    ("owner",),
    "corpus:weekly_review":   ("owner",),

    # Integration actions
    "integrations:send":    ("owner",),
    "integrations:sync":    ("owner",),

    # Admin
    "admin:reindex":   ("owner",),
}

# Keycloak realm roles that map to the app-level "owner"
OWNER_REALM_ROLES = {"owner", "mspv-apps-admin", "admin"}
