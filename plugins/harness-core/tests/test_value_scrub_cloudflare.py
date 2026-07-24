#!/usr/bin/env python3
"""gh #50: credential_value_scrub must catch Cloudflare API tokens."""
import json
import os
import subprocess
import tempfile


HOOK = os.path.join(
    os.path.dirname(__file__), "..", "hooks", "credential_value_scrub.sh"
)
TOKEN = "cfat_" + "A1b2" * 6
PLACEHOLDER = "cfat_example" + "A1b2" * 4


def run(jsonl_line):
    with tempfile.TemporaryDirectory() as home:
        transcript_dir = os.path.join(home, ".claude", "projects", "p")
        os.makedirs(transcript_dir)
        transcript = os.path.join(transcript_dir, "sess.jsonl")
        with open(transcript, "w", encoding="utf-8") as handle:
            handle.write(jsonl_line + "\n")

        hook_input = json.dumps(
            {
                "tool_response": {"stdout": jsonl_line},
                "transcript_path": transcript,
                "tool_input": {"command": "echo test"},
            }
        )
        subprocess.run(
            ["bash", HOOK],
            input=hook_input,
            capture_output=True,
            check=True,
            text=True,
            env=dict(os.environ, HOME=home),
        )
        with open(transcript, encoding="utf-8") as handle:
            return handle.read()


def main():
    output = run(f"Cloudflare API token: {TOKEN}")
    assert TOKEN not in output, "Cloudflare API token survived scrubbing"
    assert "cfat_<REDACTED>" in output, "Cloudflare token replacement is missing"

    output = run(PLACEHOLDER)
    assert PLACEHOLDER in output, "allowlisted example token was scrubbed"
    assert "cfat_<REDACTED>" not in output

    print("value_scrub Cloudflare (#50): ALL PASS ✓")


if __name__ == "__main__":
    main()
