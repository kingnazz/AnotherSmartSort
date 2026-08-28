"""Candidate / applicant identity model."""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Any


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split()).strip()
    return text or None


@dataclass
class Candidate:
    """Identity metadata associated with a page or a document group.

    Every field is optional: real-world PDFs frequently reveal only a subset.
    """

    name: str | None = None
    email: str | None = None
    phone: str | None = None
    linkedin: str | None = None
    job_title: str | None = None
    applicant_id: str | None = None

    #: Names seen that disagree with :attr:`name` (drives review flagging).
    conflicting_names: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        for f in fields(self):
            if f.name == "conflicting_names":
                continue
            setattr(self, f.name, _clean(getattr(self, f.name)))

    @property
    def is_empty(self) -> bool:
        return not any(
            (self.name, self.email, self.phone, self.linkedin, self.job_title, self.applicant_id)
        )

    @property
    def has_conflict(self) -> bool:
        return bool(self.conflicting_names)

    @property
    def display_name(self) -> str:
        """Name suitable for folders and UI, falling back sensibly."""
        if self.name:
            return self.name
        if self.email:
            return self.email.split("@")[0].replace(".", " ").title()
        if self.applicant_id:
            return f"Applicant {self.applicant_id}"
        return "Unknown"

    @property
    def identity_key(self) -> str | None:
        """Stable key used to decide whether two pages describe one person."""
        if self.email:
            return f"email:{self.email.lower()}"
        if self.applicant_id:
            return f"id:{self.applicant_id.lower()}"
        if self.name:
            return f"name:{normalize_person_name(self.name)}"
        return None

    def merged_with(self, other: "Candidate") -> "Candidate":
        """Combine two identities, preferring existing values on ``self``.

        Disagreeing names are recorded rather than silently overwritten so the
        review workspace can flag ambiguous identity.
        """
        merged = Candidate(
            name=self.name or other.name,
            email=self.email or other.email,
            phone=self.phone or other.phone,
            linkedin=self.linkedin or other.linkedin,
            job_title=self.job_title or other.job_title,
            applicant_id=self.applicant_id or other.applicant_id,
        )
        conflicts = list(self.conflicting_names)
        for extra in other.conflicting_names:
            if extra not in conflicts:
                conflicts.append(extra)
        if (
            self.name
            and other.name
            and normalize_person_name(self.name) != normalize_person_name(other.name)
            and other.name not in conflicts
        ):
            conflicts.append(other.name)
        merged.conflicting_names = conflicts
        return merged

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "email": self.email,
            "phone": self.phone,
            "linkedin": self.linkedin,
            "job_title": self.job_title,
            "applicant_id": self.applicant_id,
            "conflicting_names": list(self.conflicting_names),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "Candidate":
        if not data:
            return cls()
        candidate = cls(
            name=data.get("name"),
            email=data.get("email"),
            phone=data.get("phone"),
            linkedin=data.get("linkedin"),
            job_title=data.get("job_title"),
            applicant_id=data.get("applicant_id"),
        )
        conflicts = data.get("conflicting_names") or []
        candidate.conflicting_names = [str(c) for c in conflicts]
        return candidate


#: Post-nominal letters that decorate a name without identifying anybody.
#: Generational suffixes (Jr, Sr, III) are deliberately absent: those do
#: distinguish people, and dropping them could merge a father with his son.
CREDENTIALS: frozenset[str] = frozenset(
    {
        "phd", "ph.d", "ph.d.", "dphil", "md", "m.d", "m.d.", "do", "dds", "dmd",
        "dvm", "edd", "ed.d", "ed.d.", "psyd", "dnp", "pharmd", "jd", "j.d",
        "j.d.", "llm", "esq", "esq.", "mba", "mpa", "mph", "msw", "mfa", "med",
        "ma", "ms", "msc", "mres", "ba", "bs", "bsc", "bsn", "msn", "rn", "np",
        "pa", "cpa", "cfa", "pmp", "pe", "cfp", "shrm", "sphr", "phr", "cissp",
        "lcsw", "lmft", "lpc", "otr", "pt", "dpt", "rd", "ccc",
    }
)


def strip_credentials(name: str) -> str:
    """Remove trailing post-nominals: ``Amara Okonjo, PhD`` -> ``Amara Okonjo``.

    Done before any ``Last, First`` handling, because a credential sits after a
    comma in exactly the place a given name would -- without this, ``Okonjo,
    PhD`` inverts into the person's name becoming "PhD".
    """
    text = " ".join(str(name).split()).strip()
    if not text:
        return ""
    # Trailing credentials may be comma-separated or simply appended.
    while True:
        stripped = text.rstrip(" ,.")
        parts = stripped.replace(",", " ").split()
        if len(parts) > 1 and parts[-1].lower().strip(".") in CREDENTIALS:
            # Rebuild without the final token, preserving any earlier comma.
            cut = stripped.rfind(parts[-1])
            text = stripped[:cut].rstrip(" ,.")
            continue
        return text


def normalize_person_name(name: str) -> str:
    """Normalize a human name for comparison.

    Handles ``"Perez, Benjamin"`` vs ``"Benjamin Perez"``, trailing credentials,
    and casing/punctuation differences, so identity continuity survives the
    formatting changes that occur between an ATS record and a resume header.
    """
    text = " ".join(strip_credentials(name).split()).strip().lower()
    if not text:
        return ""
    if "," in text:
        last, _, first = text.partition(",")
        text = f"{first.strip()} {last.strip()}".strip()
    cleaned = "".join(ch for ch in text if ch.isalnum() or ch.isspace())
    parts = [p for p in cleaned.split() if len(p) > 1 or p.isalpha()]
    return " ".join(parts)


__all__ = ["Candidate", "normalize_person_name", "strip_credentials", "CREDENTIALS"]
