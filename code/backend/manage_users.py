"""
CLI for provisioning SLEEPY's fixed user accounts. No self-service signup —
this is a personal/two-account tool (one "user" account for daily use, one
"admin" account for security monitoring).

Usage (from code/backend/, or via `uv run python code/backend/manage_users.py ...`):
    create-user <username> <role>   role is "user" or "admin"; prompts for password
    list-users
    reset-password <username>
    delete-user <username>
"""

import argparse
import getpass
import sys

import auth_utils
import config_rbac
import local_db


def _prompt_password() -> str:
    password = getpass.getpass("Password: ")
    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        print("Passwords did not match.", file=sys.stderr)
        sys.exit(1)
    if len(password) < 8:
        print("Password must be at least 8 characters.", file=sys.stderr)
        sys.exit(1)
    return password


def create_user(username: str, role: str):
    password_hash = auth_utils.hash_password(_prompt_password())

    conn = local_db.get_db()
    try:
        conn.execute(
            "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
            (username, password_hash, role),
        )
        conn.commit()
        print(f"Created {role} account: {username}")
    finally:
        local_db.return_db(conn)


def list_users():
    conn = local_db.get_db()
    try:
        rows = conn.execute(
            "SELECT username, role, created_at FROM users ORDER BY created_at"
        ).fetchall()
        if not rows:
            print("No users yet.")
        for row in rows:
            print(f"{row['username']:20s} {row['role']:8s} created {row['created_at']}")
    finally:
        local_db.return_db(conn)


def reset_password(username: str):
    password_hash = auth_utils.hash_password(_prompt_password())

    conn = local_db.get_db()
    try:
        # token_version bump = every token issued under the old password is
        # dead immediately, not in up to AUTH_TOKEN_TTL_DAYS.
        cur = conn.execute(
            "UPDATE users SET password_hash = ?, token_version = token_version + 1 WHERE username = ?",
            (password_hash, username),
        )
        conn.commit()
        if cur.rowcount == 0:
            print(f"No such user: {username}", file=sys.stderr)
            sys.exit(1)
        print(f"Password updated for {username} (all existing sessions signed out)")
    finally:
        local_db.return_db(conn)


def delete_user(username: str):
    conn = local_db.get_db()
    try:
        cur = conn.execute("DELETE FROM users WHERE username = ?", (username,))
        conn.commit()
        if cur.rowcount == 0:
            print(f"No such user: {username}", file=sys.stderr)
            sys.exit(1)
        print(f"Deleted {username}")
    finally:
        local_db.return_db(conn)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_create = sub.add_parser("create-user")
    p_create.add_argument("username")
    p_create.add_argument("role", choices=config_rbac.ROLES)

    sub.add_parser("list-users")

    p_reset = sub.add_parser("reset-password")
    p_reset.add_argument("username")

    p_delete = sub.add_parser("delete-user")
    p_delete.add_argument("username")

    args = parser.parse_args()

    local_db.init_db()

    if args.command == "create-user":
        create_user(args.username, args.role)
    elif args.command == "list-users":
        list_users()
    elif args.command == "reset-password":
        reset_password(args.username)
    elif args.command == "delete-user":
        delete_user(args.username)


if __name__ == "__main__":
    main()
