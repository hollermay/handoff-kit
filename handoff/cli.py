import argparse
import os
import shutil
import sys
import tempfile
from datetime import datetime

from . import git, sources
from .render import render

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_TARGETS = {
    "claude": ("~/.claude/skills/handoff", "claude"),
    "codex": ("~/.agents/skills/handoff", "codex"),
}


def _pick(refs, pick, ident):
    if not refs:
        raise SystemExit("No chats found. Try --path to point at the files.")
    if ident:
        hits = [r for r in refs if ident in r.path or ident in r.key]
        if not hits:
            raise SystemExit("No chat matches %r." % ident)
        return hits[0]
    if not 1 <= pick <= len(refs):
        raise SystemExit("--pick must be between 1 and %d." % len(refs))
    return refs[pick - 1]


def cmd_list(a):
    refs = sources.find(a.source, a.path)[: a.n]
    if not refs:
        print("No chats found.")
        return 1
    for i, ref in enumerate(refs, 1):
        try:
            conv = sources.load(ref)
            title, turns = conv.title, len(conv.turns)
        except Exception as exc:  # a broken file should not hide the others
            title, turns = "(unreadable: %s)" % exc, 0
        when = datetime.fromtimestamp(ref.mtime).strftime("%Y-%m-%d %H:%M") if ref.mtime else "-"
        print("%2d  %s  %3d turns  %s" % (i, when, turns, title))
    return 0


def cmd_export(a):
    ref = _pick(sources.find(a.source, a.path), a.pick, a.id)
    tools = a.tools or a.full
    tool_chars = 0 if a.full and a.tool_chars is None else (2000 if a.tool_chars is None else a.tool_chars)
    max_chars = a.max_chars if a.max_chars is not None else (0 if a.full else 60000)
    conv = sources.load(ref, tools=tools, tool_chars=tool_chars)
    if not any(role in ("USER", "ASSISTANT") for role, _ in conv.turns):
        raise SystemExit("That chat has no readable messages. The file format may have changed.")
    repo = "" if a.no_git else git.snapshot()
    doc, stats = render(conv, max_chars, a.bare, repo)
    if a.out == "-":
        sys.stdout.write(doc)
        return 0
    out = a.out
    if not out:
        fd, out = tempfile.mkstemp(prefix="handoff_", suffix=".md")
        os.close(fd)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(doc)
    print(out)
    print("source=%s turns=%d omitted=%d chars=%d" % (a.source, stats["turns"], stats["omitted"], stats["chars"]))
    return 0


PORTABLE_NOTE = (
    "In that command, `<SKILL_DIR>` is the folder that contains this SKILL.md (the tool\n"
    "usually shows its path when the skill loads), and `<TOOL>` is the tool you are running\n"
    "in: `claude` for Claude Code, `codex` for Codex, `gemini` for Gemini CLI.\n\n")


def skill_text(skill_dir=None, source=None):
    """SKILL.md text. With no skill_dir/source it is portable (path-independent)."""
    with open(os.path.join(HERE, "templates", "skill.md.tmpl"), encoding="utf-8") as fh:
        text = fh.read()
    portable = skill_dir is None
    extra = "allowed-tools: [Bash, Read, Write]\n" if portable or source == "claude" else ""
    return (text.replace("{{ALLOWED_TOOLS}}\n", extra)
            .replace("{{PORTABLE_NOTE}}", PORTABLE_NOTE if portable else "")
            .replace("{{SKILL_DIR}}", skill_dir or "<SKILL_DIR>")
            .replace("{{SOURCE}}", source or "<TOOL>"))


def write_skill(dest, text):
    if os.path.exists(dest):
        shutil.rmtree(dest)
    os.makedirs(os.path.join(dest, "scripts"))
    shutil.copytree(HERE, os.path.join(dest, "scripts", "handoff"),
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    with open(os.path.join(dest, "SKILL.md"), "w", encoding="utf-8") as fh:
        fh.write(text)


def cmd_install(a):
    targets = list(SKILL_TARGETS) if a.to == "both" else [a.to]
    for name in targets:
        dest = os.path.expanduser(a.dest or SKILL_TARGETS[name][0])
        if os.path.exists(dest) and not a.force:
            print("Skipped %s: %s exists (use --force to replace it)." % (name, dest))
            continue
        write_skill(dest, skill_text(dest, name))
        print("Installed %s skill at %s" % (name, dest))
    return 0


def cmd_build(a):
    """Write the portable skill that `npx skills add` installs (committed under skills/)."""
    write_skill(os.path.abspath(a.out), skill_text())
    print("Built portable skill at %s" % os.path.abspath(a.out))
    return 0


def build_parser():
    p = argparse.ArgumentParser(prog="handoff", description="Export an AI chat so another model can continue it.")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("--from", dest="source", required=True, choices=sorted(sources.SOURCES))
        sp.add_argument("--path", help="chat file or folder (required for chatgpt)")

    ls = sub.add_parser("list", help="show recent chats")
    common(ls)
    ls.add_argument("-n", type=int, default=15)
    ls.set_defaults(fn=cmd_list)

    ex = sub.add_parser("export", help="write a context pack")
    common(ex)
    ex.add_argument("--pick", type=int, default=1, help="1 = newest (default)")
    ex.add_argument("--id", help="choose the chat whose path or id/title contains this text")
    ex.add_argument("--tools", action="store_true",
                    help="also include tool calls and their results (each result cut at --tool-chars)")
    ex.add_argument("--full", action="store_true",
                    help="everything: tool calls and complete results, no length limit (can be huge)")
    ex.add_argument("--tool-chars", type=int, default=None,
                    help="max characters kept per tool call/result (default 2000; 0 = no cut)")
    ex.add_argument("--max-chars", type=int, default=None,
                    help="max size of the whole export (default 60000; 0 = no limit)")
    ex.add_argument("--no-git", action="store_true", help="skip the git repo snapshot")
    ex.add_argument("--bare", action="store_true", help="conversation only, no pasted-first instructions")
    ex.add_argument("--out", help="output file; '-' prints to the terminal")
    ex.set_defaults(fn=cmd_export)

    ins = sub.add_parser("install-skill", help="install the /handoff skill")
    ins.add_argument("--to", choices=["claude", "codex", "both"], default="claude")
    ins.add_argument("--dest", help="install somewhere else")
    ins.add_argument("--force", action="store_true", help="replace an existing install")
    ins.set_defaults(fn=cmd_install)

    bld = sub.add_parser("build-skill", help="regenerate the portable skills/handoff folder (maintainers)")
    bld.add_argument("--out", default="skills/handoff")
    bld.set_defaults(fn=cmd_build)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.fn(args)
