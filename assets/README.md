# Application artwork

Two files, and every icon the product shows comes from one of them.

| File | Used for |
|------|----------|
| `icon.png` | Taskbar and window icon, and the mark in the About dialog. Loaded at run time via `resource_path("assets", "icon.png")`. |
| `icon.ico` | The EXE's own icon (both builds), the Programs and Features entry, and the Start Menu and desktop shortcuts. Referenced by `packaging/pyinstaller_common.py` and `installer/Package.wxs`. |

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

## Current state

`icon.png` and `icon.ico` still carry the original generic document mark from
before the rename to AS Resume Sorter. The approved logo replaces both.
