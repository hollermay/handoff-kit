import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from handoff import cli, git, sources  # noqa: E402
from handoff.redact import redact  # noqa: E402
from handoff.render import render  # noqa: E402


def write_jsonl(path, rows):
    with open(path, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


class Tmp(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.dir = self._d.name

    def tearDown(self):
        self._d.cleanup()

    def p(self, name):
        return os.path.join(self.dir, name)


class RedactTests(unittest.TestCase):
    def test_common_secrets(self):
        text = ("sk-abcdefghijklmnop1234 GROQ_API_KEY=gsk_abcdefghijklmnopqrstuv12 "
                "Bearer abcdefghijklmnopqrstuvwxyz1 AKIAABCDEFGHIJKLMNOP "
                "postgres://user:hunter2pass@host/db ghp_abcdefghijklmnopqrstuvwx")
        out = redact(text)
        for leaked in ("sk-abcdef", "gsk_abcdef", "abcdefghijklmnopqrstuvwxyz1", "AKIAABCDEFG", "hunter2pass", "ghp_abcdef"):
            self.assertNotIn(leaked, out)
        self.assertIn("GROQ_API_KEY=[REDACTED]", out)

    def test_plain_text_untouched(self):
        self.assertEqual(redact("Use a token bucket for rate limits."), "Use a token bucket for rate limits.")


class ClaudeTests(Tmp):
    def rows(self):
        return [
            {"type": "ai-title", "aiTitle": "Demo chat"},
            {"type": "user", "message": {"content": "Plan the app <system-reminder>noise</system-reminder>"}},
            {"type": "assistant", "message": {"content": [
                {"type": "thinking", "thinking": "secret thoughts"},
                {"type": "text", "text": "Sure, writing it."},
                {"type": "tool_use", "name": "Write", "input": {"file_path": "/tmp/a.py", "content": "x"}},
                {"type": "text", "text": "Done."}]}},
            {"type": "user", "message": {"content": [{"type": "tool_result", "content": "wrote file ok"}]}},
            {"type": "assistant", "isSidechain": True, "message": {"content": "subagent chatter"}},
            {"type": "user", "isMeta": True, "message": {"content": "meta"}},
        ]

    def test_default_hides_tools_and_noise(self):
        path = self.p("s.jsonl")
        write_jsonl(path, self.rows())
        conv = sources.claude_load(sources.Ref("claude", path))
        self.assertEqual(conv.title, "Demo chat")
        self.assertEqual([r for r, _ in conv.turns], ["USER", "ASSISTANT"])
        self.assertEqual(conv.turns[0][1], "Plan the app")
        self.assertNotIn("secret thoughts", conv.turns[1][1])
        self.assertNotIn("subagent", json.dumps(conv.turns))
        self.assertEqual(conv.files, ["/tmp/a.py"])

    def test_tools_mode_keeps_calls_and_results_in_order(self):
        path = self.p("s.jsonl")
        write_jsonl(path, self.rows())
        conv = sources.claude_load(sources.Ref("claude", path), tools=True, tool_chars=10)
        roles = [r for r, _ in conv.turns]
        self.assertEqual(roles, ["USER", "ASSISTANT", "TOOL CALL", "ASSISTANT", "TOOL RESULT"])
        self.assertTrue(conv.turns[2][1].startswith("Write "))
        self.assertIn("more characters cut", conv.turns[2][1])  # clipped at 10
        self.assertTrue(conv.turns[4][1].startswith("wrote file\n[... 3 more characters cut"))

    def test_find_picks_newest(self):
        a, b = self.p("a.jsonl"), self.p("b.jsonl")
        write_jsonl(a, self.rows())
        write_jsonl(b, self.rows())
        os.utime(a, (1, 1))
        refs = sources.claude_find(self.dir)
        self.assertEqual(refs[0].path, b)


class CodexTests(Tmp):
    def test_new_layout_with_tools(self):
        path = self.p("rollout.jsonl")
        write_jsonl(path, [
            {"type": "session_meta", "payload": {"cwd": "/x"}},
            {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [
                {"type": "input_text", "text": "<environment_context>cwd</environment_context>"}]}},
            {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [
                {"type": "input_text", "text": "Fix the bug"}]}},
            {"type": "response_item", "payload": {"type": "function_call", "name": "shell",
                                                   "arguments": "{\"command\":[\"pytest\"]}"}},
            {"type": "response_item", "payload": {"type": "function_call_output",
                                                   "output": "{\"output\":\"3 passed\",\"metadata\":{}}"}},
            {"type": "response_item", "payload": {"type": "custom_tool_call", "name": "apply_patch",
                                                   "input": "*** Update File: src/app.py\n@@"}},
            {"type": "response_item", "payload": {"type": "message", "role": "assistant", "content": [
                {"type": "output_text", "text": "Fixed."}]}},
            {"type": "event_msg", "payload": {"type": "user_message", "message": "Fix the bug"}},
            {"type": "event_msg", "payload": {"type": "agent_message", "message": "Fixed."}},
        ])
        ref = sources.Ref("codex", path)
        plain = sources.codex_load(ref)
        self.assertEqual(plain.turns, [("USER", "Fix the bug"), ("ASSISTANT", "Fixed.")])  # no duplicates
        self.assertEqual(plain.files, ["src/app.py"])
        full = sources.codex_load(ref, tools=True)
        self.assertEqual([r for r, _ in full.turns],
                         ["USER", "TOOL CALL", "TOOL RESULT", "TOOL CALL", "ASSISTANT"])
        self.assertEqual(full.turns[2][1], "3 passed")

    def test_old_layout_and_event_fallback(self):
        old = self.p("old.jsonl")
        write_jsonl(old, [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": "hi"}]},
                          {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "hello"}]}])
        self.assertEqual(sources.codex_load(sources.Ref("codex", old)).turns, [("USER", "hi"), ("ASSISTANT", "hello")])
        ev = self.p("ev.jsonl")
        write_jsonl(ev, [{"type": "event_msg", "payload": {"type": "user_message", "message": "q"}},
                         {"type": "event_msg", "payload": {"type": "agent_message", "message": "a"}}])
        self.assertEqual(sources.codex_load(sources.Ref("codex", ev)).turns, [("USER", "q"), ("ASSISTANT", "a")])


class GeminiTests(Tmp):
    def test_session_file(self):
        path = self.p("session-1.json")
        with open(path, "w") as fh:
            json.dump({"sessionId": "s1", "messages": [
                {"type": "user", "content": "Explain X"},
                {"type": "gemini", "content": "X is Y", "toolCalls": [
                    {"name": "read_file", "args": {"path": "a.txt"}, "resultDisplay": "file body"}]},
                {"type": "info", "content": "ignore me"}]}, fh)
        ref = sources.gemini_find(path)[0]
        plain = sources.gemini_load(ref)
        self.assertEqual(plain.turns, [("USER", "Explain X"), ("ASSISTANT", "X is Y")])
        full = sources.gemini_load(ref, tools=True)
        self.assertEqual([r for r, _ in full.turns], ["USER", "ASSISTANT", "TOOL CALL", "TOOL RESULT"])

    def test_logs_json_groups_by_session(self):
        path = self.p("logs.json")
        with open(path, "w") as fh:
            json.dump([{"sessionId": "a", "type": "user", "message": "one"},
                       {"sessionId": "b", "type": "user", "message": "two"},
                       {"sessionId": "a", "type": "user", "message": "three"}], fh)
        refs = sources.gemini_find(path)
        self.assertEqual({r.key for r in refs}, {"a", "b"})
        a = [r for r in refs if r.key == "a"][0]
        self.assertEqual([t for _, t in sources.gemini_load(a).turns], ["one", "three"])


class ChatGPTTests(Tmp):
    def conv(self):
        def node(i, parent, role, text, **extra):
            msg = {"author": {"role": role}, "content": {"content_type": "text", "parts": [text]},
                   "create_time": i}
            msg.update(extra)
            return {"id": i, "parent": parent, "message": msg}
        mapping = {
            "root": {"id": "root", "parent": None, "message": None},
            "u1": node(1, "root", "user", "Question"),
            "a_old": node(2, "u1", "assistant", "OLD ANSWER (edited away)"),
            "a_new": node(3, "u1", "assistant", "New answer"),
            "t1": node(4, "a_new", "tool", "42", ),
            "h": node(5, "t1", "assistant", "hidden", metadata={"is_visually_hidden_from_conversation": True}),
            "u2": node(6, "h", "user", "Thanks"),
        }
        mapping["t1"]["message"]["author"]["name"] = "python"
        mapping["t1"]["message"]["content"] = {"content_type": "execution_output", "text": "42"}
        return {"title": "My chat", "update_time": 10, "current_node": "u2", "mapping": mapping}

    def test_follows_current_branch_and_skips_hidden(self):
        path = self.p("conversations.json")
        with open(path, "w") as fh:
            json.dump([self.conv()], fh)
        ref = sources.chatgpt_find(path)[0]
        conv = sources.chatgpt_load(ref)
        self.assertEqual(conv.title, "My chat")
        self.assertEqual(conv.turns, [("USER", "Question"), ("ASSISTANT", "New answer"), ("USER", "Thanks")])
        full = sources.chatgpt_load(ref, tools=True)
        self.assertIn(("TOOL RESULT", "python: 42"), full.turns)

    def test_reads_zip(self):
        path = self.p("export.zip")
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("conversations.json", json.dumps([self.conv()]))
        self.assertEqual(len(sources.chatgpt_find(path)), 1)

    def test_requires_path(self):
        with self.assertRaises(SystemExit):
            sources.chatgpt_find(None)


class RenderTests(unittest.TestCase):
    def conv(self, n=40, size=400):
        turns = []
        for i in range(n):
            turns.append(("USER" if i % 2 == 0 else "ASSISTANT", ("turn%d " % i) + "x" * size))
        return sources.Conversation("Claude Code", "t", turns, ["/a/b.py"])

    def test_trim_keeps_start_and_end_at_turn_boundaries(self):
        doc, stats = render(self.conv(), max_chars=6000)
        self.assertGreater(stats["omitted"], 0)
        self.assertIn("turn0 ", doc)
        self.assertIn("turn39 ", doc)
        self.assertNotIn("turn20 ", doc)
        self.assertIn("omitted for length", doc)

    def test_zero_means_no_limit(self):
        doc, stats = render(self.conv(), max_chars=0)
        self.assertEqual(stats["omitted"], 0)
        self.assertIn("turn20 ", doc)

    def test_bare_has_no_preamble_but_keeps_repo_and_files(self):
        doc, _ = render(self.conv(2), bare=True, repo="- Branch: main")
        self.assertNotIn("Paste this first", doc)
        self.assertIn("- Branch: main", doc)
        self.assertIn("/a/b.py", doc)

    def test_tool_note_only_when_tools_present(self):
        c = self.conv(2)
        self.assertNotIn("TOOL CALL", render(c)[0])
        c.turns.append(("TOOL CALL", "ls"))
        self.assertIn("what the previous assistant ran", render(c)[0])

    def test_secrets_removed_from_output(self):
        c = sources.Conversation("x", "t", [("USER", "my key is sk-abcdefghijklmnop1234")])
        self.assertNotIn("sk-abcdef", render(c)[0])


class GitTests(Tmp):
    def git(self, *args):
        subprocess.run(["git", "-C", self.dir] + list(args), check=True, capture_output=True,
                       env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
                                GIT_COMMITTER_EMAIL="t@t"))

    def test_not_a_repo(self):
        self.assertEqual(git.snapshot(self.dir), "")

    def test_repo_without_commits_and_with_changes(self):
        self.git("init", "-q")
        with open(self.p("a.txt"), "w") as fh:
            fh.write("1")
        snap = git.snapshot(self.dir)
        self.assertIn("Branch:", snap)
        self.assertIn("a.txt", snap)
        self.assertIn("Recent commits: none yet", snap)
        self.git("add", "a.txt")
        self.git("commit", "-q", "-m", "first commit")
        with open(self.p("a.txt"), "w") as fh:
            fh.write("2")
        snap = git.snapshot(self.dir)
        self.assertIn("first commit", snap)
        self.assertIn("a.txt", snap)


class CliTests(Tmp):
    def test_export_and_list_end_to_end(self):
        path = self.p("s.jsonl")
        write_jsonl(path, [{"type": "user", "message": {"content": "Hello there"}},
                           {"type": "assistant", "message": {"content": [{"type": "text", "text": "Hi!"}]}}])
        out = self.p("out.md")
        self.assertEqual(cli.main(["export", "--from", "claude", "--path", path, "--out", out, "--no-git"]), 0)
        text = open(out).read()
        self.assertIn("Paste this first", text)
        self.assertIn("Hello there", text)
        self.assertEqual(cli.main(["list", "--from", "claude", "--path", self.dir]), 0)

    def test_empty_chat_is_an_error(self):
        path = self.p("s.jsonl")
        write_jsonl(path, [{"type": "mode"}])
        with self.assertRaises(SystemExit):
            cli.main(["export", "--from", "claude", "--path", path, "--no-git"])

    def test_install_skill_is_self_contained_and_refuses_overwrite(self):
        dest = self.p("skill")
        self.assertEqual(cli.main(["install-skill", "--to", "claude", "--dest", dest]), 0)
        skill = open(os.path.join(dest, "SKILL.md")).read()
        self.assertIn("allowed-tools: [Bash, Read, Write]", skill)
        self.assertIn(dest + "/scripts", skill)
        self.assertNotIn("{{", skill)
        env = dict(os.environ, PYTHONPATH=os.path.join(dest, "scripts"))
        r = subprocess.run([sys.executable, "-m", "handoff", "--help"], env=env, capture_output=True, text=True, cwd="/")
        self.assertEqual(r.returncode, 0, r.stderr)
        marker = os.path.join(dest, "keep.txt")
        open(marker, "w").close()
        cli.main(["install-skill", "--to", "claude", "--dest", dest])
        self.assertTrue(os.path.exists(marker))  # not overwritten without --force
        cli.main(["install-skill", "--to", "codex", "--dest", self.p("codex_skill")])
        self.assertNotIn("allowed-tools", open(self.p("codex_skill/SKILL.md")).read())


def tree(root):
    """{relative path: bytes} for every file, ignoring caches."""
    out = {}
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for f in files:
            if not f.endswith(".pyc"):
                full = os.path.join(base, f)
                with open(full, "rb") as fh:
                    out[os.path.relpath(full, root)] = fh.read()
    return out


class PortableSkillTests(Tmp):
    def test_portable_text_has_no_unfilled_placeholders(self):
        text = cli.skill_text()
        self.assertNotIn("{{", text)
        self.assertIn('PYTHONPATH="<SKILL_DIR>/scripts"', text)
        self.assertIn("--from <TOOL>", text)
        self.assertIn("`<TOOL>` is the tool you are running", text)
        self.assertTrue(text.startswith("---\nname: handoff\n"))
        self.assertIn("\nallowed-tools:", text.split("\n---\n")[0])

    def test_committed_skill_matches_source(self):
        """skills/handoff is generated; fail if someone edited the source and forgot to rebuild."""
        built = self.p("built")
        cli.write_skill(built, cli.skill_text())
        committed = os.path.join(ROOT, "skills", "handoff")
        self.assertTrue(os.path.isdir(committed), "run: python3 -m handoff build-skill")
        self.assertEqual(sorted(tree(built)), sorted(tree(committed)))
        for name, data in tree(built).items():
            self.assertEqual(data, tree(committed)[name], "stale: %s (run build-skill)" % name)


if __name__ == "__main__":
    unittest.main()
