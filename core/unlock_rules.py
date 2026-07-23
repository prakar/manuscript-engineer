"""
core/unlock_rules.py — structural field-unlock enforcement, same logic and
same intent as lib/generate.js's enforceUnlockOrder() in the CLI tool: a
regime's generative discipline (e.g. C7's Limitations-before-Introduction,
C3's falsification-per-dimension) is enforced by blocking progress, not
left as a comment the researcher can skip past.

Message text below is user-facing (rendered directly in Step 2's error
list) and deliberately avoids the internal regime codes (C2, C3, etc.) —
those are meaningful to us, not to a researcher using the app.
"""


def check(regime_code, evidence):
    """Returns a list of error strings; empty list means unlocked."""
    errors = []

    if regime_code == "C2":
        if not evidence.get("claim_query", "").strip():
            errors.append(
                "Describe the specific query or tally that supports your "
                "claim, before you write the sentence that reports it."
            )

    if regime_code == "C3":
        dims = [d.strip() for d in evidence.get("dimensions", "").split(",") if d.strip()]
        fals_text = evidence.get("falsification", "")
        fals_lines = [l.strip() for l in fals_text.splitlines() if l.strip()]
        covered = set()
        for line in fals_lines:
            if ":" in line:
                covered.add(line.split(":", 1)[0].strip())
        for d in dims:
            if d not in covered:
                errors.append(
                    f'"{d}" needs a sentence describing what result would '
                    f"count against it (format: '{d}: <what would count against it>')."
                )

    if regime_code == "C5":
        if not evidence.get("criteria", "").strip():
            errors.append(
                "State your inclusion/exclusion criteria before you search "
                "the literature — not after."
            )

    if regime_code == "C6":
        if not evidence.get("failure_case", "").strip():
            errors.append(
                "Describe the specific problem your tool solves before "
                "listing its features."
            )

    if regime_code == "C7":
        if not evidence.get("evidence_base", "").strip():
            errors.append(
                "State your evidence base honestly (e.g. how many cases you've "
                "actually tested this on) before writing your introduction."
            )

    return errors