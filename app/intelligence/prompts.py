"""Prompt construction for AI intelligence providers.

Context is deliberately bounded: the current page plus a short tail of the
previous page and head of the next one. Whole PDFs are never sent, and only the
text needed to classify is included.
"""

from __future__ import annotations

from app.intelligence.base import PageContext

#: Hard caps on how much text leaves the machine for a single page.
MAX_PAGE_CHARS = 4000
MAX_NEIGHBOUR_CHARS = 500

SYSTEM_PROMPT = (
    "You are a document analyst that separates a combined PDF into logical documents.\n"
    "You answer two independent questions about a single page:\n"
    "  1. What kind of document is this page part of?\n"
    "  2. Does this page START a new document, or CONTINUE the previous one?\n\n"
    "These are different questions. Page 2 of a three-page resume is a Resume page "
    "that CONTINUES the previous document. Never mark a page as starting a new "
    "document just because it looks slightly different from the page before it. "
    "Only mark a new document when the page genuinely opens one: a new letter "
    "salutation, a restarted page count, a new person's header, a title page, or a "
    "clear change of document kind.\n\n"
    "Respond with a single JSON object and nothing else."
)

_RESPONSE_SHAPE = """{
  "document_type": "<one of: %s>",
  "classification_confidence": 0.0,
  "starts_new_document": true,
  "boundary_confidence": 0.0,
  "candidate_name": null,
  "email": null,
  "phone": null,
  "linkedin": null,
  "job_title": null,
  "applicant_id": null,
  "reasoning_summary": "<one short sentence>"
}"""


def build_user_prompt(context: PageContext) -> str:
    """Build the per-page user prompt from bounded context."""
    types = ", ".join(context.document_types) or "Other"
    sections: list[str] = [
        f"Document profile: {context.profile_name or 'Recruiting'}",
        f"Allowed document types: {types}",
        f"Page {context.page_number} of {context.page_count} in the source PDF.",
    ]

    if context.previous_type:
        sections.append(
            f"Previous page was classified as: {context.previous_type} "
            f"(confidence {context.previous_confidence:.2f})."
        )
    else:
        sections.append("There is no previous page; this is the first page of the PDF.")

    if context.previous_group_type:
        sections.append(
            f"The document currently being assembled is a {context.previous_group_type} "
            f"and so far contains {context.previous_group_page_count} page(s)."
        )

    known = context.candidate
    if not known.is_empty:
        identity = ", ".join(
            part
            for part in (
                f"name={known.name}" if known.name else "",
                f"email={known.email}" if known.email else "",
                f"applicant_id={known.applicant_id}" if known.applicant_id else "",
            )
            if part
        )
        if identity:
            sections.append(f"Identity detected locally on this page: {identity}.")

    if context.previous_text_tail.strip():
        sections.append(
            "END OF PREVIOUS PAGE:\n"
            f"{context.previous_text_tail[-MAX_NEIGHBOUR_CHARS:].strip()}"
        )

    sections.append(
        "CURRENT PAGE TEXT:\n" + (context.text[:MAX_PAGE_CHARS].strip() or "(no readable text)")
    )

    if context.next_text_head.strip():
        sections.append(
            "START OF NEXT PAGE:\n" + context.next_text_head[:MAX_NEIGHBOUR_CHARS].strip()
        )

    sections.append(
        "Reply with exactly this JSON shape:\n" + (_RESPONSE_SHAPE % types)
    )
    return "\n\n".join(sections)


def build_messages(context: PageContext) -> list[dict[str, str]]:
    """Chat-style message list shared by the OpenAI and Ollama providers."""
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": build_user_prompt(context)},
    ]


__all__ = ["build_messages", "build_user_prompt", "SYSTEM_PROMPT", "MAX_PAGE_CHARS"]
