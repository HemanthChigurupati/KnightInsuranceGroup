"""Live check: run the full pipeline with the real model on the sample packets.

Usage: make smoke   (needs a configured model in .env; this makes real, billed API calls)
"""

import sys
from pathlib import Path

from app.config import get_settings
from app.ingest.loader import UploadedFile, ingest
from app.llm.client import LLMError, get_llm_client
from app.services.pipeline import process_submission

SAMPLES = Path(__file__).resolve().parents[2] / "sample_submissions"


def main() -> int:
    try:
        llm = get_llm_client(get_settings())
    except LLMError as exc:
        print(f"Cannot run: {exc}")
        return 1
    print(f"Model: {llm.name}\n")
    for packet in sorted(p for p in SAMPLES.iterdir() if p.is_dir()):
        files = [UploadedFile(f.name, f.read_bytes()) for f in sorted(packet.iterdir())]
        result = process_submission(ingest(files), llm)
        print(f"=== {packet.name}: {result.overall_status.value}")
        for s in result.stages:
            print(f"  stage {s.stage:9} {s.state:7} {s.message}")
        for f in result.fields.values():
            print(f"  {f.name:26} {f.state.value:8} {f.value!r}")
            for c in f.candidates:
                mark = "ok " if c.verified else "BAD"
                print(f"      [{mark}] {c.source_file} · {c.evidence.locator}: {c.evidence.excerpt or c.evidence.observation!r}")
        for r in result.rules:
            print(f"  {r.rule_id} {r.status.value:8} {r.reason}")
        print("\n  --- Summary ---\n  " + (result.summary or "(unavailable)").replace("\n", "\n  "))
        print("\n  --- Draft email ---\n  " + (result.draft_email or "(unavailable)").replace("\n", "\n  "))
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
