# Implementation Status

**Version:** 1.0.1
**Last updated:** 2026-08-28
**Product name:** AS Resume Sorter (renamed from Smart PDF Sorter — see
[Naming](#naming))
**Phase:** final polish — complete. One of the three parsers is verified against
real client files; the other two are not, and this document says which is which
everywhere it matters.

---

## How to read this document

Every claim below is tagged with how it was actually verified. This matters: a
feature that passes a unit test with a stubbed dependency is not the same as one
proven on a clean Windows machine.

| Tag | Meaning |
|-----|---------|
| **[real-client]** | Run against actual client PDFs, not reproductions |
| **[unit]** | Proven by automated tests with stubs or synthetic data |
| **[win-ci]** | Proven on a clean `windows-latest` GitHub Actions runner |
| **[ocr-real]** | Proven by running an actual Tesseract binary |
| **[synthetic]** | Behaviour verified, but only against generated documents |
| **[measured]** | A number produced by running the thing, not an estimate |
| **[untested]** | Implemented, but not yet exercised — say so out loud |

**What real client data exists here.** Five real ATS application reports sit in
the git-ignored `qa/input/`, and the separator-page parser is verified against
them. The two files the PageUp and packet parsers were built for — a 104-page
bulk compile and a 17-file per-applicant corpus — **have never been present in
any development environment for this project**, so those
two parsers are verified against synthetic reproductions of their structures
and nothing more. A synthetic fixture proves the parser does what its author
believed the format does; it cannot prove the belief was right.

---

## Current state

The product's promise is now: **take real ATS applicant PDFs and reliably
extract the useful attached documents into folders, in one click.** That path
is implemented and verified directly against the client's own real files.

### Phase 4: parser registry, PageUp, packets, drag-and-drop review

Structured parsing moved from one class to a registry of format-specific
parsers (`app/services/parsers/`). Three formats are recognised; anything
else falls through to the generic pipeline, which now gets a whole-file
anchor pass before page classification.

| Parser | Format | Verified against |
|--------|--------|------------------|
| `UCSeparatorExportParser` | Separator-page ATS export | **5 real client PDFs** **[real-client]** |
| `PageUpBulkCompileParser` | PageUp People bulk compile | Synthetic reproduction only **[synthetic]** |
| `SubmittedApplicantPacketParser` | Submitted applicant packet | Synthetic reproduction only **[synthetic]** |

**Important caveat, restated because it decides how to read everything below.**
The real 104-page PageUp bulk compile and the real 17-file per-applicant
corpus were **not available** in this or any previous development environment. Both parsers were built to the
structures documented in the phase specification and verified against synthetic
fixtures that reproduce those structures page for page -- including the exact
ground-truth page ranges the specification lists. That is not the same as
running the real files, and no number in the two tables below is a production
accuracy figure. See [What is needed next](#what-is-needed-next).

**Synthetic PageUp bulk compile** (104 pages, 14 applicants) **[synthetic]**:

| Measure | Result |
|---------|-------:|
| Applicants detected | 14 / 14 |
| Application Reports | 14 / 14, exact page ranges |
| Resumes | 14 / 14, exact page ranges |
| Cross-applicant page leakage | 0 |
| Cover page exported | never |
| Documents needing review | 0 |
| OCR pages | 0 of 104 |
| Analysis time | 0.12 s |

Every range matches the specification's ground-truth table (Peter 2-6/7-8,
the ninth applicant 58-65/66-68, Terry 99-102/103-104, and so on).

**Synthetic applicant-packet corpus** (17 files, 161 pages) **[synthetic]**:

| Measure | Result |
|---------|-------:|
| Logical documents | 44 / 44 exact |
| Application Reports | 17 / 17 |
| Resumes | 16 / 16 |
| Cover Letters | 10 / 10 |
| Transcripts | 1 / 1 |
| Resume invented for the attachment-less applicant | no |
| Two-page cover letter with sparse signature page | kept whole |
| Concatenated 4-packet batch | 11 / 11 exact, no leakage |
| OCR pages | 0 of 161 |

**PageUp attachments when the cover declares more than one type**
**[synthetic]**. The first cut of this parser made exactly one attachment per
applicant, which is right only when the cover compiled a single type. With
several it merged a cover letter and a resume into one document named after
whichever won a scoring contest. The region is now divided on what the file
states before what a page looks like -- the uploaded file's own name, a printed
title naming a different type, a restart of page numbering, then the shape of
the text -- and where none of that appears the pages stay together and go to
review. `tests/test_pageup_parser.py::TestMultipleAttachments` covers:

| Case | Result |
|------|--------|
| Application + Resume | exact ranges |
| Application + Cover Letter + Resume | exact ranges |
| Application + Cover Letter + Resume + Transcript | exact ranges |
| Multi-page cover letter | one document, not three |
| Multi-page resume | one document |
| Cover letter ending on a signature-only page | stays with its letter |
| Attachment identified only by its filename | typed from the filename |
| Declared two types, applicant uploaded one | no second document invented |
| Applicant uploaded nothing | application form only |
| Cover letter + resume merged into one numbered upload | kept whole, flagged |
| Attachment matching no declared type clearly | typed best-effort, flagged |
| Single declared type (the 104-page file) | never split — 14/14 unchanged |
| Resume pages numbered by contact block, not footers | 14 whole resumes |
| A covering note pasted inside a declared resume | stays a resume, no invented cover letter |

Each row was checked by reverting the code it covers and confirming the test
fails. That is worth stating because the first pass at the last two rows did
*not* fail on reversion — the fixtures were too easy — and had to be rebuilt
around the layouts that actually break a splitter.

**Popup / console windows.** Every child process now goes through
`app/utils/external_process.run_hidden`, which applies `CREATE_NO_WINDOW` and
`STARTF_USESHOWWINDOW`/`SW_HIDE` together. A source audit walks every module's
AST and fails if anything outside that helper starts a process **[unit]**.
Blank pages no longer trigger OCR at all, which is what made launches frequent
on a native-text file. Verified hidden on Windows CI **[win-ci]**; the visual
absence of a flash in the installed MSI is **[untested]** -- it cannot be
observed from CI.

**Review workspace.** Type-first drag-and-drop board is the default view, with
whole-document drags between type lanes, page drags between documents,
ctrl/shift multi-select, right-click equivalents, and undo/redo over every
correction **[unit]**. Unsafe drops are refused with a reason rather than
performed **[unit]**. A document can also be dragged onto a candidate in the
candidate view to refile it **[unit]**.

Thumbnails are requested only for cards on screen (plus three quarters of a
viewport beyond, so scrolling meets pictures rather than placeholders), with a
bounded, cancellable render queue **[unit] [measured]**. Scrolling re-scans,
debounced at 90 ms so a scrollbar drag queues one batch rather than one per
pixel.

**Measured on a synthetic 301-page, 90-document board** (30 applicants ×
application + cover letter + resume), offscreen Qt on this development
container **[measured]**:

| Operation | Time |
|-----------|-----:|
| Analyse 301 pages | 0.40 s |
| Build the whole board (90 cards) | 517 ms |
| Rebuild after one correction | 524 ms |
| One visibility scan (runs per scroll) | 0.6 ms |
| Filter the board to one type | 155 ms |
| Pages requested on first paint | 100 of 301 |

Reproduce with `pytest tests/test_large_review.py -s`. Every correction still
rebuilds the whole board; at half a second for ninety documents that is
acceptable and was left alone rather than replaced with a virtualised model
whose complexity is not yet justified by a measurement.

### Production hardening pass (this phase)

Seven defects were found by going back over Phase 4 looking for them rather
than by a test failing. All seven are fixed, each with a regression test that
fails when the fix is reverted (checked by reverting it).

| # | Defect | Consequence if shipped | Fixed in |
|---|--------|------------------------|----------|
| 1 | "Move to candidate…" opened the new-candidate prompt | Refiling a document under an existing applicant was unreachable from the board | `app/ui/review_view.py`, `app/ui/widgets/type_board.py` |
| 2 | Packet actions resolved against the *selected* file, not the document's own | On a multi-file batch, a correction could act on the wrong file | `app/ui/review_view.py` |
| 3 | Undo rebuilt packets after restoring them | Undo returned a document to a candidate with a new identity, so it looked like a different person | `app/ui/review_view.py` |
| 4 | Undo restored only three page fields | Type, review flags, boundary and identity corrections survived an undo that claimed to take them back | `app/services/correction_history.py` |
| 5 | The PageUp bulk cover obeyed the separator policy | With separators set to "keep", the file's manifest — every applicant's name — was exported inside one applicant's resume | `app/services/parsers/pageup.py` |
| 6 | One attachment segment per applicant | A cover letter and a resume compiled together came out as one mislabelled document | `app/services/parsers/pageup.py`, `app/services/parsers/attachments.py` |
| 7 | A parser's page-level review flag never reached the document | A parser could say "I am not sure about this one" and the reviewer would never be told | `app/services/grouping_service.py` |

Two further gaps in the review workspace, both in the thumbnail path:
scrolling never re-asked for thumbnails (they were requested once, on load), and
a thumbnail that finished rendering was cached without being painted onto the
board. Either alone leaves a large file showing placeholders indefinitely.

### Final polish pass (this phase)

Two product changes, no behaviour changed in parsing, classification or OCR.

**One run, one folder.** Every Sort & Save now creates a new folder inside the
chosen output directory, named for the moment the run started —
`2026-08-26_10-32-AM`. Before this, a second run into the same directory
interleaved with the first and afterwards nothing said which resume came from
which batch. The folder is created once per batch rather than per PDF, is
created atomically so two runs in the same minute cannot land in the same place,
and is only created once there is something to export — a run that fails
validation leaves no empty folder behind to be mistaken for a finished export.
Everything that reports a destination reports the run folder: the completion
dialog, the status line, **Open Output Folder**, the processing history, the
Excel index, and the log. **[unit]**

**Renamed to AS Resume Sorter.** Window title, header, About, Settings, the
update row, the installer, Programs and Features, Start Menu and desktop
shortcuts, the install path, the EXE's Windows metadata, the README and the
GitHub Release title. See [Naming](#naming) for the three things that
deliberately kept their old names, and why. **[unit] [win-ci]**

### The deterministic ATS report parser (previous phase)

Real ATS exports are not free-form documents needing page-by-page
classification — they have an exact, machine-generated structure: an
application report, a labelled separator page ("Resume"), that attachment,
the next separator ("Cover Letters"), and so on. `AtsReportParser`
(`app/services/ats_parser.py`) recognises that structure and extracts it
exactly, ahead of the generic rules/AI pipeline (which still exists,
unmodified, as the fallback for anything that does not match).

**Run directly against the 5 real client PDFs in `qa/input/` (never
committed) [real-client] [measured]. Re-run at the end of the hardening pass;
every number below is unchanged from the previous phase:**

| Measure | Result |
|---------|-------:|
| Files | 5 of 5 |
| Pages | 70 |
| Documents extracted | 16 |
| Classified deterministically (Tier A) | 16 / 16 (100%) |
| Needing review | 0 / 16 |
| Trevor Hollands' resume export range | pages 12–17 (exactly as specified) |
| Trevor Hollands' cover letter export range | pages 19–21 (exactly as specified) |
| Separator pages excluded from every export | yes, all files |
| A 3-section file (References after the cover letter) | handled correctly, unprompted |
| `native_text_pages` / `ocr_pages` / `ocr_failures` | 70 / 0 / 0 |
| Analysis time | 0.32 s (5 ms per page) |

This is the exact bug report that motivated this phase: on the real Hollands-equivalent
file, the generic classifier previously mistyped the section after the
*second* separator ("Cover Letters") as a second "Resume" instead of a "Cover
Letter". The deterministic parser is exact by construction — it does not
score pages, it reads the separator structure — so this class of bug cannot
recur on a file the parser recognises.

On a synthetic ~87-page multi-applicant batch reproducing the same structure
for 11 concatenated applicants **[synthetic]**: every applicant detected,
every section's exact page range correct, zero cross-applicant page leakage,
zero documents needing review. Reproduce with `pytest tests/test_ats_parser.py -v`.

### Candidate packet reconstruction (previous phase, unchanged)

On an 87-page synthetic batch holding 15 different applicants, using the
*generic* (non-ATS) pipeline **[synthetic]**:

| Measure | Result |
|---------|-------:|
| Candidates recovered | 15 of 15 |
| Pages filed under the right person | 100% |
| Documents attributed correctly | 100% |
| False merges (two people combined) | 0 |
| False splits (one person scattered) | 0 |
| Documents left unassigned | 0 |
| Analysis time | ~1.1 s |

Reproduce with `python -m scripts.evaluate_corpus`; see
[Accuracy on synthetic documents](#accuracy-on-synthetic-documents). **These are
generated documents and must not be read as production accuracy** — the real-file
numbers above are the ones that matter for this client's actual workflow.

Everything from the previous two phases is unchanged and still passes. The
deployment path is verified end to end on a clean `windows-latest` runner —
[run 32218291058](https://github.com/kingnazz/AnotherSmartSort/actions/runs/32218291058),
all 27 steps green:

| Artifact | Size | Status |
|----------|-----:|--------|
| `SmartPDFSorter-Setup-<version>.msi` | 93.3 MB **[measured]** | Primary deliverable **[win-ci]** |
| `SmartPDFSorter-Portable-<version>.exe` | 75.8 MB **[measured]** | Secondary **[win-ci]** |
| `SHA256SUMS.txt` | — | Checksums for both **[win-ci]** |
| Installed footprint | 306.6 MB **[measured]** | `C:\Program Files\AS Resume Sorter` |

The green Windows run proves, in order: test suite → OCR staging →
**`tesseract --version` and `--list-langs` against the bundled binary** → real
OCR integration → onedir build → onefile build → pre-package smoke tests → MSI
build → MSI metadata → **silent install** → installed-app smoke test with
bundled OCR → Programs and Features registration → Start Menu shortcut → user
data outside Program Files → **silent uninstall** → removal verified →
**in-place upgrade preserving user data** → artifacts and checksums uploaded.

Recorded by that run **[win-ci] [ocr-real] [measured]**:

```
tesseract v5.4.0.20240606
List of available languages in ".../ocr/tessdata/" (2): eng, osd
[measured] 104 native-text pages: native_text_pages=104 ocr_pages=0 ocr_failures=0
[measured] languages: eng, osd
12 passed, 777 deselected
```

---

## Naming

The product is **AS Resume Sorter**. It shipped as *Smart PDF Sorter* and was
renamed; three things kept their original names on purpose, and each of them
looks like an oversight to anyone reading the code fresh.

| Kept as | Where | Why |
|---------|-------|-----|
| `SmartPDFSorter` | `%LOCALAPPDATA%\SmartPDFSorter\` — settings and history | Renaming a data folder does not migrate what is in it, it abandons it. An upgraded installation would look to the user like the application had forgotten every setting and its whole history. Nobody sees this string. |
| `SMART_PDF_SORTER_HOME`, `SMART_PDF_SORTER_LOG_LEVEL` | Environment overrides | Documented deployment knobs. Renaming them silently stops honouring the variables existing scripts already set. |
| `7B3F2E64-9A21-4C0D-9E2B-5F1A6D8C4E30` | MSI `UpgradeCode` | Windows Installer identifies a product family by this GUID alone. It is what makes the rebranded MSI *upgrade* an installed Smart PDF Sorter instead of installing a second product beside it. `ProductName` and `Manufacturer` are only labels. |

Build artifacts also keep the `SmartPDFSorter-*` filenames
(`SmartPDFSorter-Setup-<version>.msi`, `SmartPDFSorter-Portable-<version>.exe`,
`SmartPDFSorter.exe`). Nobody reads a product name off a filename here — the
Release page, the installer, Programs and Features and the running application
all say AS Resume Sorter — while the two PyInstaller `.spec` files are named
after that constant and invoked by name from CI, and the release workflow's
artifact cleanup matches on the prefix. Renaming them buys nothing and costs
three places that break quietly.

`tests/test_branding.py` and `tests/test_ui.py::TestBranding` pin both halves:
every surface that must show the new name, and every one of the above that must
not change.

---

## Deployment

### MSI installer
- [x] WiX 5 sources in `installer/Package.wxs`, buildable from source **[win-ci]**
- [x] Per-machine install under `C:\Program Files\AS Resume Sorter` **[win-ci]**
- [x] 64-bit Windows 10/11, with launch conditions rejecting anything older **[unit]**
- [x] Registered in Programs and Features with name, version, publisher, icon **[win-ci]**
- [x] Start Menu shortcut created, and removed on uninstall **[win-ci]**
- [x] Desktop shortcut available but off by default (`INSTALLDESKTOPSHORTCUT=1`) **[win-ci]**
- [x] Silent install: `msiexec /i ... /qn /norestart` **[win-ci]**
- [x] Silent uninstall, with binaries verified gone afterwards **[win-ci]**
- [x] Fixed `UpgradeCode`, asserted in CI so it can never drift **[win-ci] [unit]**
- [x] `MajorUpgrade` with `AllowSameVersionUpgrades` — installing over the top
      replaces in place and leaves exactly one entry in Programs and Features **[win-ci]**
- [x] Downgrade blocked with a clear message **[unit]**
- [x] Repair supported (`ARPNOREPAIR=0`) **[untested]** — the WiX component
      structure supports it; no repair run has been executed
- [x] Requires no Python, pip, Git, Visual Studio or WiX on the target PC **[win-ci]**

### User data survives everything
- [x] Settings and history live in `%LOCALAPPDATA%\SmartPDFSorter` **[win-ci]**
- [x] CI asserts no `settings.json` or `history.sqlite3` under Program Files **[win-ci]**
- [x] An in-place upgrade preserves a marker file written into the data dir **[win-ci]**
- [x] Uninstall leaves the user's data directory intact **[win-ci]**
- [x] The installer declares no user-data directories at all **[unit]**

### Two distinct builds
- [x] **Installed**: PyInstaller *onedir* → `dist/SmartPDFSorter/` → into the MSI **[win-ci]**
- [x] **Portable**: PyInstaller *onefile* → single EXE **[win-ci]**
- [x] Both carry real Windows version metadata (product name, version) **[win-ci]**
- [x] Neither opens a console window **[unit]**
- [x] The two are never confused: separate spec files, separate artifact names **[unit]**

### Bundled OCR
- [x] Pinned Tesseract 5.4.0.20240606, verified by SHA-256 before use **[measured]**
- [x] Only the runnable closure is shipped: executable + 26 of 51 DLLs +
      `eng`/`osd` data = ~130 MB of the installer's 239 MB **[measured]**
- [x] Dependency closure computed from real PE imports, transitively **[unit] [measured]**
- [x] Installed to `C:\Program Files\AS Resume Sorter\ocr\` **[win-ci]**
- [x] Found automatically; `TESSDATA_PREFIX` set so it finds its language data **[win-ci] [unit]**
- [x] Bundled engine preferred over any system install, so behaviour is identical
      on every client PC **[unit]**
- [x] A path set in Settings still overrides it **[unit]**
- [x] Scanned English PDF → real text → correct classification, on a clean
      machine, with no user setup **[win-ci] [ocr-real]**
- [x] Licences reproduced in `THIRD_PARTY_NOTICES.md` **[unit]**

### Versioning
- [x] `app/version.py` is the single source of truth **[unit]**
- [x] Drives: Python package, About dialog, EXE metadata, MSI `ProductVersion`,
      both artifact filenames, CI artifact names **[win-ci] [unit]**
- [x] `pyproject.toml` reads it dynamically; a test fails if a literal reappears **[unit]**
- [x] A release tag that disagrees with it fails the release build **[untested]** —
      the check is written but no tag has been pushed

### Build automation
- [x] `.\scripts\build_windows.ps1 -Clean` produces everything **[untested on Windows]**
      — its individual stages are all exercised by CI, but the script itself has
      not run end to end on a Windows machine; CI performs the same steps directly
- [x] Build stops immediately if tests fail **[unit]**
- [x] SHA-256 checksums for every artifact **[win-ci]**
- [x] Optional `-Sign`, hooked at the two correct points: binaries, then MSI **[untested]**
- [x] No certificate, key or password is committed, and none is needed to build **[unit]**

### CI/CD
- [x] Windows workflow builds, installs, verifies, upgrades and uninstalls **[win-ci]**
- [x] Uploads MSI + portable EXE + checksums **[win-ci]**
- [x] Linux job runs the same suite plus real OCR tests **[win-ci]**
- [x] Release workflow on `vX.Y.Z` tags → draft GitHub Release **[untested]**

---

## Candidate packet reconstruction

The third intelligence problem, and its own layer. Classification asks what a
page is, the boundary engine asks where a document ends,
`CandidatePacketService` asks who a finished document belongs to. None reaches
into another's reasoning.

### Attribution
- [x] One mixed PDF is reconstructed into per-applicant packets **[synthetic]**
- [x] Identity from name, email, phone, LinkedIn and applicant ID **[unit]**
- [x] Names normalised across `Benjamin F. Perez` / `Perez, Benjamin` /
      `BENJAMIN PEREZ` **[unit]**
- [x] Phones normalised across `(206) 555-1234` / `+1 206 555 1234` **[unit]**
- [x] Explicit identity beats proximity — a named resume is never absorbed into
      the preceding applicant **[unit] [synthetic]**
- [x] A document naming nobody inherits the active applicant, at a confidence
      that says it was inferred **[unit] [synthetic]**
- [x] Same name with conflicting email or applicant ID is **not** merged, and
      both packets are flagged **[unit]**
- [x] An identity matching two applicants equally goes to review, not to a guess **[unit]**
- [x] Packet boundaries recorded explicitly, with their own confidence **[unit]**
- [x] `association_confidence` per document, separate from type and boundary
      confidence, feeding the existing review workflow **[unit]**

### Unknown queue
- [x] Documents with no confident owner stay unassigned rather than being
      forced under someone **[unit]**
- [x] Sorted last, flagged for review, assignable by the user **[unit]**

### Review UI
- [x] Documents are shown grouped under the applicant that owns them **[unit]**
- [x] Packet header: name, page range, document count, confidence, review badge **[unit]**
- [x] Move a document to another applicant **[unit]**
- [x] Create a new applicant from a document **[unit]**
- [x] Merge two packets **[unit]**
- [x] Split a packet **[unit]**
- [x] Rename an applicant, which flows into folder and file names **[unit]**
- [x] Inspector shows who a document was filed under and the evidence why **[unit]**
- [x] A manual assignment survives a later split or merge re-deriving packets **[unit]**

### Output
- [x] Separate document PDFs (existing behaviour) **[unit]**
- [x] Combined `<Name>_Complete_Packet.pdf` per applicant **[unit] [synthetic]**
- [x] Packet order configurable per profile; missing types skipped; page order
      inside each document preserved **[unit]**
- [x] Combined packets copy original pages — no rasterisation, text intact **[unit]**
- [x] Both output modes independently switchable; turning both off is corrected
      rather than silently exporting nothing **[unit]**
- [x] Excel index carries packet ID, candidate confidence and combined-packet
      filename **[unit]**
- [x] Analysis summary and completion dialog report candidates detected **[unit]**

### What is *not* claimed
- Cross-file packets: an applicant appearing in two different source PDFs
  produces two packets. Merging them is a manual action. **[untested]**
- Accuracy on real ATS exports. See [What is needed next](#what-is-needed-next).

---

## Application (unchanged from V1, still verified)

All V1 functionality is intact; the full suite passes on Linux and Windows.

- [x] Classification, boundary detection and candidate association are three
      separate subsystems **[unit]**
- [x] Multi-page documents stay grouped; a 3-page resume is one PDF **[unit]**
- [x] Splitting copies original pages — no rasterisation, text layer intact **[unit] [ocr-real]**
- [x] Rules Only works fully offline; OpenAI and Ollama are optional **[unit]**
- [x] AI output validated defensively; every failure falls back to rules **[unit]**
- [x] Review workspace, corrections, confidence workflow **[unit]**
- [x] Excel index, history, duplicate detection, settings **[unit]**
- [x] About dialog showing version, provider, OCR status, file locations **[unit]**

---

## Testing

**Full suite: 789 tests — 787 passed, 2 skipped, 0 failed** on Linux. Counted
by running it, not carried forward from a previous phase.

Windows CI reaches the same 789 by a different route, which is worth knowing
when reading its log: the main suite step runs before the OCR runtime is
staged, so the twelve `ocr_real` tests skip there (`777 passed, 12 skipped`),
and a later step runs exactly those twelve against the bundled engine
(`12 passed, 777 deselected`). Nothing is missed, and nothing is counted twice.

The two Linux skips need something the development container cannot provide:
`test_bundled_runtime_is_preferred_when_present` needs a staged OCR runtime and
so only has something to assert in a packaged build, and one packaging test
needs Windows.

```bash
QT_QPA_PLATFORM=offscreen python -m pytest       # ~15 minutes, mostly test_ui
python -m pytest -m ocr_real -v -s               # real OCR only, with counters
pytest tests/test_large_review.py -s             # the measured performance run
```

| Area | Tests | Notes |
|------|------:|-------|
| `test_ui.py` | 89 | Real widgets offscreen: Sort & Save, packet review, corrections |
| `test_packaging.py` | 80 | Version, CLI, bundled OCR discovery, specs, WiX sources, build script, workflow |
| `test_infrastructure.py` | 47 | Discovery, broken PDFs, cancellation, settings migration, history, log redaction |
| `test_export.py` | 46 | Split quality, no rasterisation, collisions, combined packets, Excel |
| `test_metadata.py` | 43 | Fields, third-party contacts, conflicts |
| `test_ai_providers.py` | 41 | Every provider failure mode, stubbed HTTP |
| `test_corrections_undo.py` | 41 | **Undo/redo**, including `PageAnalysis` fields inspected directly |
| `test_pageup_parser.py` | 39 | **PageUp bulk compile**: exact 104-page ranges, cover exclusion, multi-attachment splitting |
| `test_packets.py` | 33 | **Candidate attribution**: specification Tests B–I |
| `test_classification.py` | 31 | Each type, ambiguity, confidence calibration |
| `test_anchor_scan.py` | 31 | Whole-file anchor pass before page classification |
| `test_ocr.py` | 30 | OCR policy, failure handling, native/OCR merge regression |
| `test_filenames.py` | 27 | Sanitisation, reserved names, templates, cross-folder collisions |
| `test_corrections.py` | 26 | Retype, split, merge, exclude, separators |
| `test_ats_parser.py` | 24 | **The separator-page ATS parser**: exact page ranges, 80-page multi-applicant, output layout |
| `test_grouping.py` | 23 | Multi-page integrity, transitions, identity splits |
| `test_type_board.py` | 21 | Drag-and-drop board: drops reach the domain, unsafe drops refused |
| `test_boundary.py` | 20 | Every boundary signal, calibration |
| `test_packet_parser.py` | 19 | **Submitted applicant packets**: 17-file corpus, concatenated batches |
| `test_mixed_batch.py` | 18 | **Specification Test A**: the 87-page mixed batch, end to end |
| `test_external_process.py` | 13 | Hidden child processes, plus the AST audit of every launch site |
| `test_candidate_moves.py` | 12 | Refiling a document under another applicant, by menu and by drag |
| `test_large_review.py` | 12 | **301-page board**: lazy thumbnails, scroll re-requests, measured cost |
| `test_ocr_integration.py` | 12 | **Real Tesseract** on a generated image-only PDF, and a 104-page native file that must never reach it |
| `test_qa_harness.py` | 11 | The evaluator's own scoring, against known answers |

### The ATS parser tests **[synthetic + real-structure]**

`tests/test_ats_parser.py` is the regression suite for this phase. It asserts
*exact* export page ranges for four fixtures whose section lengths reproduce
the real client files (Marcus Delgado, Nathan Whitfield, Trevor Hollands, Sofia
Brennan) -- Trevor's resume must be source pages 12–17 and his cover letter
19–21, and nothing else. It also builds an ~87-page PDF of eleven ATS packets
concatenated and asserts that no page of one applicant reaches another's
files, that every separator page is excluded, and that nothing is flagged for
review. Two further tests pin the fallback: a plain resume and the generic
separator sample must produce byte-identical grouping with the parser wired
in and with it absent, so the fast path cannot silently capture files it was
never meant to handle.

### The mixed-batch test **[synthetic]**

`tests/test_mixed_batch.py` builds an 87-page PDF holding 15 applicants —
resumes of one to three pages, packets missing their application report,
references, transcripts, separator pages — and asserts three things separately:
the right documents were formed, the right applicants were found, and **no page
was filed under the wrong person**. That last one is the assertion that matters;
a misfiled document is one nobody goes looking for.

Pages left in the unknown queue are deliberately not counted as misattributed.
Unresolved is a safe failure; wrong is not. A separate assertion caps how much
may be left unresolved, so parking everything in review cannot pass.

### The real OCR integration test **[ocr-real]**

`tests/test_ocr_integration.py` generates an image-only PDF (text rendered, then
rasterised, then embedded with no text layer) and proves the whole path:

1. native extraction really does return nothing
2. a real Tesseract binary recovers the text
3. the recovered text classifies correctly as a Resume
4. the full pipeline reports `TextSource.OCR` and groups it
5. the applicant's email is recovered from the scan
6. **the exported PDF still contains the original scan** — verified by comparing
   the embedded image stream byte-for-byte, and confirming the output has no
   text layer

It also proves the other half of the contract, which costs money when it
breaks: the 104-page native-text fixture runs through the same real engine with
OCR *enabled* and must report `native_text_pages=104, ocr_pages=0,
ocr_failures=0`. OCR-ing readable pages would turn a half-second analysis into
a several-minute one and produce worse text than the page already had. The
counters are printed as well as asserted, and Windows CI runs this step with
`-s` so they appear in the log — otherwise nothing records what the packaged
runtime actually did.

Verified against Tesseract 5.3.4 on Linux and the bundled 5.4.0 on Windows CI.
CI additionally runs `--list-langs` against the staged binary and fails if
English is missing.

**One caveat on these tests.** The accuracy assertions in steps 2, 3 and 5 read
real OCR output, so they are only as stable as OCR is. Two of them failed once
on this container while a second full test run was competing for CPU, and
passed 5 runs out of 5 when run alone. That is worth knowing before wiring
these into a release gate: they are correctness tests, but they are not
hermetic.

---

## Measured performance **[measured]**

500 synthetic PDFs, 2,216 pages, Rules Only, OCR off, on this development
container:

| Metric | Result |
|--------|--------|
| Analysis time | 42.2 s |
| Throughput | 712 PDFs/min · 3,154 pages/min |
| Per PDF | 84 ms |
| Documents detected | 929 |
| Needing review | 142 (15.3%) |
| Failures | 0 |
| Export | 929 documents + 929 combined packets in 6.6 s |
| Peak Python allocation | 8.3 MB |
| Process RSS growth | 17.3 MB total (~35 KB per PDF) |

Memory is flat — it does not scale with batch size, so a 2,000-PDF folder is a
throughput question (~3 minutes), not a memory one. No leak was found.

**Absolute throughput varies with the container this runs in**; an earlier
session measured 966 PDFs/min on the same code path. What does *not* vary is the
cost of the new attribution pass, measured directly by running the same 200
files with and without it: **−1.6%, i.e. nothing outside noise.** Association is
a single pass over finished documents, so it scales with document count, not
page count.

Reproduce: `python -m scripts.benchmark_batch --pdfs 500 --export`

---

## Accuracy on synthetic documents **[synthetic]**

`python -m scripts.evaluate_corpus --input <dir> --ground-truth qa/expected.example.json`

The 87-page mixed batch (15 applicants, 41 labelled documents), which is the
shape of the real client input:

| Metric | Result |
|--------|-------:|
| **Candidate packet accuracy** | **100%** (15 of 15) |
| Document association accuracy | 100% |
| Page type accuracy | 100% |
| Boundary accuracy | 100% |
| Whole-document accuracy | 100% |
| False candidate merges | 0 |
| False candidate splits | 0 |
| Documents unassigned | 0 |
| Needing review | 4.7% (2 of 43) |
| False splits | 0 |
| Missed splits | 0 |

Across the smaller single-applicant samples, page type accuracy is 96.8% and
review rate 15.4%.

**These numbers describe documents the project generated for itself and must not
be quoted as production accuracy.** The generator knows what the classifier
looks for, which is precisely the bias real documents will not share. They are a
regression baseline, nothing more.

### A real Windows behaviour worth knowing

`SmartPDFSorter.exe` is a windowed (GUI subsystem) executable, which is correct
for a desktop application. A consequence: **`cmd` and PowerShell do not wait for
it**. Writing `& $exe --smoke-test` and then testing `$LASTEXITCODE` reads a
stale value — it can report success while the application is still starting.

This produced a CI failure that looked impossible (the check failed while the
smoke test it was checking printed `RESULT: PASS` a second later), and it had
been making some earlier checks pass spuriously.

`scripts/Invoke-AppCli.ps1` runs the executable, waits, and returns its real exit
code; every script now goes through it, a guard test fails if any reverts, and
the README warns deployment scripters who will hit exactly this.

The same fact has a second consequence, found while writing the instructions for
testing a real install: a windowed executable has **no console at all**, so
`SmartPDFSorter.exe --version` typed into PowerShell printed into the void. The
command was documented as printing the version and did nothing visible. The CLI
path now calls `AttachConsole(ATTACH_PARENT_PROCESS)` to borrow the caller's
console before producing any output, leaving already-redirected streams alone so
scripted callers still get output where they asked for it. A GUI launch never
reaches for a console. CI asserts the version is visible through plain `cmd`,
not only through a redirected file handle. **[win-ci]**

### A real bug this harness found

Running the evaluator with OCR enabled exposed a defect no unit test had caught.
OCR renders the whole page, so its output already contains any native text; the
pipeline was *concatenating* native + OCR, duplicating every word on pages with a
small text layer (`"RESUME"` became `"RESUME RESUME"`). That stopped separator
pages being recognised and doubled keyword counts.

Fixed by detecting coverage and preferring the OCR result. Whole-document
accuracy went from 84.6% to 100%, and false splits from 4 to 0. Regression tests
added in `tests/test_ocr.py::TestOCRTextMerging`.

---

## Known limitations

### Licensing — needs a business decision before client distribution

**PyMuPDF/MuPDF is licensed under AGPL v3.** Distributing AS Resume Sorter to
third parties under the AGPL obliges you to offer those recipients the complete
corresponding source of the whole application on the same terms. For proprietary
distribution you must either:

1. buy a commercial MuPDF licence from Artifex, or
2. replace PyMuPDF with a permissively licensed PDF engine (pypdf plus a separate
   renderer is the usual route), or
3. accept and comply with the AGPL.

Qt/PySide6 is LGPL v3, which is satisfied by the current dynamic linking, and
Tesseract is Apache-2.0, which is straightforwardly redistributable. MuPDF is the
one that needs a decision. This is recorded in `THIRD_PARTY_NOTICES.md`; nothing
was shipped quietly.

### Not yet verified
0. **The PageUp and packet parsers have never seen a real file. [untested]**
   This is the largest open risk in the product and belongs at the top of this
   list. Neither the 104-page bulk compile nor the 17-file per-applicant
   corpus was present in any development environment; both
   parsers were written from the phase specification's prose description of
   those files and are proven only against synthetic reproductions of it.
   Everything the specification stated — the cover page's exact wording, the
   `Total score` marker, the `Surname, Given (Submitted on: …)` header, the
   ground-truth page ranges — is reproduced faithfully in the fixtures and
   matched exactly. What cannot be known without the files is whether the
   specification described them completely. A marker phrased differently in
   the real export would make the parser decline the file (it falls through to
   the generic pipeline, which is the safe failure) or, less likely, split a
   packet at the wrong place. Both are localised fixes rather than redesigns,
   because detection markers are module-level constants.
1. **Real client PDFs verified only for the one ATS vendor format seen so
   far.** The 5 real files in `qa/input/` all match the same deterministic
   structure (report → "Resume" separator → resume → "Cover Letters"
   separator → cover letter, occasionally a third section) and are extracted
   with 100% deterministic classification and zero review flags. A different
   ATS vendor (Workday, Taleo, iCIMS, Greenhouse) may format its export
   differently; a file that does not match `is_ats_generated_page`'s markers
   falls back to the generic rules/AI pipeline, which remains tuned against
   invented documents only, exactly as before this phase.
2. **`build_windows.ps1` has not been run end to end on Windows.** Every stage it
   performs is exercised by the Windows workflow directly, and the release
   workflow invokes the script itself — but that workflow has not fired yet, so
   the script as a whole remains unproven.
3. **Code signing is unproven.** The hooks exist and take no certificate; no
   signed build has been produced.
4. **The release workflow has not fired.** No `vX.Y.Z` tag has been pushed.
5. **MSI repair has not been exercised**, only enabled.
6. **Only English OCR is bundled.** Other languages need `eng` swapped or extended
   in `scripts/fetch_ocr_runtime.py`.
6a. **No console window has been observed *not* appearing. [untested]** Every
   launch site goes through `run_hidden`, an AST audit of every module in
   `app/` fails if a new one appears, and both suppression flags are asserted.
   None of that is the same as a human watching the installed MSI process a
   scanned PDF and seeing no flash, and CI cannot supply that observation.
6b. **Scroll-driven thumbnail loading is proven offscreen, not on a real
   display.** `tests/test_large_review.py` drives the scrollbars directly and
   asserts the requests that result. Qt's offscreen platform reports geometry
   faithfully, which is what the visibility scan reads, but momentum scrolling
   on a real trackpad has not been tried.

### Accepted trade-offs
7. **The MSI is 93 MB** — PySide6, PyMuPDF and a bundled OCR engine. Unused Qt
   modules are already excluded. This is a normal size for OCR-capable desktop
   software but worth knowing before a mass rollout over a slow link. The
   installed footprint is larger than the MSI because the payload is compressed;
   CI now prints the measured on-disk size after every install.
8. **The portable EXE has no bundled OCR**, deliberately: a onefile build unpacks
   its whole payload on every launch, and ~130 MB of that would make startup
   painful for a capability portable users rarely need. It still uses a system
   Tesseract, or an `ocr` folder placed beside it.
9. **Artifacts are unsigned**, so SmartScreen warns on first run.
10. **Only the Recruiting profile exists** — deliberate; the architecture supports
    more, and adding them is out of scope for this phase.

---

## What is needed next

To move from "deployable" to "known-good across everything the client
actually receives":

1. **Run the two real corpora these parsers were built for. This is the one
   item that changes what can honestly be claimed about the product.** The
   104-page PageUp bulk compile and the 17-file applicant-packet corpus were
   specified in detail but have never been present in any development
   environment, so both parsers are proven only against synthetic
   reproductions of their structures. Put the real files in `qa/input/`
   (git-ignored — they contain confidential applicant information and must
   never be committed) and run:

   ```powershell
   python -m scripts.evaluate_corpus --input qa\input --ground-truth qa\expected.json
   ```

   Expected, from the specification: 14 applicants / 14 application reports /
   14 resumes / 0 leakage for the PageUp file; 17 application reports / 16
   resumes / 10 cover letters / 1 transcript / 44 documents for the corpus.

   What to look at, in order. First, **did the right parser claim the file?**
   `analysis.parser_name` should read "PageUp bulk compile" or "Submitted
   applicant packet"; if it is empty the file fell through to the generic
   pipeline, which means a detection marker is worded differently in reality
   — a one-line fix in the constants at the top of the parser module. Second,
   **are the page ranges exact?** A boundary off by one page points at the
   `Total score` / form-section markers. Third, **is anything flagged?** The
   parsers are built to flag rather than guess, so review flags are the
   designed outcome for a structure they cannot read, not a failure.

   The 104-page file declares a single attachment type, so its attachment
   region is never split — that path is exercised only if the real cover page
   turns out to declare more than one.

2. **Watch the installed MSI for console flashes.** The hidden-process helper
   is unit-tested and the audit prevents new launch sites, but "no window
   appeared on screen" can only be confirmed by a human running the installed
   build against a scanned PDF.

3. **Broaden the real corpus.** 5 real files are in `qa/input/` (git-ignored)
   and all extract deterministically with zero review flags. Add more —
   especially any file from a *different* ATS vendor, or one holding more
   than one applicant in a single PDF, since the deterministic parser's
   multi-applicant path (point 8/15 of the specification) is currently proven
   only against synthetic concatenations, never a real multi-applicant
   export. If the client's actual workflow is one large mixed PDF rather than
   one PDF per applicant, that file matters more than anything else on this
   list.
2. **Label them.** `python -m scripts.evaluate_corpus --make-template qa/input > qa/expected.json`,
   then correct the predictions by hand — especially the candidate names and
   which documents belong to whom. An unreviewed template measures the
   classifier against itself and is worthless.
3. **Measure.** `python -m scripts.evaluate_corpus --input qa/input --ground-truth qa/expected.json`
   Read **candidate packet accuracy** first, then false merges. A false merge is
   the failure that loses documents.
4. **Tune** against the files the report names: signal weights in
   `app/profiles/recruiting.py` for type errors, `app/services/boundary_engine.py`
   for split errors, and `app/services/identity.py` /
   `app/services/packet_service.py` for attribution errors. Re-measure after each
   change. Which file to touch is decided by which of the three metrics moved.
5. **Settle the MuPDF licence** before the software goes to the client.
6. **Decide on code signing.** An unsigned installer triggers SmartScreen on every
   machine, which is a poor first impression for paid software.

Only after step 3 can any production accuracy figure be quoted honestly.

---

## Architecture notes for future sessions

**0. A known file format is handled deterministically, ahead of the three
subsystems below.** `app/services/ats_parser.py` is Tier A of the
classification priority order (A: known ATS parser, B: generic rules
classifier, C: AI escalation, D: Needs Review). `ProcessingPipeline` extracts
every page's text and features first (`_analyze_pages`, two passes), then
checks `AtsReportParser.looks_like_ats_export()` once for the whole file — a
whole-file decision, since a separator on page 40 has to be seen before page
1 can be trusted. When it matches, `AtsReportParser.parse()` fills in every
page's type, boundary and identity directly (~0.99 confidence,
`ClassificationSource.DETERMINISTIC`) and the rules classifier / AI never run
on that file at all. When it does not match, nothing changed: the same
per-page loop that existed before this phase runs exactly as it did. The two
paths converge back into the same `GroupingService.build_groups` /
`CandidatePacketService.build_packets` / `ExportService` — Tier A only
decides *how a page's answers are produced*, never bypasses the rest of the
pipeline.

Three further rules hold this codebase together — please preserve them:

**1. The three questions stay in three subsystems.**
`app/intelligence/rules_provider.py` answers *what kind of document is this
page?*. `app/services/boundary_engine.py` separately answers *does this page
start a new document?*. `app/services/packet_service.py` separately again
answers *which applicant does this document belong to?*.

Merging the first two reintroduces the exact failure the product exists to
prevent: a resume splitting into per-page files because one page scored weakly.
Merging attribution into either reintroduces the other one: documents filed by
what they look like rather than by whose name is on them.

**2. Attribution runs after grouping, over finished documents.**
Whether page 6 belongs to Jane depends on what page 7 turns out to be, so
`CandidatePacketService.build_packets` needs the whole file. It must not be
folded into the per-page loop.

**3. Domain logic never lives in the UI.**
`app/services/` imports no Qt. Every correction the review workspace offers is a
call into `GroupingService` or `CandidatePacketService`, which re-derive type,
confidence, identity, attribution, separator state and review flags.

**Packet additions from an earlier phase:**
- `app/services/identity.py` — normalisation and comparison only. Conflicts are
  treated as stronger evidence than matches, deliberately: two people merged is
  worse than two packets left apart.
- `app/services/packet_service.py` — attribution and every packet correction.
- `app/models/packet.py` — `CandidatePacket`, including the unknown queue.
- `scripts/mixed_batch.py` — the synthetic multi-applicant generator, seeded so
  a failure reproduces exactly.

**ATS parser additions from this phase:**
- `app/services/ats_parser.py` — `AtsReportParser`, Tier A. Whole-file
  detection plus the deterministic page-walk described in note 0 above.
- `app/profiles/recruiting.py` — `is_ats_generated_page`, a public wrapper
  around the existing `_ats_generated_header` signal predicate, reused as the
  whole-file detector so detection and classification never diverge.
- `app/services/metadata_service.py` — `name_from_report_subject`, promoted
  from a private method to a module-level function so the parser and
  `MetadataExtractor` share one implementation.
- `scripts/ats_fixtures.py` — synthetic ATS-structure fixtures: the four
  named, real-file-derived single-candidate batches (exact page counts) and
  `build_multi_applicant_batch()` for the ~80-page concatenated test.
- `app/services/export_service.py` — `group_by_document_type` mode
  (Resumes/, Cover Letters/, …, Needs Review/), on by default. Bug fixed
  alongside it: `unique_path`'s collision-avoidance was keyed on bare
  filename, so the same candidate name recurring across different type
  folders (never possible under the old candidate-first layout, where every
  filename carried a type suffix) was wrongly treated as a collision. Now
  keyed on full path.
- `app/services/packet_service.py` — bug fixed: `_start_packet` flagged
  *every* pair of applicants with different emails as "sharing a name" with
  a review note, regardless of whether their names had anything in common,
  because the email-clash conflict reason never mentions names at all. In an
  11-applicant batch this put a false review flag on every single packet.
  Now checked by actual name similarity.
- `tests/test_ats_parser.py` — exact page-range regression tests per named
  candidate, the multi-applicant leakage test, export-layout tests.

**Rules that hold the structured parsers together — please preserve them:**

- **A repeated heading is not a boundary; a change of identity is.** Both the
  PageUp and packet formats print their heading as a running header on every
  continuation page. Keying a packet boundary on "the heading appeared" makes
  every page a new applicant. Both parsers therefore open a packet only when
  the *identity* changes (`PageUpBulkCompileParser._walk`,
  `SubmittedApplicantPacketParser._split_packets`). This mistake was made
  twice, once per parser, which is why it is written down.
- **A document's first page is never "Page 2 of N".** Uploaded resumes and
  transcripts repeat their title in a running header, so without this guard
  their second page re-opens as a fresh document
  (`AttachmentDetector.is_continuation_page`).
- **What the file states outranks what a page looks like.** Attachment
  boundaries are read from filenames, printed titles and page numbering before
  any text scoring, and where the file states nothing the pages stay together
  and go to review. Merging two documents and splitting one are both wrong;
  the difference is that a flagged document gets looked at and a silently
  mislabelled one does not.
- **A parser's doubt has to reach the reviewer.** `assign_page` marks pages
  `DETERMINISTIC`, which by design stops the AI escalation and the confidence
  heuristics from second-guessing them. That also means nothing else will
  flag them, so `GroupingService.evaluate_review` copies a deterministic
  page's own review reasons onto its document. Remove that and every parser
  warning becomes invisible.
- **The bulk cover is not a separator page.** A separator is a divider inside
  one applicant's packet, which a user may reasonably want kept. The PageUp
  cover is the file's manifest, naming every applicant in the batch, so it is
  excluded unconditionally rather than by the separator policy.

**Parser modules:**
- `app/services/parsers/base.py` — the `can_parse` / `parse` contract,
  `assign_page`, and `MIN_MATCH_CONFIDENCE`, below which the registry hands
  nothing over and the generic pipeline runs.
- `app/services/parsers/registry.py` — asks every parser, runs only the
  strongest, and rolls back partial work if one raises.
- `app/services/parsers/attachments.py` — the shared attachment-opening
  detector. Both formats end the same way (generated form, then uploads,
  nothing between), so this exists to stop two copies of the same rules
  drifting apart.
- `app/services/parsers/pageup.py`, `submitted_packet.py`, `uc_separator.py` —
  one module per recognised format; detection markers are module-level
  constants so a real file phrasing one differently is a one-line fix.
- `scripts/pageup_fixtures.py`, `scripts/packet_fixtures.py` — synthetic
  reproductions with invented applicants. No real applicant data is ever
  committed to this repository.

**Packaging additions from this phase:**
- `app/version.py` — the only version. Everything else reads it.
- `app/cli.py` — `--version`, `--smoke-test`, `--ocr-info`. Kept out of the UI so
  installer verification needs no test-only branches in application code.
- `scripts/fetch_ocr_runtime.py` — pinned, hash-verified OCR staging.
- `installer/Package.wxs` — the `UpgradeCode` is permanent. Changing it orphans
  every already-installed client.
