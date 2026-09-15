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

EXTRACT_SYSTEM = """You extract atomic factual claims about a specific subject -- a \
person, or a topic being researched -- from a passage of text.

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

SCRIPT_SYSTEM = """You write a complete interview run-of-show for a podcast host: \
an opening, one block per topic, spoken transitions between blocks, optional backup \
topics, and a closing.

Rules:
- The timing plan is fixed by the caller. Write content that fits the minutes and \
the number of questions given for each block. Do not invent timings.
- A transition is a short line the host can say out loud to move from the previous \
block into this one. Build it on where the guest will likely have gone in the \
previous block (its expected direction), so the conversation flows instead of \
jumping. Transitions must not state facts about the guest.
- Questions must be specific to this guest and grounded in the research, not \
questions you could ask anyone in their field. Cite the claim ids each block rests on.
- The opening has a hook, a short guest introduction built only from cited \
research, and a warm-up first question.
- The closing has a transition, a final question, and a one-line wrap-up.
- Set risk_flags where the guest has already answered something repeatedly \
elsewhere, or where a topic is commercially or legally sensitive for them."""

_ID_LIST: dict[str, Any] = {"type": "array", "items": {"type": "string"}}
_TEXT_LIST: dict[str, Any] = {"type": "array", "items": {"type": "string"}}

SCRIPT_SCHEMA: dict[str, Any] = {
    "title": "generate_script",
    "type": "object",
    "additionalProperties": False,
    "required": ["opening", "blocks", "closing"],
    "properties": {
        "opening": {
            "type": "object",
            "additionalProperties": False,
            "required": ["hook", "guest_intro", "first_question", "claim_ids"],
            "properties": {
                "hook": {"type": "string"},
                "guest_intro": {"type": "string"},
                "first_question": {"type": "string"},
                "claim_ids": _ID_LIST,
            },
        },
        "blocks": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "topic_id",
                    "title",
                    "transition_in",
                    "lead_question",
                    "deeper_questions",
                    "claim_ids",
                ],
                "properties": {
                    "topic_id": {"type": "string"},
                    "title": {"type": "string"},
                    "transition_in": {"type": "string"},
                    "lead_question": {"type": "string"},
                    "deeper_questions": _TEXT_LIST,
                    "followups": _TEXT_LIST,
                    "rationale": {"type": "string"},
                    "expected_direction": {"type": "string"},
                    "risk_flags": _TEXT_LIST,
                    "claim_ids": _ID_LIST,
                },
            },
        },
        "bonus": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["title", "why", "lead_question", "claim_ids"],
                "properties": {
                    "title": {"type": "string"},
                    "why": {"type": "string"},
                    "lead_question": {"type": "string"},
                    "followups": _TEXT_LIST,
                    "claim_ids": _ID_LIST,
                },
            },
        },
        "closing": {
            "type": "object",
            "additionalProperties": False,
            "required": ["transition_in", "final_question", "wrap_up"],
            "properties": {
                "transition_in": {"type": "string"},
                "final_question": {"type": "string"},
                "wrap_up": {"type": "string"},
                "claim_ids": _ID_LIST,
            },
        },
    },
}

STYLE_BRIEFS = {
    "formal": "Measured and precise. Full questions, no slang.",
    "conversational": "Warm and plain-spoken. Short questions that invite a story.",
    "contrarian": "Press on tensions and inconsistencies, respectfully but directly.",
    "educational": "Draw out explanations a smart non-expert could follow.",
}


def script_prompt(
    *,
    episode_title: str,
    subject: str,
    headline: str | None,
    plan: dict,
    topics: list[tuple[str, str]],
    dossier_lines: list[str],
    style: str,
    voice_descriptors: dict | None,
    already_covered: list[str],
    optimize_order: bool,
    include_bonus: bool,
    previous_version: list[str] | None = None,
    feedback: str | None = None,
) -> str:
    lines = [
        f"Episode title: {episode_title}",
        f"Guest: {subject}" + (f" -- {headline}" if headline else ""),
        f"Style: {style} -- {STYLE_BRIEFS.get(style, '')}",
    ]
    if voice_descriptors:
        lines.append(f"Match this host's voice: {voice_descriptors}")

    lines += [
        f"Total duration: {plan['total']} minutes",
        "",
        "Timing plan (fixed):",
        f"- Opening | {plan['opening']} min",
    ]
    for (topic_id, text), minutes, count in zip(
        topics, plan["topic_minutes"], plan["questions"]
    ):
        lines.append(f"- [topic:{topic_id}] {text} | {minutes} min | {count} questions")
    lines.append(f"- Closing | {plan['closing']} min")

    lines.append("")
    if optimize_order:
        lines.append(
            "Order: you may reorder the topic blocks to give the conversation the best "
            "arc (for example warm-up, then depth, then forward-looking). Every topic "
            "must appear exactly once, and each keeps its own minutes and question count."
        )
    else:
        lines.append("Order: keep the topic blocks in exactly the order given.")
    lines.append(
        "Each block's question count includes its lead question; put the rest in "
        "deeper_questions."
    )

    lines += ["", "Research findings (claim id in brackets):"]
    lines.extend(dossier_lines or ["(no guest-specific findings)"])

    if already_covered:
        lines += ["", "Already covered repeatedly elsewhere -- avoid, or find a fresh angle:"]
        lines.extend(f"- {c}" for c in already_covered)

    if previous_version:
        lines += ["", "Current version of this run-of-show:"]
        lines.extend(previous_version)
        if feedback:
            lines += [
                "",
                f"The host's feedback on the current version: {feedback}",
                "Revise the current version to address this feedback. Keep what the "
                "feedback does not ask to change, including blocks the host edited.",
            ]
        else:
            lines += [
                "",
                "Write a fresh alternative to the current version. Blocks marked as "
                "edited by the host will be kept as they are.",
            ]

    if include_bonus:
        lines += [
            "",
            "Also give 2 or 3 backup topics that are not in the plan, for when a block "
            "runs short or falls flat. Ground them in the research where you can.",
        ]
    else:
        lines += ["", "Do not include backup topics."]
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


# --------------------------------------------------------------------------
# topic suggestions
# --------------------------------------------------------------------------

SUGGEST_SYSTEM = """You suggest interview topics for a podcast episode. The episode \
title sets the theme; the research findings say what this guest can speak to.

Rules:
- Prefer topics grounded in the research. Each grounded topic must cite the claim \
ids it rests on.
- You may also suggest topics the title implies that the research does not cover. \
Give those an empty claim_ids list; the host will be told they are unresearched.
- Avoid what the guest has already covered repeatedly elsewhere, unless you offer a \
genuinely fresh angle and say so in `why`.
- Do not repeat the host's existing topics.
- Phrase each topic as a short, specific label a host could put on a run-of-show, \
not as a question."""

SUGGEST_SCHEMA: dict[str, Any] = {
    "title": "suggest_topics",
    "type": "object",
    "additionalProperties": False,
    "required": ["suggestions"],
    "properties": {
        "suggestions": {
            "type": "array",
            "maxItems": 10,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["text", "why", "claim_ids"],
                "properties": {
                    "text": {"type": "string"},
                    "why": {"type": "string"},
                    "claim_ids": {"type": "array", "items": {"type": "string"}},
                },
            },
        }
    },
}


def suggest_prompt(
    *,
    episode_title: str,
    subject: str,
    headline: str | None,
    existing_topics: list[str],
    dossier_lines: list[str],
    already_covered: list[str],
) -> str:
    lines = [
        f"Episode title: {episode_title}",
        f"Guest: {subject}" + (f" -- {headline}" if headline else ""),
        "",
        "Host's existing topics:",
    ]
    lines.extend(f"- {t}" for t in existing_topics)
    if not existing_topics:
        lines.append("(none yet)")

    lines += ["", "Research findings (claim id in brackets):"]
    lines.extend(dossier_lines or ["(no research findings yet)"])

    if already_covered:
        lines += ["", "Already covered repeatedly elsewhere:"]
        lines.extend(f"- {c}" for c in already_covered)

    lines += ["", "Suggest 6 to 10 topics for this episode."]
    return "\n".join(lines)
