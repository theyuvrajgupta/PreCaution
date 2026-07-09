#!/usr/bin/env python3
"""CLI: read a protocol text file, run the full pipeline, print the result as JSON.

Runs all four stages end-to-end (extraction -> per-chemical grounding ->
interaction reasoning -> brief composition) via app.pipeline.run_pipeline,
unlike scripts/run_extraction.py which only exercises Stage 1.

Usage:
    python scripts/run_pipeline.py path/to/protocol.txt
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.extraction import ExtractionError  # noqa: E402
from app.pipeline import run_pipeline  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("protocol_file", type=Path, help="Path to a plain-text protocol file.")
    args = parser.parse_args()

    protocol_text = args.protocol_file.read_text(encoding="utf-8")

    try:
        result = run_pipeline(protocol_text)
    except ExtractionError as exc:
        print(f"Extraction failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    print(json.dumps(result.model_dump(), indent=2))


if __name__ == "__main__":
    main()
