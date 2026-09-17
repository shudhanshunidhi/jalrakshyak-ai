"""
Step 4: LLM explanation layer.

Takes the deterministic (severity, reasons) output from scoring.py and asks
an LLM to phrase it as one clear sentence for the commander. The LLM never
sees raw report data beyond the reasons list, and never influences the
score — it's a explanation-writer, not a decision-maker.

Set ANTHROPIC_API_KEY as an environment variable before running.
If it's not set, falls back to a simple template so the demo never breaks
on a flaky/missing connection.
"""

import os

FALLBACK_TEMPLATE = "{severity}: {reasons}."


def explain_severity(severity: str, reasons: list[str]) -> str:
    reasons_text = ", ".join(reasons) if reasons else "no major risk factors reported"

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return FALLBACK_TEMPLATE.format(severity=severity, reasons=reasons_text)

    try:
        import anthropic

        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=100,
            messages=[{
                "role": "user",
                "content": (
                    f"Severity level: {severity}. "
                    f"Contributing factors: {reasons_text}. "
                    "Write ONE short, plain-language sentence a disaster "
                    "commander can read in under 2 seconds explaining why "
                    "this report got this severity level. No preamble, just "
                    "the sentence."
                ),
            }],
        )
        return msg.content[0].text.strip()
    except Exception:
        # Never let an LLM/network hiccup break triage during a live demo
        return FALLBACK_TEMPLATE.format(severity=severity, reasons=reasons_text)
