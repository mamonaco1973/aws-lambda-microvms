"""Code snippets offered in the editor.

The demonstration is deliberately dull, and the order matters. "Check state"
runs first on a freshly launched MicroVM and finds `user`, `visits` and `items`
already set, because they were assigned during the image build and live in the
snapshot. "Update state" then mutates them. Suspend, resume, check again and
the mutations are still there; terminate, launch and check again and they are
gone while the build-time values are back. Same cell, three different answers,
which is the whole distinction between image memory and session memory.

"Beat the gateway" and "Install a package" exist to prove the front door. An
API Gateway integration is capped at 30 seconds and cannot be raised; this
project answers from a Lambda Function URL instead, whose ceiling is the
function's own timeout. The first cell simply outlasts 30 seconds. The second
does something useful with the room -- installs software into the running VM,
which then persists like any other session state.

These are conveniences, not a contract. The editor is free-form and the
controller executes whatever it is sent.
"""

PRESETS = {
    "bash": {
        "check": """echo "user   = ${user}"
echo "visits = ${visits}"
echo "items  = ${items[*]}"
echo "file   = $(cat note.txt 2>/dev/null || echo '(none)')"
echo "git    = $(command -v git >/dev/null && git --version || echo '(not installed)')"
""",

        "update": """user=mike
visits=$((visits + 1))
items+=("item${visits}")
echo "updated ${visits} time(s)" > note.txt
echo 'State updated. Check it, or suspend and come back to it.'""",

        # An API Gateway integration would have returned 504 at 30s. This is the
        # single cheapest proof that the Function URL ceiling is real.
        "gateway": """start=${SECONDS}
echo 'Sleeping 45 seconds -- longer than API Gateway would ever allow...'
sleep 45
echo "Still here after $((SECONDS - start))s. A 30s gateway cap would have"
echo 'killed this request; the Function URL did not.'""",

        # The useful version of the same point: real work, and it sticks around
        # because the VM keeps its disk as well as its memory.
        "install": """start=${SECONDS}
dnf install -y -q git >/dev/null 2>&1
echo "dnf install finished in $((SECONDS - start))s"
git --version
echo 'Now run Check state -- git is reported as installed, and stays that way'
echo 'across a suspend and resume.'""",

        "failure": """echo 'A submitted cell failed; the MicroVM is unaffected' >&2
false""",

        "kill": """exit 17""",
    },
}
