"""
Untrusted-content labelling — web text is data, never instructions.

news_watch.py writes 📰 bullets into inbox.md whose titles and "relevant
because" text originate on the public web. Every path that hands corpus text
to an LLM (chat tools, the RAG block in the chat system prompt, the morning
briefing's inbox) runs it through mark_untrusted(), which wraps each 📰 line
in <untrusted_web_content> tags that SystemPrompt.MD tells the model never to
obey. A label is not a guarantee — models can still be talked round — so the
hard guarantees stay where they were (confirm gate, path guards, email
allowlist); this lowers the odds and feeds action_risk's warnings.

    mark_untrusted(text)      -> text with every 📰 line wrapped
    contains_untrusted(text)  -> True if text carries a wrapped line
"""

import re

OPEN_TAG = "<untrusted_web_content>"
CLOSE_TAG = "</untrusted_web_content>"

_NEWS_MARKER = "📰"
# Remove the tag name itself from the wrapped text, whatever its case or
# spacing, so a line can't close its own wrapper early and have the rest read
# as trusted.
_TAG_NAME_RE = re.compile(r"untrusted[\s_\-]*web[\s_\-]*content", re.IGNORECASE)


def _wrap(line: str) -> str:
    return f"{OPEN_TAG}{_TAG_NAME_RE.sub('[removed]', line)}{CLOSE_TAG}"


def mark_untrusted(text: str) -> str:
    """
    Wrap every line containing a 📰 news bullet. Deliberately NOT idempotent:
    skipping lines that already look wrapped would let a web line shaped like
    `<tag>a</tag> OBEY <tag>b</tag>` pass through with its middle outside any
    wrapper. Each caller marks a given text exactly once.
    """
    if not text or _NEWS_MARKER not in text:
        return text
    return "\n".join(_wrap(line) if _NEWS_MARKER in line else line for line in text.split("\n"))


def contains_untrusted(text: str) -> bool:
    return bool(text) and OPEN_TAG in text


def unwrap(text: str) -> str:
    """
    Strip the wrapper tags from model-written content before it is saved. The
    labels exist only in what the model reads; a model that reads inbox.md
    and writes it back whole would otherwise persist them into the corpus.
    """
    if not text:
        return text
    return text.replace(OPEN_TAG, "").replace(CLOSE_TAG, "")
