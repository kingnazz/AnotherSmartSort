"""Candidate identity normalisation and comparison.

Deciding whether two documents belong to the same person is a different problem
from deciding what kind of document a page is, and a different problem again
from deciding where one document ends. This module owns only the identity
question: given two sets of contact details, are these the same applicant?

The rules are deliberately asymmetric. Matching evidence (a shared email, a
shared applicant ID) is treated as strong; *conflicting* evidence is treated as
stronger still. Two documents that agree on a name but disagree on an email are
reported as a conflict rather than a weak match, because merging two real people
into one packet is far worse than leaving them apart for a human to join.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.models.candidate import Candidate, normalize_person_name

#: Strength at or above which two identities are considered the same person.
STRONG_MATCH = 0.85

_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_LINKEDIN_SLUG_RE = re.compile(
    r"(?i)linkedin\.com/(?:in|pub)/(?P<slug>[A-Za-z0-9\-_%]+)"
)


def normalize_email(value: str | None) -> str | None:
    """Lowercase and trim an email address for comparison."""
    if not value:
        return None
    text = str(value).strip().strip(".,;:<>()[]").lower()
    if "@" not in text or text.startswith("@") or text.endswith("@"):
        return None
    return text


def normalize_phone(value: str | None) -> str | None:
    """Reduce a phone number to comparable digits.

    ``(206) 555-1234``, ``206-555-1234``, ``2065551234`` and ``+1 206 555 1234``
    all normalise to ``2065551234``. Numbers too short to identify anybody are
    discarded rather than matched loosely.
    """
    if not value:
        return None
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    # Strip a North American country code so +1 forms match bare ones.
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) < 7:
        return None
    return digits


def normalize_linkedin(value: str | None) -> str | None:
    """Reduce a LinkedIn URL to its profile slug."""
    if not value:
        return None
    text = str(value).strip()
    match = _LINKEDIN_SLUG_RE.search(text)
    if match:
        return match.group("slug").strip("/").lower()
    # A bare slug ("jane-smith-1234") is accepted; a stray word is not.
    bare = text.strip("/").lower()
    if bare and "/" not in bare and " " not in bare and len(bare) >= 3:
        return bare
    return None


#: An applicant ID this short is not evidence that two documents share an owner.
_MIN_MATCHABLE_ID = 3


def normalize_applicant_id(value: str | None) -> str | None:
    """Compare applicant IDs without punctuation or case getting in the way.

    ``A-10482`` and ``a10482`` are the same reference. A single character is
    noise and is discarded; anything longer is kept, because a short ID that
    *disagrees* is still proof of two different people even when it is too weak
    to prove they are the same one.
    """
    if not value:
        return None
    cleaned = _NON_ALNUM_RE.sub("", str(value).lower())
    if len(cleaned) < 2:
        return None
    return cleaned


def _name_tokens(name: str | None) -> tuple[str, ...]:
    if not name:
        return ()
    normalized = normalize_person_name(name)
    return tuple(part for part in normalized.split() if part)


def _is_initial(token: str) -> bool:
    return len(token) == 1 or (len(token) == 2 and token.endswith("."))


def _initial_of(token: str) -> str:
    return token[0] if token else ""


def name_similarity(left: str | None, right: str | None) -> float:
    """How strongly two written names indicate the same person, in ``[0, 1]``.

    Handles the variations that actually occur in applicant packets: middle
    names appearing on one document only, middle initials, ``Last, First``
    ordering and shouty ALL CAPS headers. Returns ``0.0`` when the names look
    like different people, which callers treat as a conflict rather than merely
    an absence of evidence.
    """
    first_tokens = _name_tokens(left)
    second_tokens = _name_tokens(right)
    if not first_tokens or not second_tokens:
        return 0.0
    if first_tokens == second_tokens:
        return 1.0
    if len(first_tokens) < 2 or len(second_tokens) < 2:
        # A single token ("Perez") is not enough to identify a person.
        return 0.0

    # Surnames must agree; a differing surname means different people.
    if first_tokens[-1] != second_tokens[-1]:
        return 0.0

    first_given, second_given = first_tokens[0], second_tokens[0]
    if first_given != second_given:
        # "B Perez" vs "Benjamin Perez" is plausible; "Robert" vs "Benjamin" is not.
        if not (_is_initial(first_given) or _is_initial(second_given)):
            return 0.0
        if _initial_of(first_given) != _initial_of(second_given):
            return 0.0
        return 0.85

    middles_left = first_tokens[1:-1]
    middles_right = second_tokens[1:-1]
    if middles_left == middles_right:
        return 1.0
    if not middles_left or not middles_right:
        # One document carried a middle name or initial and the other did not.
        return 0.94

    # Both have middles: an initial standing in for a full middle name is fine,
    # but two different middle names suggest two different people.
    if len(middles_left) == len(middles_right):
        for one, two in zip(middles_left, middles_right):
            if one == two:
                continue
            if (_is_initial(one) or _is_initial(two)) and _initial_of(one) == _initial_of(two):
                continue
            return 0.0
        return 0.92
    return 0.0


@dataclass(frozen=True)
class IdentitySignals:
    """The comparable form of one candidate's identifying details."""

    name: str | None = None
    name_key: str = ""
    email: str | None = None
    phone: str | None = None
    linkedin: str | None = None
    applicant_id: str | None = None

    @property
    def is_empty(self) -> bool:
        return not any(
            (self.name_key, self.email, self.phone, self.linkedin, self.applicant_id)
        )

    @property
    def has_strong_identifier(self) -> bool:
        """True when something better than a name is available."""
        return bool(self.email or self.applicant_id or self.linkedin)


def identity_signals(candidate: Candidate) -> IdentitySignals:
    """Extract the comparable identity from a :class:`Candidate`."""
    return IdentitySignals(
        name=candidate.name,
        name_key=normalize_person_name(candidate.name) if candidate.name else "",
        email=normalize_email(candidate.email),
        phone=normalize_phone(candidate.phone),
        linkedin=normalize_linkedin(candidate.linkedin),
        applicant_id=normalize_applicant_id(candidate.applicant_id),
    )


@dataclass(frozen=True)
class IdentityComparison:
    """The outcome of comparing two identities."""

    strength: float = 0.0
    conflict: bool = False
    reasons: list[str] = field(default_factory=list)

    @property
    def is_match(self) -> bool:
        return not self.conflict and self.strength >= STRONG_MATCH


def _email_clash_is_decisive(left: IdentitySignals, right: IdentitySignals) -> bool:
    """Whether two different email addresses prove two different people.

    An email is self-reported and people have several: an applicant's ATS
    record commonly carries the address they registered with while the resume
    they attached prints their preferred one. Treating that as proof split a
    real applicant into two packets.

    An applicant ID is different -- the system assigns exactly one per
    applicant. So a clash is decisive only when the two sides are comparable on
    that footing: either both are ATS records with their own IDs, or neither is
    and the email is the only strong identifier either one has. When one side
    has an ID and the other does not, the ID-less document is an attachment
    inside somebody's record rather than a competing applicant, and its
    author's other address proves nothing.
    """
    both_registered = bool(left.applicant_id) and bool(right.applicant_id)
    neither_registered = not left.applicant_id and not right.applicant_id
    return both_registered or neither_registered


def compare_identities(left: IdentitySignals, right: IdentitySignals) -> IdentityComparison:
    """Decide whether two identities describe the same applicant.

    A conflict is reported when the two carry the *same kind* of strong
    identifier and those identifiers disagree. That case must never be merged on
    the strength of a matching name, because two applicants genuinely can share
    a name, and combining their packets loses documents inside another person's
    file where nobody will look for them.
    """
    reasons: list[str] = []

    if left.is_empty or right.is_empty:
        return IdentityComparison()

    # -- conflicting strong identifiers settle it immediately ---------------
    email_clash = bool(left.email and right.email and left.email != right.email)
    if email_clash and _email_clash_is_decisive(left, right):
        return IdentityComparison(
            conflict=True, reasons=[f"different email addresses ({left.email} / {right.email})"]
        )
    if left.applicant_id and right.applicant_id and left.applicant_id != right.applicant_id:
        return IdentityComparison(
            conflict=True,
            reasons=[f"different applicant IDs ({left.applicant_id} / {right.applicant_id})"],
        )
    if left.linkedin and right.linkedin and left.linkedin != right.linkedin:
        return IdentityComparison(
            conflict=True, reasons=["different LinkedIn profiles"]
        )

    names = name_similarity(left.name, right.name)
    if left.name_key and right.name_key and names == 0.0:
        return IdentityComparison(
            conflict=True, reasons=[f"different names ({left.name} / {right.name})"]
        )

    # -- matching evidence ---------------------------------------------------
    strength = 0.0
    if left.email and left.email == right.email:
        strength = max(strength, 0.98)
        reasons.append(f"same email ({left.email})")
    if (
        left.applicant_id
        and left.applicant_id == right.applicant_id
        and len(left.applicant_id) >= _MIN_MATCHABLE_ID
    ):
        strength = max(strength, 0.98)
        reasons.append(f"same applicant ID ({left.applicant_id})")
    if left.linkedin and left.linkedin == right.linkedin:
        strength = max(strength, 0.95)
        reasons.append("same LinkedIn profile")
    if left.phone and left.phone == right.phone:
        # Weaker on its own: households and small firms share numbers.
        strength = max(strength, 0.88)
        reasons.append("same phone number")
    if names >= 1.0:
        strength = max(strength, 0.93)
        reasons.append(f"same name ({right.name})")
    elif names > 0.0:
        strength = max(strength, 0.80 + 0.1 * names)
        reasons.append(f"matching name ({left.name} / {right.name})")

    if not reasons:
        return IdentityComparison()

    if email_clash:
        # Tolerated, not ignored: cap the match below the "no review needed"
        # bar so a reviewer is shown the two addresses and can say whether this
        # is one person with two of them.
        strength = min(strength, 0.86)
        reasons.append(
            f"but two different email addresses ({left.email} / {right.email})"
        )

    return IdentityComparison(strength=round(min(strength, 0.99), 4), reasons=reasons)


def merge_signals(left: IdentitySignals, right: IdentitySignals) -> IdentitySignals:
    """Combine two agreeing identities, keeping whichever details exist."""
    return IdentitySignals(
        name=left.name or right.name,
        name_key=left.name_key or right.name_key,
        email=left.email or right.email,
        phone=left.phone or right.phone,
        linkedin=left.linkedin or right.linkedin,
        applicant_id=left.applicant_id or right.applicant_id,
    )


__all__ = [
    "IdentitySignals",
    "IdentityComparison",
    "STRONG_MATCH",
    "identity_signals",
    "compare_identities",
    "merge_signals",
    "name_similarity",
    "normalize_email",
    "normalize_phone",
    "normalize_linkedin",
    "normalize_applicant_id",
]
