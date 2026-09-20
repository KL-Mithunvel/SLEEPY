# RBAC policy — single source of truth for access control.
# Two roles: "user" (full personal-assistant access — this is still a
# single/two-person tool, "user" isn't restricted from anything on their
# own data) and "admin" (everything "user" has, plus visibility into
# login/security monitoring). "admin" bypass is handled in code
# (auth_utils.has_perm / compute_permissions), never listed as a grantee
# in PERMISSIONS below — see test_no_admin_in_permission_tuples.

ROLES = ("admin", "user")  # ordered most → least privileged

PERMISSIONS = {
    # Projects
    "projects:read":   ("user",),
    "projects:write":  ("user",),

    # AI actions
    "ai:suggest":      ("user",),
    "ai:edit_md":      ("user",),

    # Logs / briefings
    "logs:read":       ("user",),
    "logs:write":      ("user",),

    # Corpus actions
    "corpus:news_watch":      ("user",),
    "corpus:materialise":     ("user",),
    "corpus:move_line":       ("user",),
    "corpus:housekeeping":    ("user",),
    "corpus:weekly_review":   ("user",),

    # Integration actions
    "integrations:send":    ("user",),
    "integrations:sync":    ("user",),

    # Admin
    "admin:reindex":   ("user",),

    # Security monitoring / AI usage — granted to "user" by nobody; admins
    # get it via the code-level bypass (compute_permissions expands to every
    # key here for role="admin"), not via this tuple.
    "admin:security":  (),
    "admin:ai_usage":  (),
    "admin:alerts":    (),
    "admin:system":    (),
}
