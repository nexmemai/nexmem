#!/usr/bin/env python
"""Download required spaCy model for EngramProcessor."""

import subprocess
import sys


def main():
    model = "en_core_web_sm"
    print(f"Downloading spaCy model: {model}")

    try:
        subprocess.check_call(
            [sys.executable, "-m", "spacy", "download", model]
        )
        print(f"Successfully downloaded {model}")
    except subprocess.CalledProcessError as e:
        print(f"Failed to download {model}: {e}")
        print("Try running manually: python -m spacy download en_core_web_sm")
        sys.exit(1)


if __name__ == "__main__":
    main()