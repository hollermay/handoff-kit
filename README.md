# handoff-kit

Move an AI chat from one tool to another without re-explaining anything.

It reads a saved chat, strips noise, removes likely secrets, adds a snapshot of your git
repo, and produces either a **context pack** (the cleaned chat plus instructions, ready to
paste) or, through the `/handoff` skill, a distilled **HANDOFF.md** in the format below.
Everything runs locally with Python 3 and no dependencies.

## Use it

```bash
# newest Claude Code chat for this folder -> context pack
python3 -m handoff export --from claude

# include tool calls and what they returned (each result cut at 2,000 chars)
python3 -m handoff export --from claude --tools

# everything, no cuts, no length limit (can be huge)
python3 -m handoff export --from claude --full

# other tools
python3 -m handoff list   --from codex                      # ~/.codex/sessions
python3 -m handoff export --from gemini --pick 2            # ~/.gemini/tmp
python3 -m handoff export --from chatgpt --path ~/Downloads/export.zip --id "my chat title"
```

Useful flags: `--bare` (no pasted-first block), `--out FILE` (`-` prints to the terminal),
`--max-chars N`, `--tool-chars N`, `--no-git`.

## Install the /handoff skill

```bash
npx skills add hollermay/handoff-kit --skill handoff        # pick your tools; add -g for all projects
```

Or from a clone: `python3 -m handoff install-skill --to claude` (or `codex`, or `both`)
copies the skill into `~/.claude/skills/handoff` or `~/.agents/skills/handoff`. It refuses
to overwrite an existing install unless you pass `--force`.

The skill lives in `skills/handoff/` (what `npx skills` installs). It is **generated**:
after changing anything under `handoff/`, run `python3 -m handoff build-skill`. A test fails
if the committed copy is stale.

When you type `/handoff`, the assistant runs the export (chat + repo snapshot), then writes
`HANDOFF.md` with these sections, based on
[this gist](https://gist.github.com/azhar-zuberi/2037b717ac9e65f9e288596689c4d4ab): Project,
Primary Goal, Background, What Was Completed, Decisions Made (and Why), Current State,
Results and Outputs, Open Threads, Gotchas and Constraints, Repo Snapshot, Suggested First
Task. Paste it into the new chat, or upload the file.

## What is verified

| Reader | Status |
|---|---|
| Claude Code (`~/.claude/projects/*/*.jsonl`) | Checked against real session files |
| Codex CLI (`~/.codex/sessions/**/*.jsonl`) | Written from the known format; tested on sample files only |
| Gemini CLI (`~/.gemini/tmp/**/chats/*.json`, `logs.json`) | Written from the known format; tested on sample files only |
| ChatGPT data export (`conversations.json` or the zip) | Written from the known format; tested on sample files only |

Codex and Gemini were not installed where this was built. If a reader finds nothing or
reads the wrong thing, the file layout has probably changed: open an issue with a sample
(secrets removed) or fix the reader in `handoff/sources.py`. Gemini's `logs.json` only
holds your own prompts, not the replies.

## Privacy and limits

- Redaction is pattern based (API keys, tokens, JWTs, bearer tokens, private keys,
  passwords in `NAME=value` and in URLs). It can miss unusual secrets. Skim the output
  before sharing it, especially with `--tools` or `--full`, because tool output often
  contains environment details.
- The default size cap is 60,000 characters. When a chat is longer, the start and the most
  recent part are kept and the middle is dropped at a turn boundary; the stats line says
  how many turns were omitted. Chats with tool results hit the cap sooner.
- The git snapshot uses `git diff --stat`, not the full diff, to stay short and avoid
  copying code or secrets.

## Tests

```bash
python3 -m unittest discover -s tests
```
