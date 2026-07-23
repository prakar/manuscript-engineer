"""
core/tool_runner.py — invokes the Cookbook's Node/Python tools as real
subprocesses, from the Admin panel, against a directory the researcher
points to (typically the Node CLI's `manuscript-output/` folder — this
Streamlit app's own docx export does not produce a build.js, so there is
nothing of this app's own to lint yet; see Instructions tab).

No Streamlit import here — testable standalone, same rule as the rest of
core/.
"""
import shutil
import subprocess
import os


def node_available():
    return shutil.which("node") is not None


def python_available():
    # We're already running under python3 (this process), so this is
    # really just documenting the check for consistency with node_available()
    # rather than a meaningful runtime question.
    return shutil.which("python3") is not None


def run_structural_lint(tool_source_path, target_dir, timeout=30):
    """
    structural_lint.js reads ./build.js relative to its OWN location
    (__dirname), not the current working directory — so it must be
    copied into target_dir alongside the build.js it's meant to check,
    then run from there. Copying a static reference tool alongside a
    project's build.js is the same pattern the Cookbook itself describes
    ("copy this file into your project").
    """
    if not node_available():
        return False, "", "node is not installed or not on PATH in this environment."

    build_js_path = os.path.join(target_dir, "build.js")
    if not os.path.isfile(build_js_path):
        return False, "", f"No build.js found at {build_js_path}. Point this at a directory containing your generated build.js (e.g. the Node CLI's manuscript-output/)."

    dest = os.path.join(target_dir, "structural_lint.js")
    try:
        shutil.copyfile(tool_source_path, dest)
    except OSError as e:
        return False, "", f"Could not copy structural_lint.js into {target_dir}: {e}"

    try:
        result = subprocess.run(
            ["node", "structural_lint.js"],
            cwd=target_dir, capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, "", f"structural_lint.js did not finish within {timeout}s — aborted."
    except OSError as e:
        return False, "", f"Failed to launch node: {e}"

    return result.returncode == 0, result.stdout, result.stderr


def run_blind_check(target_dir, timeout=60):
    """
    Unlike structural_lint.js, blind_check_template.js is NOT generic —
    it must already be customized (IDENTIFYING_STRINGS, BUILD_COMMAND,
    BLINDED_OUTPUT_PATH filled in) before it means anything. The Node
    CLI's generate.js does this automatically when blind mode is chosen,
    writing a filled-in blind_check_template.js into manuscript-output/.
    This function does NOT copy the raw template — it only runs a copy
    that's already sitting in target_dir, on the assumption it's already
    been customized. If it isn't, this fails loudly rather than silently
    checking nothing.
    """
    if not node_available():
        return False, "", "node is not installed or not on PATH in this environment."

    script_path = os.path.join(target_dir, "blind_check_template.js")
    if not os.path.isfile(script_path):
        return False, "", (
            f"No blind_check_template.js found at {script_path}. This tool must "
            "already be customized for your project (identifying strings, build "
            "command, output path) — it is not run from an unedited template. "
            "The Node CLI's generate.js does this automatically when you answer "
            "'yes' to masked review during classification."
        )

    try:
        result = subprocess.run(
            ["node", "blind_check_template.js"],
            cwd=target_dir, capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, "", f"blind_check_template.js did not finish within {timeout}s — aborted."
    except OSError as e:
        return False, "", f"Failed to launch node: {e}"

    return result.returncode == 0, result.stdout, result.stderr


def run_citation_verification(target_dir, timeout=120):
    """
    citation_verification_template.py must already have its CITATIONS
    list filled in with real DOIs/queries, and requires network access to
    CrossRef plus the `requests` package. Same non-generic caveat as
    blind_check: this runs whatever's already in target_dir, it does not
    fill in a template for you.
    """
    script_path = os.path.join(target_dir, "citation_verification_template.py")
    if not os.path.isfile(script_path):
        return False, "", (
            f"No citation_verification_template.py found at {script_path}. "
            "This must already have its CITATIONS list filled in with your "
            "paper's real references before running."
        )

    try:
        result = subprocess.run(
            ["python3", "citation_verification_template.py"],
            cwd=target_dir, capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, "", f"citation_verification_template.py did not finish within {timeout}s — likely a network stall against CrossRef."
    except OSError as e:
        return False, "", f"Failed to launch python3: {e}"

    return result.returncode == 0, result.stdout, result.stderr
