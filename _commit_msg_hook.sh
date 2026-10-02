#!/bin/sh
# commit-msg hook: refuse a commit message that holds a session id found in a
# local session store (this repository is public). The pre-commit hook scans the
# staged change; this one scans the message, which pre-commit never sees.
# git passes the message file as $1. See _check_public_ids.py for what is matched,
# what is not, and why the output never names the id.
#
# NOT VERSION CONTROLLED when installed. Git does not track .git/hooks, so a fresh
# clone has no hook until someone installs it:
#
#   cp _commit_msg_hook.sh .git/hooks/commit-msg && chmod +x .git/hooks/commit-msg
set -e

if [ -x .venv-PowerAtlas/Scripts/python.exe ]; then
    PY=.venv-PowerAtlas/Scripts/python.exe
elif [ -x .venv-PowerAtlas/bin/python ]; then
    PY=.venv-PowerAtlas/bin/python
else
    PY=python
fi

if ! "$PY" _check_public_ids.py --message "$1"; then
    echo "" >&2
    echo "commit-msg: blocked. Replace the id in the message with a synthetic one" >&2
    echo "(see _check_public_ids.py), then commit again." >&2
    exit 1
fi
