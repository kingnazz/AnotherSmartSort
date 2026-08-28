"""Generate the synthetic sample PDFs used for manual QA.

    python scripts/generate_samples.py [output_dir]

Everything produced is invented test data. No real applicant documents are ever
used or committed.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.sample_data import ALL_SAMPLES, build_pdf  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "output_dir",
        nargs="?",
        default=str(ROOT / "samples"),
        help="Where to write the sample PDFs (default: ./samples)",
    )
    args = parser.parse_args(argv)

    destination = Path(args.output_dir)
    destination.mkdir(parents=True, exist_ok=True)

    print(f"Writing sample PDFs to {destination}\n")
    for factory in ALL_SAMPLES:
        document = factory()
        path = build_pdf(document, destination / document.filename)
        expected = (
            ", ".join(
                f"{name} p{start}-{end}" for name, start, end in document.expected_groups
            )
            or "no expectation recorded"
        )
        print(f"  {path.name}")
        print(f"      {document.page_count} pages — {document.description}")
        print(f"      expected: {expected}\n")

    print(f"Done. {len(ALL_SAMPLES)} sample PDFs written.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
