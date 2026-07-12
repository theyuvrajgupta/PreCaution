"""Guards for user-visible copy that a demo-based test can never catch.

The testing principle behind this file (Build_Spec: Prose Segmentation and UI
Rework): any value hardcoded to the demo's value is invisible to demo-based
tests, because a hardcode and a correct binding are identical whenever the input
is the demo. The left-panel caption was literally hardcoded to "piranha" and
survived ~70 demo runs for exactly this reason. There is no JS test runner in
this repo, so these assert against the static source directly — enough to fail
loudly if either fix is reverted.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parent.parent / "app" / "static"


def test_bench_caption_is_generic_not_hardcoded_to_the_demo():
    """Pass B1: the structural-read caption must be protocol-independent. It prints on
    every protocol, so it can never name the demo's mixture ("piranha")."""
    src = (STATIC / "thread.js").read_text(encoding="utf-8")
    assert "Claude resolved this protocol into its reagents" in src
    # The old hardcode must not come back. (The word "piranha" still appears in this
    # file's comments; the caption's own copy is what must never carry it again.)
    assert 'Claude resolved "piranha"' not in src


def test_post_run_empty_state_states_the_finding_not_a_protocol_judgment():
    """Pass B6: after a run with no chemicals, the copy must state what preCaution
    knows (it found none), never assert the text "isn't a protocol," which it cannot
    judge."""
    src = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "No chemicals were confidently identified in this text." in src
    assert "this doesn't look like one" not in src


# Pass B4: sanitizeName is JS, and this repo has no JS test runner, so exercise the
# REAL shipped function via node (skipped when node isn't installed, same graceful-skip
# pattern the network/live tests use). Confirmed live: multi-part reagents were leaking
# Stage-1 scratch-work like "Phenol (25 parts (of 25:24:1 mixture))" into the panel.
_SANITIZE_NODE_SCRIPT = """
import {{ sanitizeName, cap }} from {module!r};
const cases = [
  ['phenol (25 parts (of 25:24:1 mixture))', 'Phenol'],
  ['ethanol (cold (temperature qualifier, not concentration))', 'Ethanol'],
  ['sodium acetate (3 M)', 'Sodium acetate (3 M)'],
  ['sodium azide (0.02%)', 'Sodium azide (0.02%)'],
  ['iron(III) chloride', 'Iron(III) chloride'],
];
for (const [input, want] of cases) {{
  const got = cap(sanitizeName(input));
  if (got !== want) {{
    console.error(`FAIL ${{JSON.stringify(input)}} -> ${{JSON.stringify(got)}} (want ${{JSON.stringify(want)}})`);
    process.exit(1);
  }}
}}
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_sanitize_name_flattens_scratchwork_but_keeps_real_concentrations():
    # node's ESM loader needs a file:// URL, not a bare Windows path (d:\...).
    module_url = (STATIC / "render.js").resolve().as_uri()
    script = _SANITIZE_NODE_SCRIPT.format(module=module_url)
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
