#!/usr/bin/env python3
"""One-shot chat sanity check against Sarvam.

    python chat.py "नमस्ते, आप कैसे हैं?"
    python chat.py --offline          # no key needed
"""
from __future__ import annotations

import argparse
import sys

from companion import config
from companion.offline_client import OfflineSarvam
from companion.sarvam_client import SarvamClient, SarvamError


def main() -> int:
    parser = argparse.ArgumentParser(description="Ask Sarvam one question")
    parser.add_argument("prompt", nargs="?", default="Say hello in Hindi in one short sentence.")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--model", default=config.CHAT_MODEL)
    args = parser.parse_args()

    if args.offline or not config.have_sarvam_key():
        client = OfflineSarvam({"name": "Ramesh", "language_code": "hi-IN"})
        print("(offline engine)")
    else:
        client = SarvamClient()

    messages = [
        {"role": "system", "content": "You are a concise assistant. Answer in the user's language."},
        {"role": "user", "content": args.prompt},
    ]
    try:
        print(client.chat(messages, model=args.model))
    except SarvamError as exc:
        print(f"Sarvam error: {exc}", file=sys.stderr)
        return 1
    except config.ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
