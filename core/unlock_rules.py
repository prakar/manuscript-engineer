"""
core/unlock_rules.py — structural field-unlock enforcement, same logic and
same intent as lib/generate.js's enforceUnlockOrder() in the CLI tool: a
regime's generative discipline (e.g. C7's Limitations-before-Introduction,
C3's falsification-per-dimension) is enforced by blocking progress, not
left as a comment the researcher can skip past.
"""


def check(regime_code, evidence):
    """Returns a list of error strings; empty list means unlocked."""
    errors = []

    if regime_code == "C2":
        if not evidence.get("claim_query", "").strip():
            errors.append(
                "[C2] No claim-query mapping given. Draft the query the Results "
                "sentence will report before drafting the sentence."
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
                    f'[C3] Dimension "{d}" has no falsification sentence '
                    f"(expected a line like '{d}: <what would count against it>')."
                )

    if regime_code == "C5":
        if not evidence.get("criteria", "").strip():
            errors.append(
                "[C5] Inclusion/exclusion criteria not stated. Criteria must be "
                "locked before search."
            )

    if regime_code == "C6":
        if not evidence.get("failure_case", "").strip():
            errors.append(
                "[C6] No failure case described. A features list may not be "
                "drafted before the failure case it addresses."
            )

    if regime_code == "C7":
        if not evidence.get("evidence_base", "").strip():
            errors.append(
                "[C7] Limitations/evidence-base statement is empty. This MUST "
                "be written before Introduction's novelty claim gets drafted."
            )

    return errors
