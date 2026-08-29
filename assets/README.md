# Application artwork

Three files. Every icon the product shows comes from one of the first two; the
third is a documentation asset.

| File | Used for |
|------|----------|
| `icon.png` | The square mark. Taskbar and window icon, the mark in the About dialog, and the mark in the application header. Loaded at run time via `resource_path("assets", "icon.png")`. |
| `icon.ico` | The same mark, as the EXE's own icon (both builds), the Programs and Features entry, and the Start Menu and desktop shortcuts. Referenced by `packaging/pyinstaller_common.py` and `installer/Package.wxs`. |
| `logo.png` | The full wordmark -- the mark plus "AS resume sorter" set over three lines. Documentation only: it heads `README.md`. It is deliberately *not* used in the application and not bundled into the build. |

Nothing reads a name or a path out of these beyond the two filenames above, so
replacing the artwork is a matter of replacing the files — no code, spec or
installer change is involved.

## Replacing them

Drop in a new `icon.png` (square, 512×512 or larger, transparent background),
then regenerate the `.ico` from it so the two cannot drift apart:

```bash
python -c "
from PIL import Image
source = Image.open('assets/icon.png').convert('RGBA')
source.save('assets/icon.ico', sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])
"
```

The `.ico` needs all of those sizes. Windows picks a different one for the
taskbar, the Start Menu, Programs and Features and Explorer's large-icon view,
and it scales badly from a single size — an icon that looks right in one place
and blurred in another is almost always a `.ico` with one image in it.

## The mark and the wordmark are not interchangeable

The wordmark is a three-line stacked lockup, roughly 1.4:1, and most of its area
is the words. It is unusable anywhere small: Windows draws the taskbar and
Programs and Features icons at 16, 24, 32 and 48 px, and the About dialog scales
its mark into a 56x56 box, so at any of those sizes the type is two or three
pixels tall and the whole thing reads as a smudge. The application header is a
single ~44 px bar, which is the same problem again.

So the mark goes anywhere the artwork has to survive being small -- icons, the
header, About -- and the wordmark goes where there is room to read it, which
today means the README. If a splash screen or an installer banner is ever added,
those are the wordmark's other natural homes.

`tests/test_branding.py::TestTheArtwork` enforces the part a machine can check:
that `icon.png` is square, and that `icon.ico` really does carry the small sizes
rather than one large image Windows would have to downscale by itself.
