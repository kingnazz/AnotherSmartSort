"""Batch performance benchmark.

The application is designed for folders of hundreds or thousands of PDFs. This
generates a synthetic workload of that size and measures what actually happens,
so throughput and memory claims come from measurement rather than hope.

    python -m scripts.benchmark_batch --pdfs 500
    python -m scripts.benchmark_batch --pdfs 500 --export --json bench.json
    python -m scripts.benchmark_batch --pdfs 50 --quick

Nothing here runs during a normal test run: generating and analysing 500 PDFs
takes minutes and would make the unit suite useless as a fast feedback loop.
"""

from __future__ import annotations

import argparse
import gc
import json
import shutil
import sys
import time
import tracemalloc
from dataclasses import asdict, dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.models.enums import FileStatus  # noqa: E402
from app.models.processing_job import ProcessingJob  # noqa: E402
from app.services.app_services import build_analysis_services, build_export_service  # noqa: E402
from app.storage.settings_store import AppSettings  # noqa: E402
from scripts import sample_data  # noqa: E402


@dataclass
class PhaseResult:
    """Timing and memory for one phase of the benchmark."""

    name: str
    seconds: float = 0.0
    peak_mb: float = 0.0
    items: int = 0

    @property
    def per_minute(self) -> float:
        return (self.items / self.seconds * 60) if self.seconds > 0 else 0.0


@dataclass
class BenchmarkResult:
    """Everything the benchmark measured."""

    pdf_count: int = 0
    page_count: int = 0
    documents: int = 0
    review_documents: int = 0
    failures: int = 0
    exported: int = 0
    generation: PhaseResult = field(default_factory=lambda: PhaseResult("generation"))
    analysis: PhaseResult = field(default_factory=lambda: PhaseResult("analysis"))
    export: PhaseResult = field(default_factory=lambda: PhaseResult("export"))
    memory_growth_mb: float = 0.0

    def to_dict(self) -> dict:
        data = asdict(self)
        data["analysis"]["pdfs_per_minute"] = round(self.analysis.per_minute, 1)
        data["analysis"]["pages_per_minute"] = round(
            (self.page_count / self.analysis.seconds * 60) if self.analysis.seconds else 0.0, 1
        )
        return data


#: A rotating mix so the corpus resembles a real intake rather than one document
#: repeated, which would let caching flatter the numbers.
_SAMPLE_MIX = (
    sample_data.sample_a,   # 10-page full packet
    sample_data.sample_b,   # 3-page resume
    sample_data.sample_c,   # 2-page cover letter
    sample_data.sample_h,   # 4-page transcript + references
    sample_data.sample_g,   # 4-page two candidates
    sample_data.sample_f,   # 5-page separator pages
    sample_data.sample_e,   # 3-page with an ambiguous page
)


def generate_corpus(directory: Path, count: int) -> PhaseResult:
    """Write ``count`` PDFs, cycling through the sample mix."""
    directory.mkdir(parents=True, exist_ok=True)
    phase = PhaseResult("generation", items=count)
    started = time.monotonic()

    for index in range(count):
        factory = _SAMPLE_MIX[index % len(_SAMPLE_MIX)]
        document = factory()
        # Unique names so nothing is skipped as a duplicate path.
        target = directory / f"{index:05d}_{document.filename}"
        sample_data.build_pdf(document, target)
        if (index + 1) % 100 == 0:
            print(f"    generated {index + 1}/{count}")

    phase.seconds = time.monotonic() - started
    return phase


def _rss_mb() -> float:
    """Resident set size in MB, or 0 when it cannot be read on this platform."""
    try:
        import resource

        usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # Linux reports kilobytes; macOS reports bytes.
        return usage / 1024 if sys.platform != "darwin" else usage / (1024 * 1024)
    except Exception:
        try:
            with open("/proc/self/status", encoding="utf-8") as handle:
                for line in handle:
                    if line.startswith("VmHWM:"):
                        return int(line.split()[1]) / 1024
        except OSError:
            pass
    return 0.0


def run_benchmark(
    corpus: Path,
    *,
    settings: AppSettings,
    do_export: bool,
    output: Path | None,
) -> BenchmarkResult:
    from app.services.file_discovery import discover_pdfs

    result = BenchmarkResult()
    pdfs = discover_pdfs([corpus])
    result.pdf_count = len(pdfs)

    services = build_analysis_services(settings)
    job = ProcessingJob(inputs=[str(p) for p in pdfs])

    print(f"  Analysing {len(pdfs)} PDFs...")
    gc.collect()
    baseline = _rss_mb()
    tracemalloc.start()
    started = time.monotonic()

    analyses = []
    completed = 0

    def on_file_complete(analysis) -> None:
        nonlocal completed
        completed += 1
        if completed % 100 == 0:
            elapsed = time.monotonic() - started
            rate = completed / elapsed * 60 if elapsed else 0
            print(f"    {completed}/{len(pdfs)}  ({rate:.0f} PDFs/min, RSS {_rss_mb():.0f} MB)")

    analyses = services.pipeline.analyze_files(
        pdfs, job=job, on_file_complete=on_file_complete
    )

    result.analysis.seconds = time.monotonic() - started
    result.analysis.items = len(analyses)
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    result.analysis.peak_mb = round(peak / (1024 * 1024), 1)
    result.memory_growth_mb = round(max(0.0, _rss_mb() - baseline), 1)

    result.page_count = sum(a.page_count for a in analyses)
    result.documents = sum(len(a.groups) for a in analyses)
    result.review_documents = sum(a.review_group_count for a in analyses)
    result.failures = sum(1 for a in analyses if a.status is FileStatus.ERROR)

    if do_export and output is not None:
        print(f"  Exporting to {output}...")
        exporter = build_export_service(settings)
        started = time.monotonic()
        export_result = exporter.export(analyses, output, job=job)
        result.export.seconds = time.monotonic() - started
        result.export.items = export_result.document_count
        result.exported = export_result.document_count

    services.close()
    return result


def render(result: BenchmarkResult) -> str:
    analysis = result.analysis
    pages_per_minute = (
        result.page_count / analysis.seconds * 60 if analysis.seconds else 0.0
    )
    lines = [
        "",
        "Batch benchmark",
        "===============",
        f"  PDFs                : {result.pdf_count}",
        f"  Pages               : {result.page_count}",
        f"  Documents detected  : {result.documents}",
        f"  Needing review      : {result.review_documents}"
        f" ({result.review_documents / result.documents * 100:.1f}%)"
        if result.documents
        else "  Needing review      : 0",
        f"  Failures            : {result.failures}",
        "",
        "Throughput",
        "----------",
        f"  Generation          : {result.generation.seconds:.1f}s "
        f"({result.generation.per_minute:.0f} PDFs/min)",
        f"  Analysis            : {analysis.seconds:.1f}s",
        f"    PDFs per minute   : {analysis.per_minute:.0f}",
        f"    Pages per minute  : {pages_per_minute:.0f}",
        f"    Per PDF           : {analysis.seconds / result.pdf_count * 1000:.0f} ms"
        if result.pdf_count
        else "",
    ]
    if result.export.seconds:
        lines += [
            f"  Export              : {result.export.seconds:.1f}s "
            f"({result.export.per_minute:.0f} documents/min)",
            f"  Documents exported  : {result.exported}",
        ]
    lines += [
        "",
        "Memory",
        "------",
        f"  Peak Python allocation : {analysis.peak_mb} MB",
        f"  Process RSS growth     : {result.memory_growth_mb} MB",
        "",
    ]
    if result.pdf_count:
        per_pdf_kb = result.memory_growth_mb * 1024 / result.pdf_count
        verdict = (
            "flat -- memory does not scale with batch size"
            if per_pdf_kb < 200
            else "GROWING -- investigate retention per file"
        )
        lines.append(f"  Roughly {per_pdf_kb:.0f} KB retained per PDF: {verdict}")
        lines.append("")
    return "\n".join(line for line in lines if line != "")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pdfs", type=int, default=500, help="How many PDFs to generate.")
    parser.add_argument("--corpus", help="Reuse an existing folder instead of generating one.")
    parser.add_argument("--keep", action="store_true", help="Keep the generated corpus.")
    parser.add_argument("--export", action="store_true", help="Also benchmark exporting.")
    parser.add_argument("--json", help="Write the measurements to this JSON file.")
    parser.add_argument("--quick", action="store_true", help="Shorthand for --pdfs 50.")
    parser.add_argument("--ocr", action="store_true", help="Leave OCR enabled (slower).")
    arguments = parser.parse_args(argv)

    count = 50 if arguments.quick else arguments.pdfs

    settings = AppSettings()
    # OCR is off by default here: it dominates the timings and is measured
    # separately by the OCR integration tests.
    settings.ocr_enabled = arguments.ocr

    import tempfile

    workspace = Path(arguments.corpus) if arguments.corpus else Path(
        tempfile.mkdtemp(prefix="sps-bench-")
    )
    corpus = workspace if arguments.corpus else workspace / "corpus"
    output = workspace / "output"

    print(f"Benchmark workspace: {workspace}")
    result = BenchmarkResult()

    try:
        if not arguments.corpus:
            print(f"  Generating {count} PDFs...")
            result.generation = generate_corpus(corpus, count)

        measured = run_benchmark(
            corpus, settings=settings, do_export=arguments.export, output=output
        )
        measured.generation = result.generation
        result = measured

        print(render(result))

        if arguments.json:
            Path(arguments.json).write_text(
                json.dumps(result.to_dict(), indent=2), encoding="utf-8"
            )
            print(f"  Measurements written to {arguments.json}")
    finally:
        if not arguments.keep and not arguments.corpus:
            shutil.rmtree(workspace, ignore_errors=True)
        else:
            print(f"  Workspace kept at {workspace}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
