"""Readers that turn each tool's saved chats into a common Conversation.

Claude Code is verified against real session files. The Codex, Gemini and ChatGPT
readers follow the documented/known file shapes and are covered by sample files in
tests/, not by real ones. If a reader finds nothing, the file layout may have changed.
"""
import glob
import io
import json
import os
import re
import zipfile
from dataclasses import dataclass, field

USER, ASSISTANT = "USER", "ASSISTANT"
TOOL_CALL, TOOL_RESULT = "TOOL CALL", "TOOL RESULT"


@dataclass
class Ref:
    source: str
    path: str
    mtime: float = 0.0
    key: str = ""     # session id or conversation title, when one file holds many chats
    data: object = None  # pre-parsed chat for multi-chat files


@dataclass
class Conversation:
    source: str
    title: str
    turns: list = field(default_factory=list)  # [(USER|ASSISTANT, text)]
    files: list = field(default_factory=list)
    path: str = ""


# ---------------------------------------------------------------- helpers

def _jsonl(path):
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if isinstance(obj, dict):
                yield obj


def _parts_text(content, types=("text", "input_text", "output_text")):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for part in content:
            if isinstance(part, str):
                out.append(part)
            elif isinstance(part, dict) and part.get("type", "text") in types and isinstance(part.get("text"), str):
                out.append(part["text"])
        return "\n".join(out)
    return ""


def _clip_text(text, limit):
    """Cut a long tool result. limit 0 means keep everything."""
    if not limit or len(text) <= limit:
        return text
    return text[:limit] + "\n[... %d more characters cut ...]" % (len(text) - limit)


def _as_text(value):
    """Best-effort text for a tool input/output of unknown shape."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list) and all(isinstance(v, (str, dict)) for v in value):
        joined = _parts_text(value)
        if joined:
            return joined
    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(value)


def _snippet(text, n=70):
    one = " ".join(text.split())
    return one if len(one) <= n else one[: n - 1] + "…"


def _by_mtime(paths):
    return sorted(paths, key=os.path.getmtime, reverse=True)


# ------------------------------------------------------------ Claude Code

_CLAUDE_NOISE = re.compile(
    r"<(system-reminder|task-notification|local-command-caveat|local-command-stdout|"
    r"command-name|command-message|command-args)>.*?</\1>", re.S)


def claude_find(path=None):
    root = os.path.expanduser("~/.claude/projects")
    if path and os.path.isfile(path):
        files = [path]
    elif path:
        files = glob.glob(os.path.join(path, "*.jsonl")) or glob.glob(os.path.join(path, "*", "*.jsonl"))
    else:
        enc = re.sub(r"[^A-Za-z0-9]", "-", os.getcwd())
        files = glob.glob(os.path.join(root, enc, "*.jsonl")) or glob.glob(os.path.join(root, "*", "*.jsonl"))
    return [Ref("claude", p, os.path.getmtime(p)) for p in _by_mtime(files)]


def claude_load(ref, tools=False, tool_chars=2000):
    turns, files, title = [], [], None
    for o in _jsonl(ref.path):
        kind = o.get("type")
        if kind == "ai-title":
            title = o.get("aiTitle") or title
            continue
        if o.get("isSidechain") or o.get("isMeta") or kind not in ("user", "assistant"):
            continue
        content = (o.get("message") or {}).get("content", "")
        blocks = content if isinstance(content, list) else []
        if kind == "user":
            results = [b for b in blocks if isinstance(b, dict) and b.get("type") == "tool_result"]
            if results:
                if tools:  # what the tools returned
                    for b in results:
                        label = "error: " if b.get("is_error") else ""
                        turns.append((TOOL_RESULT, label + _clip_text(_as_text(b.get("content")).strip(), tool_chars)))
                continue
            text = _CLAUDE_NOISE.sub("", _parts_text(content)).strip()
            if text:
                turns.append((USER, text))
        else:
            pending = []

            def flush():
                text = "\n".join(pending).strip()
                if text:
                    turns.append((ASSISTANT, text))
                del pending[:]

            if isinstance(content, str):
                pending.append(content)
            for b in blocks:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "text" and isinstance(b.get("text"), str):
                    pending.append(b["text"])
                elif b.get("type") == "tool_use":
                    inp = b.get("input") or {}
                    if b.get("name") in ("Write", "Edit", "NotebookEdit"):
                        fp = inp.get("file_path")
                        if fp and fp not in files:
                            files.append(fp)
                    if tools:
                        flush()
                        turns.append((TOOL_CALL, "%s %s" % (b.get("name"), _clip_text(_as_text(inp), tool_chars))))
            flush()
    if not title and turns:
        title = _snippet(turns[0][1], 60)
    return Conversation("Claude Code", title or os.path.basename(ref.path), turns, files, ref.path)


# -------------------------------------------------------------- Codex CLI

_CODEX_NOISE = re.compile(
    r"<(environment_context|user_instructions|INSTRUCTIONS)>.*?</\1>", re.S)
_CODEX_PATCH_FILE = re.compile(r"\*\*\* (?:Add|Update|Delete) File: (.+)")


def codex_find(path=None):
    if path and os.path.isfile(path):
        files = [path]
    else:
        root = path or os.path.join(os.environ.get("CODEX_HOME", os.path.expanduser("~/.codex")), "sessions")
        files = glob.glob(os.path.join(root, "**", "*.jsonl"), recursive=True)
    return [Ref("codex", p, os.path.getmtime(p)) for p in _by_mtime(files)]


def _codex_output(value):
    """function_call_output.output is often a JSON string like {"output": "...", ...}."""
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except ValueError:
            return value
        if isinstance(parsed, dict) and "output" in parsed:
            return _as_text(parsed["output"])
        return value
    return _as_text(value)


def codex_load(ref, tools=False, tool_chars=2000):
    messages, events, files = [], [], []
    for o in _jsonl(ref.path):
        p = o.get("payload") if isinstance(o.get("payload"), dict) else o
        ptype = p.get("type")
        if tools and ptype in ("function_call", "custom_tool_call", "local_shell_call"):
            if ptype == "local_shell_call":
                body = _as_text((p.get("action") or {}).get("command"))
                name = "shell"
            else:
                body = _as_text(p.get("arguments") if "arguments" in p else p.get("input"))
                name = p.get("name") or ptype
            item = (TOOL_CALL, "%s %s" % (name, _clip_text(body, tool_chars)))
            messages.append(item)
            events.append(item)
        elif tools and ptype in ("function_call_output", "custom_tool_call_output"):
            item = (TOOL_RESULT, _clip_text(_codex_output(p.get("output")).strip(), tool_chars))
            messages.append(item)
            events.append(item)
        if ptype == "message" and p.get("role") in ("user", "assistant"):
            text = _parts_text(p.get("content"))
            if p["role"] == "user":
                text = _CODEX_NOISE.sub("", text)
                if text.lstrip().startswith("# AGENTS.md instructions"):
                    text = ""
            text = text.strip()
            if text:
                messages.append((USER if p["role"] == "user" else ASSISTANT, text))
        elif ptype == "user_message" and isinstance(p.get("message"), str):
            events.append((USER, _CODEX_NOISE.sub("", p["message"]).strip()))
        elif ptype == "agent_message" and isinstance(p.get("message"), str):
            events.append((ASSISTANT, p["message"].strip()))
        elif ptype in ("function_call", "custom_tool_call"):
            args = p.get("arguments") or p.get("input") or ""
            if isinstance(args, str):
                for m in _CODEX_PATCH_FILE.finditer(args):
                    fp = m.group(1).strip().strip('"\\')
                    if fp not in files:
                        files.append(fp)
    # response_item messages and event_msg echoes carry the same text; use one set.
    chat = lambda items: any(role in (USER, ASSISTANT) for role, _ in items)
    turns = [t for t in (messages if chat(messages) else events) if t[1]]
    chat_turns = [t for t in turns if t[0] in (USER, ASSISTANT)]
    title = _snippet(chat_turns[0][1], 60) if chat_turns else os.path.basename(ref.path)
    return Conversation("Codex CLI", title, turns, files, ref.path)


# ------------------------------------------------------------- Gemini CLI

def _gemini_text(msg):
    for key in ("content", "message", "parts", "text"):
        if key in msg:
            value = msg[key]
            if isinstance(value, dict):
                value = value.get("parts") or value.get("text") or ""
            text = _parts_text(value, types=("text",))
            if text:
                return text
    return ""


def gemini_find(path=None):
    if path and os.path.isfile(path):
        files = [path]
    else:
        root = path or os.path.expanduser("~/.gemini/tmp")
        files = (glob.glob(os.path.join(root, "**", "chats", "*.json"), recursive=True)
                 + glob.glob(os.path.join(root, "**", "logs.json"), recursive=True))
    refs = []
    for p in _by_mtime(files):
        try:
            with open(p, encoding="utf-8", errors="replace") as fh:
                data = json.load(fh)
        except ValueError:
            continue
        mt = os.path.getmtime(p)
        if isinstance(data, dict):
            refs.append(Ref("gemini", p, mt, str(data.get("sessionId", "")), data.get("messages", [])))
        elif isinstance(data, list):  # logs.json: one flat list for many sessions
            sessions = {}
            for entry in data:
                if isinstance(entry, dict):
                    sessions.setdefault(str(entry.get("sessionId", "")), []).append(entry)
            for sid, entries in sessions.items():
                refs.append(Ref("gemini", p, mt, sid, entries))
    return refs


def _gemini_tool_result(tc):
    shown = tc.get("resultDisplay")
    if isinstance(shown, str) and shown.strip():
        return shown
    out = []
    for r in tc.get("result") or []:
        resp = ((r or {}).get("functionResponse") or {}).get("response")
        out.append(_as_text((resp or {}).get("output") if isinstance(resp, dict) and "output" in resp else resp))
    return "\n".join(x for x in out if x)


def gemini_load(ref, tools=False, tool_chars=2000):
    turns = []
    for m in ref.data or []:
        if not isinstance(m, dict):
            continue
        role = m.get("type") or m.get("role")
        text = _gemini_text(m).strip()
        if role == "user" and text:
            turns.append((USER, text))
        elif role in ("gemini", "model", "assistant"):
            if text:
                turns.append((ASSISTANT, text))
            if tools:
                for tc in m.get("toolCalls") or []:
                    if not isinstance(tc, dict):
                        continue
                    turns.append((TOOL_CALL, "%s %s" % (tc.get("name"), _clip_text(_as_text(tc.get("args")), tool_chars))))
                    result = _gemini_tool_result(tc).strip()
                    if result:
                        turns.append((TOOL_RESULT, _clip_text(result, tool_chars)))
    chat_turns = [t for t in turns if t[0] in (USER, ASSISTANT)]
    title = _snippet(chat_turns[0][1], 60) if chat_turns else (ref.key or os.path.basename(ref.path))
    return Conversation("Gemini CLI", title, turns, [], ref.path)


# ---------------------------------------------------------- ChatGPT export

def _chatgpt_files(path):
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            for name in sorted(z.namelist()):
                if re.search(r"(^|/)conversations[^/]*\.json$", name):
                    yield name, io.TextIOWrapper(z.open(name), encoding="utf-8").read()
    elif os.path.isdir(path):
        for p in sorted(glob.glob(os.path.join(path, "conversations*.json"))):
            with open(p, encoding="utf-8") as fh:
                yield p, fh.read()
    else:
        with open(path, encoding="utf-8") as fh:
            yield path, fh.read()


def chatgpt_find(path=None):
    if not path:
        raise SystemExit("ChatGPT needs --path to the export (.zip, conversations.json, or its folder). "
                         "Get one from ChatGPT: Settings > Data controls > Export data.")
    refs = []
    for name, raw in _chatgpt_files(path):
        data = json.loads(raw)
        if isinstance(data, dict):
            data = [data]
        for conv in data:
            if isinstance(conv, dict) and "mapping" in conv:
                refs.append(Ref("chatgpt", path, float(conv.get("update_time") or conv.get("create_time") or 0),
                                str(conv.get("title") or "Untitled"), conv))
    return sorted(refs, key=lambda r: r.mtime, reverse=True)


def _chatgpt_branch(conv):
    mapping = conv.get("mapping", {})
    node, chain, seen = conv.get("current_node"), [], set()
    while node and node in mapping and node not in seen:
        seen.add(node)
        chain.append(mapping[node])
        node = mapping[node].get("parent")
    if chain:
        return list(reversed(chain))
    return sorted((n for n in mapping.values() if n.get("message")),
                  key=lambda n: n["message"].get("create_time") or 0)


def _chatgpt_text(content):
    ctype = content.get("content_type")
    if ctype == "multimodal_text":
        return _parts_text(content.get("parts"), types=("text",))
    if ctype == "text":
        return "\n".join(p for p in content.get("parts", []) if isinstance(p, str))
    if ctype in ("code", "execution_output"):
        return str(content.get("text", ""))
    return ""


def chatgpt_load(ref, tools=False, tool_chars=2000):
    turns = []
    for node in _chatgpt_branch(ref.data):
        m = node.get("message")
        if not m:
            continue
        author = m.get("author") or {}
        role = author.get("role")
        if role not in ("user", "assistant", "tool"):
            continue
        if (m.get("metadata") or {}).get("is_visually_hidden_from_conversation"):
            continue
        content = m.get("content") or {}
        text = _chatgpt_text(content).strip()
        if not text:
            continue
        if role == "tool":  # output from python, browsing, etc.
            if tools:
                turns.append((TOOL_RESULT, "%s: %s" % (author.get("name") or "tool", _clip_text(text, tool_chars))))
        elif m.get("recipient") not in (None, "all"):  # assistant calling a tool
            if tools:
                turns.append((TOOL_CALL, "%s %s" % (m.get("recipient"), _clip_text(text, tool_chars))))
        elif content.get("content_type") == "code":
            turns.append((USER if role == "user" else ASSISTANT, "```\n" + text + "\n```"))
        else:
            turns.append((USER if role == "user" else ASSISTANT, text))
    return Conversation("ChatGPT", ref.key or "Untitled", turns, [], ref.path)


# ---------------------------------------------------------------- registry

SOURCES = {
    "claude": (claude_find, claude_load),
    "codex": (codex_find, codex_load),
    "gemini": (gemini_find, gemini_load),
    "chatgpt": (chatgpt_find, chatgpt_load),
}


def find(source, path=None):
    return SOURCES[source][0](path)


def load(ref, tools=False, tool_chars=2000):
    """tools=True also keeps tool calls and what they returned (each result cut at tool_chars; 0 = no cut)."""
    return SOURCES[ref.source][1](ref, tools=tools, tool_chars=tool_chars)
