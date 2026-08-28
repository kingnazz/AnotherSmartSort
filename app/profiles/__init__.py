"""Document profiles.

Register additional profiles here; nothing else in the application needs to
change for a new profile to appear in Settings.
"""

from __future__ import annotations

from .base import OTHER, DocumentProfile, ProfileRegistry, Signal
from .recruiting import RECRUITING_PROFILE

PROFILE_REGISTRY = ProfileRegistry([RECRUITING_PROFILE])

DEFAULT_PROFILE_NAME = RECRUITING_PROFILE.name


def get_profile(name: str | None = None) -> DocumentProfile:
    """Return a registered profile by name (falls back to the default)."""
    return PROFILE_REGISTRY.get(name)


def available_profiles() -> list[str]:
    return PROFILE_REGISTRY.names()


__all__ = [
    "DocumentProfile",
    "Signal",
    "ProfileRegistry",
    "PROFILE_REGISTRY",
    "DEFAULT_PROFILE_NAME",
    "get_profile",
    "available_profiles",
    "RECRUITING_PROFILE",
    "OTHER",
]
