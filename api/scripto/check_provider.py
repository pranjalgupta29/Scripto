"""Smoke test a configured LLM provider before spending real money on it.

Answers the only question that matters for this pipeline: can this model return
strict JSON with character spans that actually resolve? A model that chats well
but fails this produces a thin dossier that looks like bad research rather than
a bad model, because the span guard drops its claims silently.

Costs a handful of calls. Run it whenever you point at a new provider:

    .venv/bin/python -m scripto.check_provider
"""

from __future__ import annotations

import sys
import time

from scripto.config import settings
from scripto.llm import LLMError, get_llm
from scripto.llm.prompts import (
    DOSSIER_SCHEMA,
    DOSSIER_SYSTEM,
    EXTRACT_SCHEMA,
    EXTRACT_SYSTEM,
    IDENTIFY_SCHEMA,
    IDENTIFY_SYSTEM,
    dossier_prompt,
    extract_prompt,
    identify_prompt,
)
from scripto.pipeline.extract import validate_claim

CHUNK = (
    "Satya Nadella became chief executive of Microsoft in February 2014, "
    "succeeding Steve Ballmer. Before that he led the company's cloud and "
    "enterprise group, where he oversaw the growth of Azure. He has said that "
    "Microsoft's culture needed to shift from a know-it-all to a learn-it-all "
    "mindset. In recent years he has argued that AI capability will become the "
    "defining constraint on business productivity."
)

FAKE_CLAIM_ID = "11111111-1111-1111-1111-111111111111"


def _ok(label: str, detail: str = "") -> None:
    print(f"  \033[32mPASS\033[0m {label}" + (f" — {detail}" if detail else ""))


def _fail(label: str, detail: str = "") -> None:
    print(f"  \033[31mFAIL\033[0m {label}" + (f" — {detail}" if detail else ""))


def _warn(label: str, detail: str = "") -> None:
    print(f"  \033[33mWARN\033[0m {label}" + (f" — {detail}" if detail else ""))


def list_models() -> int:
    """Ask the endpoint what it actually serves, instead of guessing model ids."""
    import httpx

    if settings.llm_provider not in ("openai", "openai_compatible"):
        print("--models only applies to an OpenAI-compatible endpoint.")
        return 1

    url = settings.openai_base_url.rstrip("/") + "/models"
    try:
        response = httpx.get(
            url,
            headers={"Authorization": f"Bearer {settings.openai_api_key}"},
            timeout=30,
        )
        response.raise_for_status()
    except Exception as exc:
        print(f"Could not list models from {url}: {exc}")
        return 1

    ids = sorted(m.get("id", "") for m in response.json().get("data", []))
    print(f"\n{len(ids)} model(s) available at {url}:\n")
    for model_id in ids:
        marks = []
        if model_id.endswith(settings.llm_extract_model):
            marks.append("LLM_EXTRACT_MODEL")
        if model_id.endswith(settings.llm_compose_model):
            marks.append("LLM_COMPOSE_MODEL")
        suffix = f"   <- {', '.join(marks)}" if marks else ""
        print(f"  {model_id}{suffix}")
    print()
    return 0


def main() -> int:
    if "--models" in sys.argv:
        return list_models()

    print(f"\nProvider : {settings.llm_provider}")
    print(f"Extract  : {settings.llm_extract_model}")
    print(f"Compose  : {settings.llm_compose_model}")

    if settings.llm_provider == "fake":
        print(
            "\n\033[33mLLM_PROVIDER=fake — this checks the harness, not a real "
            "model.\033[0m"
        )

    llm = get_llm()
    failures = 0

    # -- 1. structured JSON at all -------------------------------------
    print("\n1. Structured JSON output")
    start = time.monotonic()
    try:
        payload = llm.complete_json(
            role="extract",
            system=EXTRACT_SYSTEM,
            prompt=extract_prompt("Satya Nadella", CHUNK),
            schema=EXTRACT_SCHEMA,
        )
    except LLMError as exc:
        _fail("returned parseable JSON", str(exc)[:120])
        print("\nThis provider cannot be used until JSON output works.\n")
        return 1
    except Exception as exc:
        _fail("call succeeded", f"{type(exc).__name__}: {str(exc)[:120]}")
        print("\nCheck OPENAI_BASE_URL, the API key, and the model id.\n")
        return 1

    elapsed = time.monotonic() - start
    _ok("returned parseable JSON", f"{elapsed:.1f}s")

    claims = payload.get("claims")
    if not isinstance(claims, list):
        _fail("has a `claims` array", f"got {type(claims).__name__}")
        return 1
    _ok("has a `claims` array", f"{len(claims)} claim(s)")

    # -- 2. the span guard ---------------------------------------------
    print("\n2. Verbatim spans (the one that decides dossier quality)")
    if not claims:
        _fail("extracted any claims", "model returned an empty list")
        failures += 1
    else:
        kept = [c for c in claims if validate_claim(c, CHUNK) is not None]
        rate = len(kept) / len(claims)
        detail = f"{len(kept)}/{len(claims)} spans resolve ({rate:.0%})"

        if rate >= 0.8:
            _ok("spans resolve inside the chunk", detail)
        elif rate >= 0.4:
            _warn("spans resolve inside the chunk", detail + " — dossier will be thin")
            failures += 1
        else:
            _fail("spans resolve inside the chunk", detail + " — unusable for extraction")
            failures += 1

        # Show what the model actually produced, so a bad rate is diagnosable.
        for claim in claims[:3]:
            span = claim.get("verbatim_span")
            valid = validate_claim(claim, CHUNK) is not None
            mark = "✓" if valid else "✗"
            quoted = ""
            if isinstance(span, (list, tuple)) and len(span) == 2:
                try:
                    quoted = CHUNK[int(span[0]) : int(span[1])][:52]
                except (TypeError, ValueError):
                    quoted = "<unparseable span>"
            print(f"       {mark} {str(claim.get('text'))[:56]}")
            print(f"         span={span} -> {quoted!r}")

    # -- 3. enum obedience ---------------------------------------------
    print("\n3. Schema obedience")
    valid_kinds = {"biographical", "opinion", "fact", "anecdote", "prediction"}
    kinds = {c.get("kind") for c in claims} if claims else set()
    bad = kinds - valid_kinds
    if bad:
        _warn("used only the allowed `kind` values", f"invalid: {bad}")
        failures += 1
    elif kinds:
        _ok("used only the allowed `kind` values", ", ".join(sorted(kinds)))

    # -- 4. citation discipline ----------------------------------------
    print("\n4. Citation discipline (dossier composer)")
    try:
        composed = llm.complete_json(
            role="compose",
            system=DOSSIER_SYSTEM,
            prompt=dossier_prompt(
                "career_timeline",
                "Satya Nadella",
                [(FAKE_CLAIM_ID, "Became CEO of Microsoft in February 2014.", "2014-02-04")],
            ),
            schema=DOSSIER_SCHEMA,
        )
        items = composed.get("items", [])
        cited = [i for i in items if i.get("claim_ids")]
        if not items:
            _warn("composed any items", "empty response")
        elif len(cited) == len(items):
            _ok("every item carries claim ids", f"{len(items)} item(s)")
        else:
            # Not fatal: the verification pass drops these. But it means waste.
            _warn(
                "every item carries claim ids",
                f"{len(items) - len(cited)}/{len(items)} unsourced, will be dropped",
            )
    except Exception as exc:
        _warn("composer call", f"{type(exc).__name__}: {str(exc)[:90]}")

    # -- 5. identity ranking -------------------------------------------
    print("\n5. Identity ranking")
    try:
        ranked = llm.complete_json(
            role="rank",
            system=IDENTIFY_SYSTEM,
            prompt=identify_prompt(
                "Satya Nadella",
                "Microsoft",
                [
                    {
                        "url": "https://en.wikipedia.org/wiki/Satya_Nadella",
                        "title": "Satya Nadella - Wikipedia",
                        "snippet": "CEO of Microsoft since 2014.",
                    },
                    {
                        "url": "https://example.com/other",
                        "title": "Satya Nadella, cardiologist in Hyderabad",
                        "snippet": "Consultant cardiologist.",
                    },
                ],
            ),
            schema=IDENTIFY_SCHEMA,
        )
        candidates = ranked.get("candidates", [])
        if candidates:
            top = candidates[0]
            _ok(
                "ranked candidates",
                f"top: {top.get('name')} / {top.get('employer')} "
                f"({top.get('confidence')})",
            )
        else:
            _warn("ranked candidates", "returned none")
    except Exception as exc:
        _warn("identify call", f"{type(exc).__name__}: {str(exc)[:90]}")

    # -- verdict --------------------------------------------------------
    print()
    if failures == 0:
        print("\033[32mUsable for the full pipeline.\033[0m\n")
        return 0
    print(
        f"\033[33m{failures} issue(s). Usable for composition, but consider a "
        f"stronger model for LLM_EXTRACT_MODEL.\033[0m\n"
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
