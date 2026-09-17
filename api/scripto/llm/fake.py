"""Deterministic fake provider.

Lets the entire pipeline, the API and the eval harness run with no API keys and
no network. It is not a mock that returns `{}` — it produces structurally valid,
deterministic output for each task so that the real guards downstream (verbatim
span resolution, unsourced-sentence verification, clustering) are genuinely
exercised in tests.

Dispatch is on `schema["title"]`, which every prompt builder sets.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from scripto.llm.base import LLMProvider, Role

_SENTENCE = re.compile(r"[^.!?]+[.!?]")


def _stable_pick(seed: str, options: list[str]) -> str:
    digest = hashlib.sha256(seed.encode()).digest()
    return options[digest[0] % len(options)]


class FakeProvider(LLMProvider):
    @property
    def version(self) -> str:
        return "fake:v1"

    def complete_text(
        self, *, role: Role, system: str, prompt: str, max_tokens: int | None = None
    ) -> str:
        return f"[fake:{role}] {prompt[:120]}"

    def complete_json(
        self,
        *,
        role: Role,
        system: str,
        prompt: str,
        schema: dict[str, Any],
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        task = schema.get("title", "")
        handler = getattr(self, f"_task_{task}", None)
        if handler is None:
            return {}
        return handler(prompt)

    # -- identify -------------------------------------------------------
    def _task_identify_candidates(self, prompt: str) -> dict[str, Any]:
        name = _extract_field(prompt, "Name") or "Unknown Person"
        employer = _extract_field(prompt, "Disambiguator") or "Unknown Firm"
        return {
            "candidates": [
                {
                    "name": name,
                    "headline": f"Analyst at {employer}",
                    "employer": employer,
                    "photo_url": None,
                    "evidence_urls": ["https://example.com/profile"],
                    "confidence": 0.91,
                    "reasoning": "Name and employer both match the disambiguator.",
                },
                {
                    "name": name,
                    "headline": "Unrelated person with the same name",
                    "employer": "Some Other Co",
                    "photo_url": None,
                    "evidence_urls": ["https://example.com/other"],
                    "confidence": 0.22,
                    "reasoning": "Name matches but no connection to the disambiguator.",
                },
            ]
        }

    # -- claim extraction -----------------------------------------------
    def _task_extract_claims(self, prompt: str) -> dict[str, Any]:
        """Emit one claim per sentence of the chunk, with real spans.

        Spans are computed against the actual chunk text, so they resolve --
        which is what makes downstream span validation meaningful rather than
        vacuous. One deliberately invalid span is emitted for long chunks so
        tests can prove the guard drops it.
        """
        chunk = _extract_block(prompt, "CHUNK")
        if not chunk:
            return {"claims": []}

        claims: list[dict[str, Any]] = []
        for match in _SENTENCE.finditer(chunk):
            text = match.group().strip()
            if len(text) < 25:
                continue
            kind = _stable_pick(text, ["biographical", "opinion", "fact", "anecdote", "prediction"])
            claims.append(
                {
                    "text": text,
                    "kind": kind,
                    "claim_date": None,
                    "quote": text,
                }
            )
            if len(claims) >= 6:
                break

        # A fabricated claim with an unresolvable span. The extractor must drop
        # this in code, not in the prompt.
        if len(chunk) > 400:
            claims.append(
                {
                    "text": "This assertion appears nowhere in the source text.",
                    "kind": "fact",
                    "claim_date": None,
                    "quote": "a sentence that is nowhere in the chunk at all",
                }
            )
        return {"claims": claims}

    # -- clustering -----------------------------------------------------
    def _task_confirm_cluster(self, prompt: str) -> dict[str, Any]:
        members = _extract_block(prompt, "CLAIMS") or ""
        first = next((ln.strip("- ").strip() for ln in members.splitlines() if ln.strip()), "")
        return {
            "same_claim": True,
            "canonical_text": first or "Repeated claim",
        }

    # -- dossier --------------------------------------------------------
    def _task_compose_dossier_section(self, prompt: str) -> dict[str, Any]:
        # Echo the claim's own wording rather than inventing filler, so the eval
        # harness measures the real retrieval path instead of the fake's prose.
        pairs = re.findall(r"^\[([0-9a-f-]{36})\](?:\s*\([^)]*\))?\s*(.+)$", prompt, re.MULTILINE)
        items = [
            {"text": text.strip(), "claim_ids": [cid]} for cid, text in pairs[:8] if text.strip()
        ]
        claim_ids = [cid for cid, _ in pairs]
        if claim_ids:
            # An unsourced sentence. The verification pass must drop or flag it.
            items.append({"text": "An assertion with no supporting claim.", "claim_ids": []})
        return {"items": items}

    # -- script ---------------------------------------------------------
    def _task_generate_script(self, prompt: str) -> dict[str, Any]:
        """A complete run-of-show that follows the caller's timing plan."""
        plan = re.findall(
            r"^- \[topic:([0-9a-f-]{36})\] (.+?) \| (\d+) min \| (\d+) questions$",
            prompt,
            re.MULTILINE,
        )
        claim_ids = re.findall(r"^\[([0-9a-f-]{36})\]", prompt, re.MULTILINE)

        blocks = []
        previous = "your background"
        for i, (topic_id, text, _minutes, count) in enumerate(plan):
            topic = text.strip()
            blocks.append(
                {
                    "topic_id": topic_id,
                    "title": topic,
                    "transition_in": f"You mentioned {previous}; that leads us to {topic}.",
                    "lead_question": f"What is your current thinking on {topic}?",
                    "deeper_questions": [
                        f"Deeper question {n + 1} on {topic}." for n in range(int(count) - 1)
                    ],
                    "followups": [
                        "If they cite market conditions, ask how that changed since last year."
                    ],
                    "rationale": "Opens the topic without repeating prior coverage.",
                    "expected_direction": f"Likely to reference their recent work on {topic}.",
                    "risk_flags": [],
                    "claim_ids": claim_ids[i : i + 1],
                }
            )
            previous = topic

        out: dict[str, Any] = {
            "opening": {
                "hook": "A cold open built from the guest's own words.",
                "guest_intro": "Our guest today leads research at their firm.",
                "first_question": "How did you get started?",
                "claim_ids": claim_ids[:1],
            },
            "blocks": blocks,
            "closing": {
                "transition_in": f"Before we wrap up on {previous},",
                "final_question": "What should listeners watch for next?",
                "wrap_up": "Thanks for joining us.",
                "claim_ids": [],
            },
        }
        if "backup topics that are not in the plan" in prompt:
            out["bonus"] = [
                {
                    "title": "Backup: first role",
                    "why": "Grounded fallback if a block runs short.",
                    "lead_question": "What did your first role teach you?",
                    "followups": [],
                    "claim_ids": claim_ids[:1],
                },
                {
                    "title": "Backup: industry outlook",
                    "why": "Title-driven fallback.",
                    "lead_question": "Where is the industry heading?",
                    "followups": [],
                    "claim_ids": [],
                },
            ]
        return out

    def _task_suggest_topics(self, prompt: str) -> dict[str, Any]:
        """Grounded suggestions from the research, plus one from the title alone."""
        pairs = re.findall(
            r"^\[([0-9a-f-]{36})\](?:\s*\([^)]*\))?\s*(.+)$", prompt, re.MULTILINE
        )
        title = _extract_field(prompt, "Episode title") or "this episode"
        suggestions = [
            {
                "text": f"The story behind: {text.strip()[:60]}",
                "why": "Grounded in the research.",
                "claim_ids": [claim_id],
            }
            for claim_id, text in pairs[:4]
        ]
        suggestions.append(
            {
                "text": f"Where {title} goes next",
                "why": "Implied by the episode title; not researched.",
                "claim_ids": [],
            }
        )
        return {"suggestions": suggestions}

    def _task_check_identity(self, prompt: str) -> dict[str, Any]:
        """Mismatch when the page names an employer other than the subject's.

        A crude proxy for what a real model weighs, but deterministic, and it
        catches the case that caused this gate: a same-name journalist's page
        attributed to an engineer.
        """
        employer = (_extract_field(prompt, "Employer") or "").strip()
        # Only the page itself. The prompt's own "Role: Analyst at X" line would
        # otherwise match the subject's employer every time.
        opening = prompt.split("Page opening:", 1)[-1]
        named = re.findall(r"\bat ([A-Z][\w&.\-' ]{2,40})", opening)
        if not employer or not named:
            return {"same_person": True, "why": "nothing contradicts the subject"}

        matches = any(
            employer.lower() in found.lower() or found.lower() in employer.lower()
            for found in named
        )
        return {
            "same_person": matches,
            "why": (
                f"the page names {named[0].strip()}, not {employer}"
                if not matches
                else f"the page names {employer}"
            ),
        }

    def _task_suggest_prep_questions(self, prompt: str) -> dict[str, Any]:
        """Questions grounded in the research, plus one that suits the format."""
        pairs = re.findall(
            r"^\[([0-9a-f-]{36})\](?:\s*\([^)]*\))?\s*(.+)$", prompt, re.MULTILINE
        )
        questions = [
            {
                "text": f"Can you tell us more about: {text.strip()[:60]}?",
                "why": "Grounded in the research.",
                "claim_ids": [claim_id],
            }
            for claim_id, text in pairs[:3]
        ]
        questions.append(
            {
                "text": "What are you most excited about right now?",
                "why": "Suits the format; not researched.",
                "claim_ids": [],
            }
        )
        return {"questions": questions}

    def _task_voice_descriptors(self, prompt: str) -> dict[str, Any]:
        return {
            "descriptors": {
                "pacing": "measured",
                "register": "conversational but precise",
                "question_length": "short",
                "signature_moves": ["opens with a concrete anecdote", "asks for numbers"],
            }
        }


def _extract_field(prompt: str, label: str) -> str | None:
    match = re.search(rf"^{re.escape(label)}:\s*(.+)$", prompt, re.MULTILINE)
    return match.group(1).strip() if match else None


def _extract_block(prompt: str, label: str) -> str | None:
    match = re.search(rf"<{label}>\n(.*?)\n</{label}>", prompt, re.DOTALL)
    return match.group(1) if match else None
