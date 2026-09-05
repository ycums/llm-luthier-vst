#!/usr/bin/env python3
"""Fetch and verify the golden corpus described in corpus/manifest.json.

For each manifest entry:
  - bundled entries are read from their bundled_path (already in the repo)
  - non-bundled entries are downloaded from download_url into
    corpus/.cache/<id>.<format> (skipped if already present)
Every entry's SHA256 is then verified against the manifest. Any mismatch,
missing bundled file, or download failure causes this script to exit with
a non-zero status.

Stdlib only (urllib, hashlib, json) -- see AGENTS.md #5 on not adding new
external dependencies without an ADR.

Usage:
    python3 corpus/fetch_and_verify.py            # download missing + verify all
    python3 corpus/fetch_and_verify.py --no-fetch # verify only what's already local
"""
import argparse
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
MANIFEST_PATH = os.path.join(HERE, "manifest.json")
CACHE_DIR = os.path.join(HERE, ".cache")
REPO_ROOT = os.path.dirname(HERE)
USER_AGENT = "llm-luthier-vst-corpus-fetch/1.0 (https://github.com/ycums/llm-luthier-vst)"


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def local_path_for(entry):
    if entry["bundled"]:
        return os.path.join(REPO_ROOT, entry["bundled_path"])
    return os.path.join(CACHE_DIR, f"{entry['id']}.{entry['format']}")


def download(url, dest, tries=5, initial_delay=3):
    """Download url to dest, retrying with exponential backoff on HTTP 429
    (observed in practice against the Wikimedia CDN during curation of
    this corpus -- a single request is not reliable enough for CI)."""
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    delay = initial_delay
    last_err = None
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp, open(dest, "wb") as out:
                out.write(resp.read())
            return
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code == 429 and attempt < tries - 1:
                time.sleep(delay)
                delay = min(delay * 2, 30)
                continue
            raise
    raise last_err


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--no-fetch", action="store_true",
        help="Do not download missing non-bundled entries; only verify what's already local.",
    )
    args = parser.parse_args()

    with open(MANIFEST_PATH, encoding="utf-8") as f:
        manifest = json.load(f)

    failures = []
    for entry in manifest["entries"]:
        eid = entry["id"]
        path = local_path_for(entry)

        if not os.path.exists(path):
            if entry["bundled"]:
                failures.append(f"{eid}: bundled file missing at {path}")
                continue
            if args.no_fetch:
                failures.append(f"{eid}: not present locally and --no-fetch given ({path})")
                continue
            print(f"[fetch] {eid} <- {entry['download_url']}")
            try:
                download(entry["download_url"], path)
            except Exception as e:  # noqa: BLE001 - report and continue to next entry
                failures.append(f"{eid}: download failed: {e}")
                continue

        actual = sha256_of(path)
        expected = entry["sha256"]
        if actual != expected:
            failures.append(
                f"{eid}: SHA256 mismatch for {path}\n"
                f"  expected: {expected}\n"
                f"  actual:   {actual}"
            )
        else:
            print(f"[ok]    {eid}: {path} sha256 matches manifest")

    if failures:
        print("\n=== VERIFICATION FAILED ===", file=sys.stderr)
        for msg in failures:
            print(f" - {msg}", file=sys.stderr)
        return 1

    print(f"\nAll {len(manifest['entries'])} corpus entries verified OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
