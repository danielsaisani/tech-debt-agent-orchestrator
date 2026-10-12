#!/usr/bin/env python3
"""Send a signed GitHub `issues` webhook to a local remediation-orchestrator.

Tests the webhook path without ngrok, GitHub or a new issue. Standard library
only, so it runs without the project's virtualenv:

    python3 scripts/send_webhook.py --issue 7
    python3 scripts/send_webhook.py --issue 8 --scenario no_pr   # simulation mode

The secret and repository come from --secret/--repo, then the environment
(GITHUB_WEBHOOK_SECRET, TARGET_REPO), then ./.env, then the simulation defaults.
"""

import argparse
import hashlib
import hmac
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

SIMULATION_DEFAULTS = {"GITHUB_WEBHOOK_SECRET": "simulate", "TARGET_REPO": "simulated/superset"}


def setting(name: str, env_file: Path = Path(".env")) -> str:
    if os.environ.get(name):
        return os.environ[name]
    if env_file.is_file():
        for line in env_file.read_text().splitlines():
            key, _, value = line.partition("=")
            if key.strip() == name and value.strip():
                return value.strip().strip("'\"")
    return SIMULATION_DEFAULTS[name]


def build_payload(
    *, repo: str, issue: int, title: str, body: str, action: str, label: str
) -> dict:
    payload = {
        "action": action,
        "issue": {
            "number": issue,
            "title": title,
            "body": body,
            "html_url": f"https://github.com/{repo}/issues/{issue}",
            "labels": [{"name": label}],
        },
        "repository": {"full_name": repo},
    }
    if action == "labeled":
        payload["label"] = {"name": label}
    return payload


def signature(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default="http://localhost:8000/webhooks/github")
    parser.add_argument("--issue", type=int, default=1, help="issue number (default: 1)")
    parser.add_argument("--title", default="Remove dead code in the legacy chart helpers")
    parser.add_argument("--body", default="Sent by scripts/send_webhook.py for local testing.")
    parser.add_argument("--action", choices=["opened", "labeled"], default="labeled")
    parser.add_argument("--label", default="tech-debt")
    parser.add_argument("--repo", help="owner/name (default: TARGET_REPO)")
    parser.add_argument("--secret", help="webhook secret (default: GITHUB_WEBHOOK_SECRET)")
    parser.add_argument(
        "--scenario",
        help="simulation mode only: outcome to simulate, e.g. no_pr, needs_input, "
        "session_error, usage_limit, create_failed, timeout",
    )
    args = parser.parse_args(argv)
    args.repo = args.repo or setting("TARGET_REPO")
    args.secret = args.secret or setting("GITHUB_WEBHOOK_SECRET")
    if args.scenario:
        args.title = f"{args.title} [sim:{args.scenario}]"
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    payload = build_payload(
        repo=args.repo, issue=args.issue, title=args.title, body=args.body,
        action=args.action, label=args.label,
    )
    body = json.dumps(payload).encode()
    request = urllib.request.Request(
        args.url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": "issues",
            "X-Hub-Signature-256": signature(args.secret, body),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            status, text = response.status, response.read().decode()
    except urllib.error.HTTPError as error:
        status, text = error.code, error.read().decode()
    except urllib.error.URLError as error:
        print(f"could not reach {args.url}: {error.reason}", file=sys.stderr)
        return 1
    print(f"{status} {text}")
    return 0 if status < 400 else 1


if __name__ == "__main__":
    sys.exit(main())
