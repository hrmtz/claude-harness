#!/usr/bin/env python3
"""Exercise installed Claude's native compaction against a localhost-only API.

No real credentials, user settings, external inference, or tmux are involved.
The mock reports >100k input usage, then checks the native summary replaces
the earlier conversation in the subsequent model request.
"""
import http.server
import json
import pathlib
import shutil
import subprocess
import tempfile
import threading
import unittest


class NativeAutocompactTests(unittest.TestCase):
    def test_native_100k_window_replaces_history(self):
        executable = shutil.which("claude")
        if not executable:
            self.skipTest("Claude Code is not installed")
        executable = str(pathlib.Path(executable).resolve())
        with tempfile.TemporaryDirectory(prefix="formation-native-compact-") as tmp:
            root = pathlib.Path(tmp)
            home = root / "home"
            home.mkdir()
            # Read results may be restored after compaction as recent-file
            # reminders. Plain printf output exercises disposable tool history.
            commands = ["printf '%s' '" + ("HISTORY_PAYLOAD_218 " * 600 if i == 0
                                         else f"Recent fixture {i}.") + "'" for i in range(6)]
            requests = []
            errors = []

            class API(http.server.BaseHTTPRequestHandler):
                def log_message(self, *_):
                    pass

                def do_CONNECT(self):
                    self.send_error(403, "External network disabled")

                def do_POST(self):
                    try:
                        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                        if self.path.split("?")[0] != "/v1/messages":
                            self.send_error(404, "Only local message inference is supported")
                            return
                        requests.append(body)
                        index = len(requests)
                        if index <= len(commands):
                            content = {"type": "tool_use", "id": f"tool_fixture_218_{index}",
                                       "name": "Bash", "input": {"command": commands[index - 1]}}
                            reason, tokens = "tool_use", 120000 if index == len(commands) else 1000 * index
                        else:
                            content = {"type": "text", "text": "COMPACT_SUMMARY_218: fixture printed; finish now."}
                            reason, tokens = "end_turn", 300
                        message = {"id": f"msg_fixture_{index}", "type": "message",
                                   "role": "assistant", "model": body["model"],
                                   "content": [], "stop_reason": None, "stop_sequence": None,
                                   "usage": {"input_tokens": tokens, "output_tokens": 0,
                                             "cache_creation_input_tokens": 0,
                                             "cache_read_input_tokens": 0}}
                        if content["type"] == "tool_use":
                            delta = {"type": "input_json_delta", "partial_json": json.dumps(content["input"])}
                            content = {**content, "input": {}}
                        else:
                            delta = {"type": "text_delta", "text": content["text"]}
                            content = {**content, "text": ""}
                        events = [("message_start", {"type": "message_start", "message": message}),
                                  ("content_block_start", {"type": "content_block_start", "index": 0,
                                                           "content_block": content}),
                                  ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                                           "delta": delta}),
                                  ("content_block_stop", {"type": "content_block_stop", "index": 0}),
                                  ("message_delta", {"type": "message_delta",
                                                      "delta": {"stop_reason": reason, "stop_sequence": None},
                                                      "usage": {"output_tokens": 20}}),
                                  ("message_stop", {"type": "message_stop"})]
                        payload = "".join(f"event: {event}\ndata: {json.dumps(data)}\n\n"
                                          for event, data in events).encode()
                        self.send_response(200)
                        self.send_header("Content-Type", "text/event-stream")
                        self.send_header("Content-Length", str(len(payload)))
                        self.end_headers()
                        self.wfile.write(payload)
                    except Exception as exc:
                        errors.append(str(exc))
                        self.send_error(500)

            server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), API)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            url = f"http://127.0.0.1:{server.server_port}"
            # Explicit allowlist: never inherit auth, shell hooks, or user config.
            env = {"HOME": str(home), "CLAUDE_CONFIG_DIR": str(home / ".claude"),
                   "PATH": "/usr/local/bin:/usr/bin:/bin", "SHELL": "/bin/bash",
                   "LANG": "C.UTF-8", "TMPDIR": str(root),
                   "ANTHROPIC_API_KEY": "offline-test-key", "ANTHROPIC_BASE_URL": url,
                   "HTTP_PROXY": url, "HTTPS_PROXY": url, "ALL_PROXY": url,
                   "NO_PROXY": "127.0.0.1,localhost",
                   "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
                   "DISABLE_AUTOUPDATER": "1"}
            try:
                help_result = subprocess.run([executable, "--help"], env=env, cwd=root,
                                             capture_output=True, text=True, timeout=15)
                if "--autocompact" not in help_result.stdout:
                    self.skipTest("Installed Claude lacks --autocompact")
                argv = [executable, "--bare", "-p", "--autocompact", "100k",
                     "--model", "claude-sonnet-4-6", "--tools", "Bash",
                     "--permission-mode", "bypassPermissions", "--output-format", "stream-json",
                     "--verbose", "--system-prompt", "Print the fixture, then finish.",
                     "Print the supplied fixture."]
                result = subprocess.run(
                    argv,
                    env=env, cwd=root, capture_output=True, text=True, timeout=45,
                )
                compact_requests = requests[:]
                requests.clear()
                argv[argv.index("100k")] = "200k"
                control = subprocess.run(
                    argv, env=env, cwd=root, capture_output=True, text=True, timeout=45,
                )
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)
            self.assertFalse(errors, errors)
            self.assertEqual(result.returncode, 0, result.stderr[-2000:] + result.stdout[-2000:])
            self.assertEqual(control.returncode, 0, control.stderr[-2000:] + control.stdout[-2000:])
            self.assertEqual(len(requests), len(commands) + 1, "200k control unexpectedly compacted")
            self.assertIn("HISTORY_PAYLOAD_218", json.dumps(requests[-1]["messages"]))
            self.assertGreaterEqual(len(compact_requests), len(commands) + 2,
                                    f"Expected normal, compact, resumed calls; got {len(compact_requests)}: {result.stdout[-3000:]}")
            boundaries = [event["compact_metadata"] for line in result.stdout.splitlines()
                          if (event := json.loads(line)).get("subtype") == "compact_boundary"]
            self.assertEqual(len(boundaries), 1)
            self.assertEqual(boundaries[0]["trigger"], "auto")
            self.assertGreater(boundaries[0]["pre_tokens"], 100000)
            self.assertLess(boundaries[0]["post_tokens"], boundaries[0]["pre_tokens"])
            before = json.dumps(compact_requests[len(commands) - 1]["messages"])
            after = json.dumps(compact_requests[-1]["messages"])
            self.assertIn("HISTORY_PAYLOAD_218", before)
            self.assertIn("COMPACT_SUMMARY_218", after)
            self.assertNotIn("HISTORY_PAYLOAD_218", after)
            self.assertLess(len(after), len(before))


if __name__ == "__main__":
    unittest.main()
