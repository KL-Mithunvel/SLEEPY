"""Quick test of the AI chat endpoint."""
import urllib.request
import json

MSG = (
    "Create a project file for a new project called SMTW Website Redesign. "
    "It is under the SMTW OU. Goal is to redesign the company website by August 2026. "
    "Initial tasks: kick off meeting, collect requirements, design mockups."
)

body = json.dumps({"messages": [{"role": "user", "content": MSG}]}).encode()
req = urllib.request.Request(
    "http://localhost:5000/api/ai/chat",
    data=body,
    headers={
        "Content-Type": "application/json",
        "Authorization": "Bearer dev-bypass",
    },
    method="POST",
)

collected_text = ""
tool_starts = []
actions = []

with urllib.request.urlopen(req, timeout=60) as resp:
    for raw_chunk in resp:
        line = raw_chunk.decode("utf-8", errors="replace").strip()
        if not line.startswith("data: "):
            continue
        ev = json.loads(line[6:])
        t = ev.get("type")
        if t == "delta":
            collected_text += ev.get("text", "")
        elif t == "tool_progress":
            inner = ev.get("event", {})
            if inner.get("type") == "tool_start":
                tool_starts.append(inner.get("name"))
        elif t == "done":
            r = ev.get("result", {})
            actions = r.get("actions", [])
            print("=== AI REPLY ===")
            print(collected_text[:600] if collected_text else "(no text)")
            print()
            print("Tool calls:", tool_starts)
            print("Actions staged:", len(actions))
            for a in actions:
                print("  ->", a)
            break
        elif t == "error":
            print("ERROR:", ev.get("message"))
            break
