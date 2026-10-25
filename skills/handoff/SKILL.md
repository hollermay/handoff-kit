---
name: handoff
description: >
  Generate a structured handoff prompt for seamless session continuity. Use when the user
  types /handoff, asks to "wrap up the session", "prepare a handoff", "start a new chat",
  "export this chat", "take this to ChatGPT/Gemini/Codex", or says the session is getting
  long and they want to continue in a fresh context. Captures task state, decisions,
  results, open threads and a repo snapshot so the next session, in any AI tool, can pick
  up without re-explaining anything.
allowed-tools: [Bash, Read, Write]
---

# Handoff

You are preparing a session handoff document: a self-contained prompt that a new session
(in Claude Code, Codex, ChatGPT, Gemini or anything else) can receive as its very first
message and immediately understand the full context of the work in progress.

## Step 1 - Gather the transcript and live repo state

From the project directory run:

```bash
PYTHONPATH="<SKILL_DIR>/scripts" python3 -m handoff export --from <TOOL> --bare
```

In that command, `<SKILL_DIR>` is the folder that contains this SKILL.md (the tool
usually shows its path when the skill loads), and `<TOOL>` is the tool you are running
in: `claude` for Claude Code, `codex` for Codex, `gemini` for Gemini CLI.

It prints a temp file path and a stats line. The file has the repo snapshot (branch,
uncommitted changes, diffstat, recent commits, stashes; skipped if this is not a git repo)
followed by the cleaned conversation, with likely secrets removed. If `omitted` is above 0,
the middle of a very long chat was dropped; say so in the handoff. Read the file. If a
command fails, note it and continue with what you have.

**Results and outputs.** By default tool output is left out. If the user wants it included
(command output, test runs, search results, file contents), add `--tools` (each result cut
at 2,000 characters; change with `--tool-chars N`). Add `--full` for everything with no
cuts, which can be very large. Tool output often contains secrets, so the re-check in
Step 4 matters more in these modes. To export another tool's chat, change `--from`
(claude, codex, gemini, chatgpt); `python3 -m handoff list --from <tool>` shows choices
and `--pick N` selects one.

## Step 2 - Reflect on the conversation

Using the file, and the conversation if it is still in your context (say which you used),
extract:

- **Primary goal** - the overarching thing the user is trying to accomplish
- **Completed work** - what was actually finished (files created or edited, features
  implemented, bugs fixed)
- **Decisions made** - choices that were deliberated and settled, with the reasoning, so the
  next session does not re-litigate them
- **Current state** - exactly where things stand (what works, what is partial, what is broken)
- **Results and outputs** - findings, numbers, command or test results, links, and anything
  marked unverified, exactly as reliable as it was in the chat
- **Open threads** - unfinished work, TODOs, ideas noted but not acted on, known issues
- **Gotchas and constraints** - environment quirks, deliberate architectural choices, things
  that were tried and did not work

## Step 3 - Write the handoff prompt

Write it to `HANDOFF.md` in the current directory (or the path the user names), formatted
exactly as below. Then show the same prompt in your reply with no preamble or commentary
outside it, except one closing line giving the file path.

```markdown
## Session Handoff

You are continuing work from an earlier session with another AI assistant. Read the
context below. Treat the Decisions and Gotchas as settled unless I say otherwise. Do not
redo finished work. Start from "Suggested First Task" and ask me if anything is unclear.

### Project
[project name and one-sentence description]

### Primary Goal
[what we are ultimately trying to accomplish]

### Background
[who the user is, their stack and preferences, stated plainly. The new assistant has no
memory files or instructions from the old tool.]

### What Was Completed This Session
[bulleted list - specific, concrete. File names, function names, feature names.]

### Decisions Made (and Why)
[bulleted list - each decision followed by a brief rationale. Include alternatives that
were ruled out if relevant.]

### Current State
[precise description of where things stand. What works. What is in progress. What is
broken. Reference specific files or components by name.]

### Results and Outputs
[key results the next steps depend on: numbers, names, links, command/test output,
chosen options. Mark anything unverified as unverified.]

### Open Threads
[bulleted list of unfinished work, in rough priority order]

### Gotchas and Constraints
[bulleted list of things the next session must know - env assumptions, approaches that
failed, deliberate constraints, non-obvious dependencies]

### Repo Snapshot
- Branch: [current branch, or "not a git repo"]
- Uncommitted changes: [yes/no - if yes, brief description of what is staged vs unstaged]
- Recent commits: [last 3-5 commit messages, one line each]

### Suggested First Task
[one concrete action the new session should start with, phrased as a direct instruction]
```

## Step 4 - Re-check for secrets

Before finishing, remove any API key, token, password, token-bearing URL or personal data
the redactor missed. Never include `.env` contents. Then tell the user the file path and
how to use it: paste the prompt into the new chat, or upload `HANDOFF.md` and say "read
the attached file and continue from Suggested First Task".

## Notes

- Be specific. Vague entries like "worked on auth" are not useful. "Added JWT refresh token
  logic in src/auth/refresh.ts; token rotation is implemented but the expiry edge case on
  line 47 is still TODO" is useful.
- Keep decisions and gotchas separate from completed work. The next session needs to know
  what exists (completed work) and what was decided (decisions) as distinct things.
- Keep the user's decisions faithful. If the chat was undecided on something, list it under
  Open Threads, not Decisions.
- If the session was exploratory or inconclusive, say so honestly. "We explored three
  approaches to X; none worked cleanly; the most promising lead is Y" is a valid Current State.
- The Suggested First Task must be unambiguous enough that the new session can act on it
  immediately without clarifying questions.
- Use absolute dates, and keep unverified or conflicting evidence marked as such.
- Do not rely on features of the old tool (slash commands, skills, memory files).
