"""Snapshot of the current git repo, so a handoff reflects real state, not memory."""
import os
import subprocess


def _run(args, cwd):
    try:
        r = subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def _block(text, max_lines=40):
    lines = text.splitlines()
    if len(lines) > max_lines:
        lines = lines[:max_lines] + ["... (%d more lines)" % (len(text.splitlines()) - max_lines)]
    return "```\n" + "\n".join(lines) + "\n```"


def snapshot(cwd=None):
    """Markdown bullets describing the repo, or '' if cwd is not inside a git repo."""
    cwd = cwd or os.getcwd()
    if _run(["rev-parse", "--is-inside-work-tree"], cwd) != "true":
        return ""
    branch = _run(["branch", "--show-current"], cwd) or "(detached HEAD)"
    status = _run(["status", "--short"], cwd)
    log = _run(["log", "--oneline", "-5"], cwd)       # None when there are no commits yet
    diff = _run(["diff", "HEAD", "--stat"], cwd)      # a stat, not the full diff: shorter, safer
    stash = _run(["stash", "list"], cwd)
    out = ["- Branch: " + branch]
    out.append("- Uncommitted changes: " + ("none" if not status else "\n" + _block(status)))
    if diff:
        out.append("- Changed since last commit (diffstat):\n" + _block(diff))
    out.append("- Recent commits: " + ("\n" + _block(log) if log else "none yet"))
    out.append("- Stashes: " + ("\n" + _block(stash) if stash else "none"))
    return "\n".join(out)
