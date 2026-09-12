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
echo "aws    = $(command -v aws >/dev/null && aws --version 2>&1 | cut -d' ' -f1 || echo '(not installed)')"
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

        # The useful version of the same point: real work, and it sticks
        # around because the VM keeps its disk as well as its memory.
        #
        # dnf's own output is left on stdout and stderr deliberately. A
        # failure here is almost always the network -- no egress connector,
        # or a mirror the VM cannot reach -- and that shows up only in dnf's
        # message. Swallowing it leaves a bare "git: command not found".
        # The worker folds stderr into stdout, so both come back.
        "git": """start=${SECONDS}
dnf install -y git
echo "dnf install finished in $((SECONDS - start))s"
git --version
echo 'Now run Check state -- git is reported as installed, and stays that way'
echo 'across a suspend and resume.'""",

        # The payoff cell: a real AWS client, authenticating with no keys.
        # sts:GetCallerIdentity needs no permission, so it answers even if the
        # role's policy is wrong -- which makes the two calls a useful pair.
        # The first proves an identity arrived; the second proves it is allowed
        # to do something. --region is explicit because nothing guarantees the
        # guest has a region configured.
        "awscli": """start=${SECONDS}
dnf install -y unzip

# A subshell, because the working directory is session state like any other:
# `cd /tmp` out here would break Check state, which reads note.txt relative
# to wherever the shell happens to be.
#
# set -e is safe ONLY in here. In the session shell it would both leak into
# every later cell and kill the session on the first error, taking the state
# this demo exists to show. Nothing below creates state worth keeping, so
# failing fast is free -- and without it a failed download cascades into a
# failed unzip and a failed install, burying the one error that mattered.
(
  set -e
  cd /tmp
  curl -fsSL -o awscliv2.zip     https://awscli.amazonaws.com/awscli-exe-linux-aarch64.zip
  unzip -q -o awscliv2.zip
  ./aws/install --update
  rm -rf /tmp/aws /tmp/awscliv2.zip
)

hash -r                     # bash caches failed lookups of `aws`
echo "installed in $((SECONDS - start))s"
aws --version

echo '--- who am I, with no access key anywhere in this VM?'
aws sts get-caller-identity --region __REGION__

echo '--- read the bucket this page was served from'
aws s3 ls "s3://__WEB_BUCKET__" --region __REGION__""",

        "failure": """echo 'A submitted cell failed; the MicroVM is unaffected' >&2
false""",

        "kill": """exit 17""",

        # Intentionally empty: an unguided cell, for typing into live. Every
        # other preset is a rehearsed answer; this is the one that shows the
        # shell is not a menu.
        "adhoc": "",
    },
}
