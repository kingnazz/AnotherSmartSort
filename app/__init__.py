"""AS Resume Sorter application package."""

from app.version import __version__

#: The product name, everywhere a person can see it: window title, About,
#: installer, Programs and Features, Start Menu. Changing it here changes it
#: everywhere that matters.
APP_NAME = "AS Resume Sorter"
APP_VERSION = __version__

#: The folder under %LOCALAPPDATA% holding settings.json and history.sqlite3.
#:
#: Deliberately NOT renamed alongside APP_NAME. It is the address of every
#: existing installation's saved settings and processing history; changing it
#: would not migrate that data, it would abandon it -- an upgrade would look to
#: the user like the application had forgotten everything it knew. Nobody sees
#: this string, so there is nothing to gain against that.
ORG_NAME = "SmartPDFSorter"

#: Shown as the Publisher in the About dialog, in Programs and Features, and in
#: the installer. It mirrors the product name, as it did before the rebrand --
#: there is no separate publishing organisation to name, and inventing one would
#: put a company on a Windows certificate-adjacent field that does not exist.
PUBLISHER = APP_NAME

__all__ = ["APP_NAME", "APP_VERSION", "ORG_NAME", "PUBLISHER", "__version__"]
