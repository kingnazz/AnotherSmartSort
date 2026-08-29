<img src="assets/logo.png" alt="AS Resume Sorter" width="320">

# AS Resume Sorter

AS Resume Sorter takes PDFs that contain several documents stuck together,
works out where each document starts and ends, groups the pages, names the
files sensibly, and saves them out — so you only have to check the handful it
was unsure about.

It was built for recruiting packets first: application reports, resumes, cover
letters, references, transcripts, writing samples and portfolios.

The everyday job is applicant tracking system (ATS) exports: one PDF per
applicant (or one large export holding many), each an application report
followed by its attachments behind separator pages that just say "Resume" or
"Cover Letters". AS Resume Sorter recognises that structure and extracts it
exactly — see [How ATS exports are handled](#how-ats-exports-are-handled).

---

# End User Instructions

## What the program does

The usual input is one big PDF — 50 to 100 pages — holding the documents for
fifteen or twenty different applicants, run together with nothing marking where
one person's papers end and the next person's begin.

Drop that file in and you get back a folder per applicant.

For every page the program works out three separate things:

1. **What kind of document is this page part of?** — Resume, Cover Letter, and so on.
2. **Does this page start a new document, or continue the previous one?**
3. **Which applicant does this document belong to?**

Those are different questions, and keeping them apart is what makes the whole
thing work. The second is what lets a 3-page resume come out as **one** 3-page
resume rather than three files. The third is what puts that resume in the right
person's folder.

It gives each answer its own confidence score, and only asks you to look at the
ones it is unsure about.

### How applicants are identified

Names, emails, phone numbers, LinkedIn URLs and applicant IDs are all used, and
all normalised — `Benjamin Perez`, `Benjamin F. Perez` and `PEREZ, BENJAMIN` are
recognised as one person, as are `(206) 555-1234` and `+1 206 555 1234`.

Two rules matter:

- **A name on the page beats its position in the file.** A resume that says
  Sarah Lee belongs to Sarah Lee even when it sits directly under Jane's cover
  letter with no application report in between.
- **A document that names nobody joins the applicant already in progress** —
  usually right, so it is done — but at a confidence that says it was inferred,
  and it goes in the review queue rather than being presented as certain.

Two applicants with the same name but different emails or applicant IDs are
kept apart and flagged, never silently merged. Merging them would bury one
person's documents inside another person's folder, where nobody would look.

## How ATS exports are handled

Recruiting systems export in several different shapes, and AS Resume Sorter
recognises each one outright rather than scoring it page by page. Three are
built in:

| Format | What it looks like |
|--------|-------------------|
| **Separator-page export** | An application report, then a page reading just "Resume", then the resume, then "Cover Letters", and so on |
| **PageUp bulk compile** | One file holding many applicants, opening with a cover page that lists everyone included and which attachments were compiled |
| **Submitted applicant packet** | One applicant's generated application form followed by whatever they uploaded, with nothing marking the join |

Each is handled by its own parser, and the application picks whichever one
recognises your file most strongly. A file none of them recognises falls
through to the general-purpose path described under [Large and unfamiliar
PDFs](#large-and-unfamiliar-pdfs) — nothing is forced.

### Separator-page exports

An application report, then a page that just says "Resume", then the resume,
then a page that just says "Cover Letters", then the cover letter, sometimes
repeating for more than one applicant in a single file:

- The application report's own header ("Confidential Report", "Application
  Details for Jane Smith", "Job Opening ID: …") is what identifies the file
  and names the applicant.
- Once a separator page opens a section, every page after it belongs to that
  section until the next separator, a new applicant's report, or the end of
  the file — a six-page resume stays one six-page resume.
- The separator page itself is not part of any document; it is excluded from
  what gets saved, by default.
- The applicant's name, from the report, carries over to their resume and
  cover letter automatically — those pages never have to repeat it.

### PageUp bulk compiles

One PDF holding many applicants. It opens with a cover page stating which
attachment types were compiled, listing every applicant included, and giving
the count:

- **The cover page is never exported.** It describes the file, not any one
  applicant, so it does not end up inside somebody's resume.
- Each applicant's application form ends at "Total score", and everything up
  to the next applicant is their attachment.
- Names keep however they were written — `Dr Tandalea Mercer (Tandalea)`
  stays that way on the output file, while still being matched to the plainer
  `Tandalea Mercer` the form prints.
- If the cover promises fourteen applicants and thirteen are found, you are
  told. A silent partial result is how documents go missing.

When the cover declares a single attachment type, extraction is exact. When
it declares several, each attachment is identified as a whole document, and
anything genuinely unclear is flagged rather than guessed.

### Submitted applicant packets

One applicant's generated application form followed by whatever they
uploaded — a cover letter, a resume, a transcript — with **no separator
pages** between them. Two things make this harder than it looks, and both are
handled:

- The form's later pages list employment and education history, which reads
  exactly like a resume to a keyword classifier. The form's own vocabulary
  keeps it from being mistaken for one.
- A cover letter's second page is often just a signature, and application
  forms sometimes contain a blank sheet. Neither starts a new document: a
  page has to look like the *opening* of something to begin it, and
  everything else continues what is already open.

The same parser handles one applicant per file and many packets concatenated
into one PDF.

## Large and unfamiliar PDFs

A file that matches none of the known formats is not left to guesswork
either. Before classifying anything, the whole file is scanned cheaply for
the places where a document's start is *stated* rather than inferred — a
title, page numbering restarting at one, a letter's salutation, an
application form ending, a different applicant's contact details.

Those anchors propose where documents begin, and they outrank a page's own
appearance. This is the right way round for long files: on page four of a
six-page resume there is very little to see, and deciding page by page is how
a document ends up split down the middle.

If you have AI enabled, it is used as a last resort on the few *logical
documents* still uncertain — one question per document, not one per page — so
a hundred-page file costs a handful of requests rather than a hundred.
Anything a known format already settled is never sent for a second opinion.

## Installing

Double-click **SmartPDFSorter-Setup-1.0.1.msi** and follow the prompts. Smart PDF
Sorter then appears in the Start Menu and in Windows Settings → Apps, like any
other program.

Nothing else needs installing — no Python, no separate OCR download. Scanned
PDFs work immediately.

Windows may show a "Windows protected your PC" warning the first time because
the installer isn't code-signed; choose **More info → Run anyway**.

### Installing on many computers at once

The MSI installs silently, so it works with Intune, an RMM, Group Policy or a
PowerShell script:

```powershell
msiexec /i SmartPDFSorter-Setup-1.0.1.msi /qn /norestart
```

Add a desktop shortcut for everyone (off by default so deployments don't clutter
desktops):

```powershell
msiexec /i SmartPDFSorter-Setup-1.0.1.msi /qn /norestart INSTALLDESKTOPSHORTCUT=1
```

Install somewhere else:

```powershell
msiexec /i SmartPDFSorter-Setup-1.0.1.msi /qn /norestart INSTALLFOLDER="D:\Apps\AS Resume Sorter"
```

Uninstall silently:

```powershell
msiexec /x SmartPDFSorter-Setup-1.0.1.msi /qn /norestart
```

`msiexec` returns 0 on success, 3010 when a restart is pending, and 1603 on
failure. Add `/l*v install.log` to capture a full log.

### Scripting the command line

AS Resume Sorter can report on itself, which is useful for verifying a
deployment:

```
SmartPDFSorter.exe --version      # print the version
SmartPDFSorter.exe --ocr-info     # report which OCR engine it will use
SmartPDFSorter.exe --smoke-test   # check the installation works; exit code 0 = healthy
```

Typed into a terminal these print their output there, because the application
borrows the console of whoever launched it. Your prompt comes back before the
text appears — that is normal for a windowed program and is only cosmetic.

**Important for scripts.** It is a windowed application, so Windows does not
make `cmd` or PowerShell wait for it. This looks right but is not:

```powershell
& $exe --smoke-test
if ($LASTEXITCODE -ne 0) { ... }   # WRONG: reads the exit code before it exists
```

Use the helper shipped in the repository, which waits and returns the real exit
code:

```powershell
.\scripts\Invoke-AppCli.ps1 -Exe $exe -AppArgs '--smoke-test'
if ($LASTEXITCODE -ne 0) { throw "installation is not healthy" }
```

Or wait yourself:

```powershell
$p = Start-Process $exe -ArgumentList '--smoke-test' -Wait -PassThru -NoNewWindow
if ($p.ExitCode -ne 0) { throw "installation is not healthy" }
```

### Upgrading

Just install the newer MSI. It replaces the old version in place — no need to
uninstall first, and **your settings, history and output folder are untouched**.

### Portable version

If you can't install software, use **SmartPDFSorter-Portable-1.0.1.exe**. It runs
from anywhere, including a USB stick. One difference: the portable build does not
carry the OCR engine, so scanned PDFs are only readable if the computer already
has Tesseract, or if you put the installed version's `ocr` folder next to the EXE.

## Adding PDFs

Any of these work:

- Drag PDFs onto the big drop area
- Drag a whole folder onto it
- **Add PDFs** to browse for files
- **Add Folder** to pick a folder

If a folder has subfolders, they're included too (you can turn that off in
Settings). Your output folder is never re-scanned as input, so re-running a job
won't pick up its own results.

Files you've processed before are marked with a ⟳. It's only a note — you can
process them again. The check uses the file's actual content, so a renamed copy
is still recognised, and two different files that happen to share a name are not.

## Choosing what you need

Above the queue: **What do you want?**

Leave it on **Everything** to save every document found. Or click **Resumes**
and you get a folder of resumes and nothing else — which is the usual job.
Click more than one to combine them. The three everyday choices — Resumes,
Cover Letters, Application Reports — are always visible; **More types** opens
References, Transcripts, Writing Samples and Portfolios, which are fully
supported but less common, so they stay one click away rather than crowding
the everyday choice.

The whole app then follows that choice — the review screen shows only those
documents, the review count counts only those, and only those get saved. Pick
Resumes and nothing else is ever put in front of you.

Everything is still analysed underneath, so changing your mind costs another
export, not another analysis. The choice is remembered for next time.

When you narrow the output to a single type, no combined packet is written: it
would be a second copy of the one document beside it.

## The normal path: one click

For most batches that's the whole job:

```
Drop PDFs → (optionally) pick a document type → Sort & Save
```

**Sort & Save** analyzes, splits, classifies, names and saves everything in
one step. There is no separate Analyze-then-Save click for normal use, and
nothing forces you through a review screen first — if a handful of documents
still need a decision when it finishes, they land in their own **Needs
Review** folder instead of blocking the rest.

The queue shows each file's progress while it runs: Waiting, Reading, OCR,
Analyzing, then either **Ready** or **Review Needed**. You can keep using the
window while it works, and **Cancel** stops cleanly — files already finished
are kept. A file that can't be read (damaged, password-protected) is marked
with an error and the rest of the batch carries on.

When it's done, a summary appears at a glance:

```
48 PDFs processed
48 resumes saved
42 cover letters saved
48 application reports saved
2 items need review
[Open Output Folder]
```

**Analyze only** and **Split & Save** (as two separate steps), plus **Review
documents**, remain available under *Advanced* for anyone who wants to check
things before saving — they are optional, not required.

## Reviewing (optional)

Click **Review documents** when you want to look at what needs a decision, or
just want to see the applicants that were found before saving. It's a detour
for the documents that need attention, not a required stop.

### Fixing a sort by dragging it

Review opens on a board of lanes — Resumes, Cover Letters, Application
Reports, References, Other, Needs Review — with every document as a card:

```
RESUMES (14)              COVER LETTERS (10)        NEEDS REVIEW (2)
┌──────────────────┐      ┌──────────────────┐      ┌──────────────────┐
│ Peter Aguila     │      │ Michael Brown    │      │ Alexis Holly     │
│ Resume · 7–8     │      │ Cover Letter · 8 │      │ Other · 41       │
│ 2 pages · 99%    │      │ 1 page · 96%     │      │ 1 page · 48%     │
└──────────────────┘      └──────────────────┘      └──────────────────┘
```

- **Drag a card to another lane** to change what it is. Dragging from Needs
  Review to Resumes says "this is a resume" and clears the flag.
- **Drag a page thumbnail onto another card** to move it, when a document
  starts or ends a page too early. Ctrl-click and Shift-click select several
  pages to move together.
- **Right-click** anything for the same actions as a menu, if you would
  rather not drag.

A drop that would corrupt the result — pages from a different PDF, a
selection that would leave a gap in the middle of a document — is refused,
and tells you why.

**Undo (Ctrl+Z) and Redo (Ctrl+Y)** cover every correction, including drags.
Undo restores exactly what was there; it never re-runs the analysis, so
nothing else you have fixed is lost.

**View: [By Type] [By Candidate]** switches to the older layout, which groups
each applicant's documents together instead.

The candidate view below is that second layout:

- **Left** — your source PDFs, with a warning marker on any that need attention.
- **Middle** — the applicants found in the selected PDF. Each block is one
  person: their name, page range, document count and confidence, with their
  documents underneath and thumbnails of exactly the pages that will be saved.
- **Right** — details of the selected document, including which applicant it was
  filed under and why, and every correction you can make.

For an 80-page batch this is the difference between reading a flat list of forty
documents and reading fifteen named people:

```
Jane Smith                     Pages 1–6 · 3 documents        98%
    Application Report         Pages 1–3
    Resume                     Pages 4–5
    Cover Letter               Page 6

Robert Jones                   Pages 7–11 · 2 documents       97%
    Application Report         Pages 7–9
    Resume                     Pages 10–11
```

**This is the part that saves you time:** click **Review Needed** and the
workspace narrows to only the documents that need a decision. If 287 of 300 came
out clean, you look at 13.

**Approve all** accepts every flagged document at once, without opening them
individually. It only approves what you can currently see, so approving while
filtered to resumes never signs off cover letters you have not looked at.

### What the confidence colours mean

| Colour | Meaning | What to do |
|--------|---------|------------|
| Green | 90–100% | Nothing. It's confident and almost certainly right. |
| Amber | 70–89% | Worth a glance. |
| Red | Below 70% | Needs a decision. |

You can change these numbers in Settings.

### Fixing things

Select a document, then use the right-hand panel:

- **Document type** — pick the correct type from the list.
- **Split before page N** — click a page first, then split. (Double-clicking a page does the same.)
- **Merge with previous / next** — join two documents that should be one.
- **Mark as Other** — for anything that doesn't fit.
- **Exclude from export** — skip this document entirely.
- **Include/remove separator page** — for divider pages that just say "RESUME".
- **Looks correct** — clears the warning when it was right all along.

Corrections take effect immediately, and the page thumbnails regroup as you go.

### Fixing who a document belongs to

The right-hand panel shows which applicant the selected document was filed
under, how confident that was, and the evidence behind it. To change it:

- **Belongs to** — pick a different applicant from the list.
- **New candidate from this document** — pull a document out into its own person.

On each applicant's header:

- **Rename** — correct an extracted name; the folder and filenames follow.
- **Merge…** — join two packets that turned out to be the same person, which
  happens when a name was written two different ways.
- **Looks right** — accept the grouping and clear its warning.

### Unknown / Needs Assignment

Documents the program could not confidently attribute are **not** forced under
someone. They collect in an **Unknown / Needs Assignment** section at the bottom
of the list, where you assign them yourself. This is deliberate: a document
sitting visibly unassigned is a minute of work, while one filed under the wrong
person is lost.

## Saving

Click **Sort & Save** (or, from the review screen, **Split & Save**). Each run
goes into its own folder, named for the moment it started, and inside that you
get one folder per document type:

```
AS Resume Sorter/
    2026-08-26_10-32-AM/
        Application Reports/
            Marcus Delgado.pdf
            Trevor Hollands.pdf
        Resumes/
            Marcus Delgado.pdf
            Trevor Hollands.pdf
        Cover Letters/
            Marcus Delgado.pdf
            Trevor Hollands.pdf
        Needs Review/
            Unknown_001.pdf
```

You pick the output folder once; every run afterwards makes a new dated folder
inside it, so two batches never mix and you can always tell which resume came
from which run. Two runs in the same minute get `… (2)`, `… (3)`. The
completion message, the **Open Output Folder** button and the History window
all point at the specific run, not the folder you chose months ago.

This is the layout the everyday job wants: a pile of resumes, a pile of cover
letters, a pile of application reports — not fifty candidate folders to open
one at a time. Anything still flagged when it is exported goes to **Needs
Review** instead of its usual type folder, so it never gets lost among
documents that are already known to be right, and never gets silently counted
as "saved" either.

- A collision gets a numeric suffix — a second `Marcus Delgado.pdf` becomes
  `Marcus Delgado_2.pdf`; existing files are never overwritten.
- A document with no identified candidate is named `Unknown_001.pdf`,
  `Unknown_002.pdf`, and so on.
- The saved PDFs contain the **original pages**, untouched: same quality, same
  size, still searchable if the original was.

### The older, candidate-first layout

**Settings → Output** can switch back to one folder per candidate instead —
useful if you want every one of an applicant's documents sitting together:

```
AS Resume Sorter/
    2026-08-26_10-32-AM/
        Benjamin Perez/
            Benjamin_Perez_Application_Report.pdf
            Benjamin_Perez_Resume.pdf
            Benjamin_Perez_Cover_Letter.pdf
            Benjamin_Perez_Complete_Packet.pdf     ← all three, in one file
        Unknown/
            Unknown_Resume_001.pdf
        DocumentIndex.xlsx
```

Turn on **Create a folder for each candidate instead** to use it (this turns
off document-type folders, since the two layouts are mutually exclusive), and
**Create a combined packet PDF for each candidate** to also get everything an
applicant submitted stitched into one file, assembled in a fixed order —
application report, resume, cover letter, references, transcript, writing
sample, portfolio — regardless of what order the source PDF used. Missing
types are skipped, and page order inside each document is left exactly as it
was. The Excel index (`DocumentIndex.xlsx`, listing every document with its
applicant, packet, contact details, page range and confidence) is also
opt-in, in either layout.

### Saving only some document types

The document-type chips on the home screen (**Choosing what you need**,
above) are the everyday way to do this. **Settings → Output → Save which
documents** offers the same choice for anyone who prefers Settings.

This only affects what is written to disk. Everything is still detected,
grouped and shown in review, so narrowing the output hides nothing and you can
change your mind and re-export without analysing again.

## History

**History** shows previous jobs — when they ran, how much was processed, and a
button to open the output folder again. Only these summaries are stored; the
contents of your documents are not kept.

## Scanned PDFs

**Nothing to set up.** The installer includes an OCR engine (Tesseract, with
English), so scanned PDFs are read automatically on a brand-new computer.

You can confirm what it's using at any time:

```powershell
& "$env:ProgramFiles\AS Resume Sorter\SmartPDFSorter.exe" --ocr-info
```

The output appears a moment after your prompt returns. AS Resume Sorter is a
windowed program, so PowerShell does not wait for it — see
[Scripting the command line](#scripting-the-command-line) if you need to check
the result in a script.

To use a different Tesseract build, set its path in **Settings → OCR**; an
explicit path always overrides the bundled engine.

OCR is only used to *understand* a page. Your exported PDF always contains the
original scan, unchanged — never a re-rendered copy.

## Privacy

Which mode you pick decides whether anything leaves your computer:

| Mode | Where your documents go |
|------|------------------------|
| **Rules Only** *(default)* | Nowhere. Everything happens on this computer. No internet needed, no account. |
| **Ollama** | To the Ollama server you configure — normally your own machine. Not to any third party. |
| **OpenAI** | Extracted page text is sent to OpenAI to be analysed. Applicant details in that text leave your computer. |

The current mode is always shown at the top of the window. If you choose OpenAI,
the Settings screen says plainly what gets sent.

Whichever mode you use:

- Nothing is ever uploaded silently.
- Your API key is stored in the Windows Credential Manager, never in a settings
  file, and never written to the log.
- Document text is not written to the log.
- There is no telemetry or analytics of any kind.

---

# Developer Instructions

## Requirements

- Python 3.11 or newer (3.12 recommended, and used by the release build)
- Windows for producing the `.exe`; development works on Windows, macOS or Linux
- Optional: [Tesseract OCR](https://github.com/UB-Mannheim/tesseract/wiki) for scanned pages

## Setting up

```bash
git clone https://github.com/kingnazz/AnotherSmartSort.git
cd AnotherSmartSort

python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements-dev.txt
```

On a bare Linux box Qt also needs its runtime libraries:

```bash
sudo apt-get install -y libegl1 libgl1 libxkbcommon-x11-0 libdbus-1-3 \
  libfontconfig1 libxcb-cursor0 libxcb-icccm4 libxcb-keysyms1 libxcb-shape0 \
  libxcb-randr0 libxcb-render-util0 libxcb-xinerama0 libxcb-xkb1 libxrender1
```

## Running the application

```bash
python -m app.main
```

## Running the tests

```bash
python -m pytest
```

Headless (CI, containers, or a machine with no display):

```bash
QT_QPA_PLATFORM=offscreen python -m pytest        # macOS / Linux
$env:QT_QPA_PLATFORM="offscreen"; python -m pytest # Windows PowerShell
```

Useful selections:

```bash
python -m pytest -m gui              # only the UI tests
python -m pytest -m "not gui"        # everything except the UI tests
python -m pytest -m ocr_real -v      # only the tests that run a real OCR binary
python -m pytest tests/test_grouping.py -v
```

The `ocr_real` tests run an actual Tesseract binary against a generated
image-only PDF and skip automatically when no engine is available. They are the
only tests that prove the scanned-document path really works.

## Generating sample PDFs

The tests build their own fixtures, so no binary files are committed. To get a
folder of samples to click through by hand:

```bash
python scripts/generate_samples.py            # writes ./samples
python scripts/generate_samples.py C:\temp\qa # or somewhere else
```

These cover a full applicant packet, a 3-page resume, a 2-page cover letter, a
scanned document with no text layer, an ambiguous page, separator pages, two
candidates in one file, and a transcript.

## OCR configuration

Installed builds ship their own Tesseract, so there is nothing to configure.
Discovery order is:

1. the path set in **Settings → OCR** (an explicit path always wins)
2. the runtime bundled with this installation
3. `tesseract` on `PATH`
4. the usual install locations

Check what a build resolved:

```bash
python -m app.main --ocr-info
```

Running from source there is no bundled runtime, so install Tesseract to work on
OCR features:

```bash
sudo apt-get install tesseract-ocr tesseract-ocr-eng   # Linux
winget install UB-Mannheim.TesseractOCR                # Windows
```

Or stage the real bundle locally, exactly as the installer does:

```bash
python scripts/fetch_ocr_runtime.py --output ocr
```

## Configuring OpenAI

**Settings → Classification → OpenAI**. Paste an API key and choose a model
(default `gpt-4o-mini`).

The key goes to the OS credential store via `keyring`; it is never written to
`settings.json` or to the log. Nothing in the repository contains a key.

Local rules run first on every page, and only pages the rules are unsure about
are sent to OpenAI. Identical pages are answered from cache, so a large batch of
similar documents doesn't pay repeatedly. The end-of-job summary reports how many
pages were classified locally versus by AI, and how many requests were made.

## Configuring Ollama

**Settings → Classification → Ollama**. Set the address (default
`http://localhost:11434`) and the model (default `llama3.1`), then use
**Test connection** — it tells you if the server is down or the model isn't
installed, including the exact `ollama pull` command to fix it.

## Building the Windows distribution

One command produces everything needed to deploy to clients:

```powershell
.\scripts\build_windows.ps1 -Clean
```

Result:

```
artifacts\
    SmartPDFSorter-Setup-1.0.1.msi        <- primary: install this on client PCs
    SmartPDFSorter-Portable-1.0.1.exe     <- secondary: no installation
    SHA256SUMS.txt
```

The script validates the environment, manages `.venv`, installs dependencies,
runs the full test suite (**and stops if anything fails**), downloads and stages
the pinned OCR runtime, builds both application flavours, runs each one's smoke
test, builds the MSI with WiX, verifies the MSI's metadata, and writes checksums.

Options:

```powershell
.\scripts\build_windows.ps1 -Clean          # remove build\, dist\ and artifacts\ first
.\scripts\build_windows.ps1 -SkipTests      # build without running the tests
.\scripts\build_windows.ps1 -SkipPortable   # MSI only
.\scripts\build_windows.ps1 -Sign           # Authenticode-sign the binaries and MSI
```

### Build prerequisites

- Windows 10/11, 64-bit
- Python 3.11+ (3.12 recommended)
- .NET SDK — for the WiX toolset, which the script installs on first run
- 7-Zip — to extract the pinned OCR runtime (`winget install 7zip.7zip`)

### The two builds, and why they differ

| | Installed (MSI) | Portable |
|---|---|---|
| PyInstaller mode | onedir | onefile |
| Startup | fast — nothing is unpacked | slower — unpacks to `%TEMP%` each launch |
| Bundled OCR | yes (~130 MB) | no |
| Antivirus false positives | rare | more common (self-extracting) |
| Windows Installer repair | yes | n/a |

A onefile build carrying the OCR engine would unpack ~130 MB on every launch, so
OCR is bundled only in the installed build, which is the one clients get.

### Bundled OCR

`scripts/fetch_ocr_runtime.py` downloads a SHA-256-pinned Tesseract build,
verifies it, and stages only what is needed to run recognition — the executable,
the transitive closure of DLLs it actually imports (26 of the 51 shipped), and
the `eng`/`osd` trained data. That is ~130 MB instead of the installer's 239 MB.

```powershell
python scripts\fetch_ocr_runtime.py                # stage into .\ocr
python scripts\fetch_ocr_runtime.py --verify-only  # just check the pin
```

To move to a newer Tesseract, update `PINNED` in that script (version, URL and
hash together), re-run with `--verify-only`, and update `THIRD_PARTY_NOTICES.md`.

### Code signing

The build is signing-ready but needs no certificate for development. Signing
happens at two points, in this order: application binaries first, then the MSI —
signing the MSI before its payload is final would invalidate it.

```powershell
$env:SPS_SIGN_THUMBPRINT = '<certificate thumbprint in the machine store>'
# or
$env:SPS_SIGN_CERT_PATH = 'C:\secure\codesign.pfx'
$env:SPS_SIGN_CERT_PASSWORD = '...'

.\scripts\build_windows.ps1 -Clean -Sign
```

Never commit a certificate, key or password. In CI, prefer a certificate in the
machine store or a cloud signing service.

## Versioning

`app/version.py` is the only place the version is written. It drives the Python
package, the About dialog, the Windows file metadata, the MSI `ProductVersion`,
both artifact filenames and the CI artifact names.

To release a new version: edit `app/version.py`, commit, then tag `vX.Y.Z`. The
release workflow refuses to build if the tag and `app/version.py` disagree.

The MSI's `UpgradeCode` is fixed forever (`7B3F2E64-…`). Changing it would make
Windows treat a new build as unrelated software and install it alongside the old
one instead of upgrading.

## GitHub Actions

`.github/workflows/windows-build.yml` runs on every push and pull request and is
the production gate. On `windows-latest` it:

1. runs the test suite
2. stages and verifies the bundled OCR runtime
3. runs the real OCR integration tests against that runtime
4. builds the installed and portable applications
5. smoke-tests both before packaging
6. builds the MSI and verifies its metadata and `UpgradeCode`
7. installs the MSI silently
8. verifies the installed files, version, smoke test and bundled OCR
9. verifies Programs and Features registration and the Start Menu shortcut
10. verifies user data lives outside Program Files
11. uninstalls silently and verifies removal — while confirming user data survives
12. re-installs over the top to prove an in-place upgrade preserves user data
13. uploads the MSI, the portable EXE and `SHA256SUMS.txt`

A Linux job runs the same tests plus the real OCR integration tests, which
catches path and encoding mistakes a Windows-only pipeline would miss.

`.github/workflows/release.yml` runs on a `vX.Y.Z` tag: it builds through the
same build script, installs and uninstalls the MSI to prove the artifact works,
then creates a **draft** GitHub Release with both artifacts and checksums.
Publishing stays a deliberate human action.

## Quality tooling

### Measuring accuracy on real documents

Synthetic tests prove the pipeline behaves as designed; they say nothing about
accuracy on a client's real applicant packets. When you have real PDFs:

```powershell
# 1. Put the PDFs in qa\input\  (git-ignored — confidential documents never get committed)
# 2. Bootstrap labels from the current predictions, then CORRECT THEM BY HAND
python -m scripts.evaluate_corpus --make-template qa\input > qa\expected.json

# 3. Measure
python -m scripts.evaluate_corpus --input qa\input --ground-truth qa\expected.json
```

The template comes out grouped by applicant, which is how a mixed batch reads
when you are working through it:

```json
{
  "documents": {
    "Applicants_2026.pdf": {
      "candidates": [
        {
          "name": "Jane Smith",
          "documents": [
            {"type": "Application Report", "pages": [1, 2, 3]},
            {"type": "Resume", "pages": [4, 5]},
            {"type": "Cover Letter", "pages": [6]}
          ]
        }
      ]
    }
  }
}
```

It reports three levels of accuracy, because they fail for different reasons:

| Metric | Question it answers |
|--------|--------------------|
| Page type | What kind of document is this page? |
| Boundary / whole document | Which pages form one document? |
| **Candidate packet** | **Which documents belong to the same person?** |
| Document association | Did this document reach the right applicant? |

Plus false candidate merges (two people combined into one packet), false
candidate splits (one person scattered across several), how many documents were
left unassigned, how many were attributed but flagged, and the specific files
that went wrong.

**Candidate packet accuracy is the one that matters.** A page read as the wrong
type is a small annoyance; a document filed under the wrong applicant is lost.

Pages your ground truth does not label — separator sheets, blanks — are ignored
rather than counted against a packet, so you do not have to label every page to
get a meaningful number. `qa/expected.example.json` shows both this format and
the older flat one, which still loads.

### Benchmarking a large batch

```powershell
python -m scripts.benchmark_batch --pdfs 500 --export
python -m scripts.benchmark_batch --quick          # 50 PDFs
```

Reports elapsed time, PDFs/min, pages/min, peak memory and retained memory per
PDF. It never runs as part of the normal test suite.

## Architecture

```
app/
    main.py                     entry point, logging, global error handling
    models/                     typed domain models (page, document, packet,
                                candidate, job)
    profiles/                   document profiles; recruiting is built in
    intelligence/               provider interface + rules, OpenAI, Ollama
    services/                   PDF, text features, anchor scan, classification,
                                boundaries, grouping, identity, candidate
                                packets, corrections/undo, metadata, OCR,
                                export, Excel, discovery
        parsers/                one parser per recognised export format,
                                plus the registry that chooses between them
    storage/                    settings (+ credential store) and SQLite history
    workers/                    Qt threads: analysis, export, thumbnails
    ui/                         theme, main window, review workspace, dialogs
    utils/                      filenames, hashing, logging, paths
```

Four rules hold the design together:

**A known file format is handled deterministically, ahead of everything
else.** `app/services/parsers/` holds one parser per recognised format and a
registry that picks between them. The priority order is:

1. Known format parser (`ATSParserRegistry`)
2. Anchor-first structural pass (`AnchorScanner`) + generic classifier
3. Document-level AI fallback (`DocumentReviewService`)
4. Needs Review, when still uncertain

The registry asks every parser how strongly it claims a file and runs only
the strongest — so a file is never half-parsed by one and re-parsed by
another, and a parser that throws has its partial work rolled back. Below a
confidence floor nothing is chosen and the file falls through: forcing the
closest parser onto an unfamiliar format produces confident, wrong
extractions, which is worse than the generic path's honest uncertainty.

A parser never scores a page. It walks the file once, assigning type,
boundary and identity from the format's own structure, and skips the rules
classifier and any AI escalation for that file entirely.

**Structure is stronger evidence than appearance.** Confidence is tracked
separately for structure, type and identity (`SourceFileAnalysis
.structure_confidence`). An interior page of a long resume looks like very
little on its own, and letting that drag a document's confidence down is what
used to send correct documents to review.

**Corrections go through services, and every one is undoable.** The review
board's drag and drop calls `GroupingService` / `CandidatePacketService`
through `CorrectionHistory`, which snapshots document structure before and
after. Undo never re-runs analysis — that would discard every other
correction the user had made.

**One place starts external processes.** `app/utils/external_process.py` is
the only module allowed to launch a child process, so nothing can put a
console window on screen. A test walks the AST of every module and fails if
anything else tries.

**The three questions are answered by three separate subsystems.**
`RulesProvider` answers "what kind of document is this page?"; `BoundaryEngine`
separately answers "does this page start a new document?"; `CandidatePacketService`
separately again answers "which applicant does this finished document belong
to?" None of the three reaches into another's reasoning, and they share only
`PageFeatures` and the finished models between them.

A page whose type score wobbles does not split a document — splitting requires
positive structural evidence such as a restarted page count, a letter
salutation, or a different applicant's name. And a document is attributed to a
person by identity evidence, not by the classifier's opinion of it.

**Attribution runs as its own pass over finished documents.** Association needs
the whole file in view: whether page 6 belongs to Jane depends on what page 7
turns out to be. So `build_packets` runs after grouping is complete, never
interleaved with it.

**Domain logic never lives in the UI.** Every correction the review workspace
offers is a call into `GroupingService` or `CandidatePacketService`, so the same
rules re-derive state no matter how a change was made. `app/services/` imports
no Qt.

Adding a new document profile (Accounting, Legal, Medical Records) means adding
one module under `app/profiles/` and registering it — no changes to the pipeline,
the grouping engine, or the UI.

## Where files live

| What | Windows | macOS / Linux |
|------|---------|---------------|
| Settings | `%LOCALAPPDATA%\SmartPDFSorter\settings.json` | `~/.local/share/SmartPDFSorter/settings.json` |
| History | `%LOCALAPPDATA%\SmartPDFSorter\history.sqlite3` | `~/.local/share/SmartPDFSorter/history.sqlite3` |
| Logs | `%LOCALAPPDATA%\SmartPDFSorter\logs\` | `~/.local/share/SmartPDFSorter/logs/` |

Set `SMART_PDF_SORTER_HOME` to override the location, and
`SMART_PDF_SORTER_LOG_LEVEL=DEBUG` for verbose logging.

The `SmartPDFSorter` folder name predates the rename to AS Resume Sorter and is
kept on purpose. It is the address of every existing installation's settings and
history; renaming it would not move that data, it would abandon it, and an
upgrade would look like the application had forgotten everything it knew. The
same reasoning keeps the `SMART_PDF_SORTER_*` variables above under their
original names.

## Current state

`IMPLEMENTATION_STATUS.md` tracks what is finished, what is not, and the known
limitations.
