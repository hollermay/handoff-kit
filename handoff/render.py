"""Turn a Conversation into a context pack another model can read."""
from datetime import date

from .redact import redact

PREAMBLE = """\
## Paste this first

You are continuing work that started in a different AI assistant ({source}). The
conversation is below, lightly cleaned, with secrets removed. Do this:

1. Read the whole conversation.
2. Reply with a short summary in your own words: the Goal, the Decisions (with reasons),
   the Current state, the Results, the Open threads, the Gotchas and constraints, and a
   Suggested first task.
3. Wait for me to confirm or correct it before you do any work.

Treat decisions as settled unless I say otherwise. Do not redo finished work. Anything
marked unverified in the conversation is still unverified. If something is missing, ask
me instead of guessing. You cannot use the other assistant's tools, skills or memory.
"""


def _clip(block, limit):
    if len(block) <= limit:
        return block
    return block[: max(limit - 20, 0)] + "\n[... clipped ...]\n"


TOOL_NOTE = """\
Blocks marked TOOL CALL and TOOL RESULT show what the previous assistant ran and what
came back. Treat results as facts from that moment; they may be out of date now.
"""


def _fit(blocks, max_chars):
    """Keep the start and (mostly) the end of the chat, cutting at turn boundaries."""
    if not max_chars:  # 0 = keep everything
        return blocks, 0
    if sum(len(b) for b in blocks) <= max_chars or len(blocks) < 2:
        if blocks and sum(len(b) for b in blocks) > max_chars:
            return [_clip(blocks[0], max_chars)], 0
        return blocks, 0
    head_budget = max_chars // 3
    tail_budget = max_chars - head_budget
    head, used = [], 0
    for b in blocks:
        if used + len(b) > head_budget:
            break
        head.append(b)
        used += len(b)
    if not head:
        head = [_clip(blocks[0], head_budget)]
    tail, used = [], 0
    for b in reversed(blocks[len(head):]):
        if used + len(b) > tail_budget:
            break
        tail.append(b)
        used += len(b)
    tail.reverse()
    if not tail:
        tail = [_clip(blocks[-1], tail_budget)]
    omitted = len(blocks) - len(head) - len(tail)
    if omitted <= 0:
        return head + tail, 0
    return head + ["[... %d turns from the middle omitted for length ...]\n" % omitted] + tail, omitted


def render(conv, max_chars=60000, bare=False, repo=""):
    """Return (document, stats). repo is an optional git snapshot to include."""
    blocks = ["### %s\n%s\n" % (role, redact(text)) for role, text in conv.turns]
    kept, omitted = _fit(blocks, max_chars)
    parts = []
    if not bare:
        parts.append("# Context pack: %s\n" % conv.title)
        parts.append("Source: %s. Exported %s. Turns: %d.\n" % (conv.source, date.today().isoformat(), len(conv.turns)))
        parts.append(PREAMBLE.format(source=conv.source))
        if any(role.startswith("TOOL") for role, _ in conv.turns):
            parts.append(TOOL_NOTE)
    if repo:
        parts.append("## Repo snapshot\n" + redact(repo) + "\n")
    parts.append("## Conversation\n")
    parts.extend(kept)
    if conv.files:
        parts.append("## Files written or edited\n" + "\n".join("- " + redact(f) for f in conv.files) + "\n")
    doc = "\n".join(parts)
    return doc, {"turns": len(conv.turns), "omitted": omitted, "chars": len(doc)}
