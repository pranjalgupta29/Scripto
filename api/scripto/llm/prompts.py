"""Prompts and their JSON schemas.

Every schema carries a `title`, which is both the structured-output contract and
the dispatch key the fake provider uses. Keep them in sync.
"""

from __future__ import annotations

from typing import Any

# --------------------------------------------------------------------------
# identify
# --------------------------------------------------------------------------

IDENTIFY_SYSTEM = """You disambiguate people from web search results for a podcast \
research tool. Picking the wrong person poisons every downstream step, so you are \
conservative: you would rather return two plausible candidates than one confident \
wrong answer.

Return only candidates genuinely supported by the evidence. Never invent an \
employer, role or photo URL that does not appear in the results."""

IDENTIFY_SCHEMA: dict[str, Any] = {
    "title": "identify_candidates",
    "type": "object",
    "additionalProperties": False,
    "required": ["candidates"],
    "properties": {
        "candidates": {
            "type": "array",
            "maxItems": 5,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "headline", "employer", "evidence_urls", "confidence"],
                "properties": {
                    "name": {"type": "string"},
                    "headline": {"type": "string"},
                    "employer": {"type": "string"},
                    "photo_url": {"type": ["string", "null"]},
                    "evidence_urls": {"type": "array", "items": {"type": "string"}},
                    "confidence": {"type": "number"},
                    "reasoning": {"type": "string"},
                },
            },
        }
    },
}


def identify_prompt(name: str, disambiguator: str, results: list[dict]) -> str:
    lines = [
        f"Name: {name}",
        f"Disambiguator: {disambiguator}",
        "",
        "Search results:",
    ]
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {r.get('title') or 'untitled'} -- {r.get('url')}")
        if r.get("snippet"):
            lines.append(f"   {r['snippet'][:300]}")
    lines += [
        "",
        "Return 2 to 5 candidate identities, ranked by confidence, that this name plus "
        "disambiguator could refer to. If several results clearly describe the same "
        "person, merge them into one candidate.",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# extract claims
# --------------------------------------------------------------------------

EXTRACT_SYSTEM = """You extract atomic factual claims about a specific person from a \
passage of text.

Rules:
- One idea per claim. Split compound sentences.
- `quote` must be copied EXACTLY, character for character, from the CHUNK text -- \
the shortest span that supports the claim. Do not paraphrase, reword, trim to a \
summary, or fix typos. The caller locates your quote in the source by exact string \
match and discards any claim whose quote cannot be found, so an approximate quote \
costs you the claim.
- Only claims about the named subject. A passage mentioning several people yields \
claims only about the subject.
- Do not infer, summarise across sentences, or add outside knowledge."""

EXTRACT_SCHEMA: dict[str, Any] = {
    "title": "extract_claims",
    "type": "object",
    "additionalProperties": False,
    "required": ["claims"],
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["text", "kind", "quote"],
                "properties": {
                    "text": {"type": "string"},
                    "kind": {
                        "type": "string",
                        "enum": [
                            "biographical",
                            "opinion",
                            "fact",
                            "anecdote",
                            "prediction",
                        ],
                    },
                    "claim_date": {"type": ["string", "null"]},
                    "quote": {
                        "type": "string",
                        "description": "Exact substring of the chunk supporting the claim.",
                    },
                },
            },
        }
    },
}


def extract_prompt(subject: str, chunk_text: str) -> str:
    return (
        f"Subject: {subject}\n\n"
        "Extract claims about the subject from the passage below. For each claim, "
        "copy the exact supporting text into `quote`.\n\n"
        f"<CHUNK>\n{chunk_text}\n</CHUNK>"
    )


# --------------------------------------------------------------------------
# cluster confirmation
# --------------------------------------------------------------------------

CLUSTER_SYSTEM = """You decide whether a group of claims are all restatements of the \
same underlying point. Similar topic is not enough -- they must assert the same thing. \
If they do, write the clearest one-sentence canonical phrasing."""

CLUSTER_SCHEMA: dict[str, Any] = {
    "title": "confirm_cluster",
    "type": "object",
    "additionalProperties": False,
    "required": ["same_claim", "canonical_text"],
    "properties": {
        "same_claim": {"type": "boolean"},
        "canonical_text": {"type": "string"},
    },
}


def cluster_prompt(claims: list[str]) -> str:
    body = "\n".join(f"- {c}" for c in claims)
    return f"<CLAIMS>\n{body}\n</CLAIMS>\n\nAre these the same underlying claim?"


# --------------------------------------------------------------------------
# dossier composition
# --------------------------------------------------------------------------

DOSSIER_SYSTEM = """You write one section of a podcast host's research dossier.

Every sentence you write must rest on the supplied claims and must list the claim \
ids it uses. A sentence you cannot attribute to at least one claim id will be \
deleted before the host ever sees it, so do not write one. Do not add background \
knowledge, however obvious it seems.

Be specific and concrete. The host is preparing to interview this person; vague \
summary is worse than nothing."""

DOSSIER_SCHEMA: dict[str, Any] = {
    "title": "compose_dossier_section",
    "type": "object",
    "additionalProperties": False,
    "required": ["items"],
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["text", "claim_ids"],
                "properties": {
                    "text": {"type": "string"},
                    "claim_ids": {"type": "array", "items": {"type": "string"}},
                },
            },
        }
    },
}


SECTION_BRIEFS = {
    "career_timeline": "A chronological career timeline. One item per role or move, "
    "earliest first. Include dates where the claims give them.",
    "recent_news": "What has happened involving this person in the last 12 months.",
    "public_positions": "Positions they hold publicly, and how those have shifted over "
    "time. Note contradictions between earlier and later claims explicitly.",
    "already_covered": "Points this person has made repeatedly across several "
    "interviews. The host uses this to avoid asking what they have answered many times.",
    "unexplored_angles": "Topics adjacent to their expertise where public coverage is "
    "thin. These are opportunities for original material.",
    "topic_brief": "A briefing on the subject itself: state of play, live debates, and "
    "notable recent developments.",
}


def dossier_prompt(section: str, subject: str, claims: list[tuple[str, str, str | None]]) -> str:
    lines = [
        f"Subject: {subject}",
        f"Section: {section}",
        f"Brief: {SECTION_BRIEFS.get(section, '')}",
        "",
        "Claims available to you (id in brackets):",
    ]
    for claim_id, text, date in claims:
        stamp = f" ({date})" if date else ""
        lines.append(f"[{claim_id}]{stamp} {text}")
    lines += ["", "Write the section as a list of items, each citing its claim ids."]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# script generation
# --------------------------------------------------------------------------

SCRIPT_SYSTEM = """You draft interview questions for a podcast host.

Each segment covers one of the host's topics. A good question is specific to this \
guest and grounded in what the research actually found -- not a question you could \
ask anyone in their field.

Set risk_flags where they apply, especially when the guest has already answered \
something repeatedly elsewhere, or where a topic is commercially or legally \
sensitive for them. Cite the claim ids the question rests on."""

SCRIPT_SCHEMA: dict[str, Any] = {
    "title": "generate_script",
    "type": "object",
    "additionalProperties": False,
    "required": ["segments"],
    "properties": {
        "segments": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["topic_id", "question", "rationale"],
                "properties": {
                    "topic_id": {"type": "string"},
                    "question": {"type": "string"},
                    "rationale": {"type": "string"},
                    "expected_direction": {"type": "string"},
                    "followups": {"type": "array", "items": {"type": "string"}},
                    "risk_flags": {"type": "array", "items": {"type": "string"}},
                    "claim_ids": {"type": "array", "items": {"type": "string"}},
                },
            },
        }
    },
}

STYLE_BRIEFS = {
    "formal": "Measured and precise. Full questions, no slang.",
    "conversational": "Warm and plain-spoken. Short questions that invite a story.",
    "contrarian": "Press on tensions and inconsistencies, respectfully but directly.",
    "educational": "Draw out explanations a smart non-expert could follow.",
}


def script_prompt(
    subject: str,
    topics: list[tuple[str, str]],
    dossier_lines: list[str],
    style: str,
    voice_descriptors: dict | None,
    already_covered: list[str],
) -> str:
    lines = [
        f"Guest: {subject}",
        f"Style: {style} -- {STYLE_BRIEFS.get(style, '')}",
    ]
    if voice_descriptors:
        lines.append(f"Match this host's voice: {voice_descriptors}")
    lines += ["", "Topics to cover:"]
    for topic_id, text in topics:
        lines.append(f"[topic:{topic_id}] {text}")

    lines += ["", "Research findings (claim id in brackets):"]
    lines.extend(dossier_lines or ["(no guest-specific findings)"])

    if already_covered:
        lines += ["", "Already covered repeatedly elsewhere -- avoid or approach freshly:"]
        lines.extend(f"- {c}" for c in already_covered)

    lines += ["", "Produce one segment per topic, in the order given."]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# voice descriptors
# --------------------------------------------------------------------------

VOICE_SYSTEM = """You describe a podcast host's interviewing style from a transcript, \
so another model can imitate it without seeing the transcript."""

VOICE_SCHEMA: dict[str, Any] = {
    "title": "voice_descriptors",
    "type": "object",
    "additionalProperties": False,
    "required": ["descriptors"],
    "properties": {
        "descriptors": {
            "type": "object",
            "additionalProperties": True,
            "properties": {
                "pacing": {"type": "string"},
                "register": {"type": "string"},
                "question_length": {"type": "string"},
                "signature_moves": {"type": "array", "items": {"type": "string"}},
            },
        }
    },
}


def voice_prompt(transcript: str) -> str:
    return (
        "Describe this host's interviewing style.\n\n"
        f"<TRANSCRIPT>\n{transcript[:20000]}\n</TRANSCRIPT>"
    )
