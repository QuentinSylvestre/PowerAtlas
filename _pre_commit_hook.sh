#!/bin/sh
# Refuse a commit that would introduce a duplicate definition in a test module.
#
# See _check_test_names.py for what this catches and why a conftest.py would
# not. Roughly 270 ms over the whole tests/ tree, so it runs unconditionally
# rather than trying to be clever about when it is worth it.
#
# Checks the **staged** content, not the working tree. That distinction is the
# reason for the temp directory below: checking the working tree would block a
# legitimate commit over a duplicate the author has not staged, and would miss
# one that is staged while the working copy has since been fixed. `git show
# :path` reads the index, which is what is actually about to be committed.
#
# A duplicate is always intra-file, so checking only the staged files is
# complete -- there is no cross-file case this misses by not scanning the rest.
#
# NOT VERSION CONTROLLED. Git does not track .git/hooks, so this file does not
# travel with a clone and a fresh checkout has no hook until someone reinstalls
# it. Reinstallation instructions are in AGENTS.md, next to the checker.
set -e

files=$(git diff --cached --name-only --diff-filter=ACMR -- 'tests/*.py')
[ -z "$files" ] && exit 0

if [ -x .venv-PowerAtlas/Scripts/python.exe ]; then
    PY=.venv-PowerAtlas/Scripts/python.exe
elif [ -x .venv-PowerAtlas/bin/python ]; then
    PY=.venv-PowerAtlas/bin/python
else
    PY=python
fi

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

for f in $files; do
    mkdir -p "$tmp/$(dirname "$f")"
    git show ":$f" > "$tmp/$f"
done

if ! "$PY" _check_test_names.py "$tmp/tests"; then
    echo "" >&2
    echo "pre-commit: blocked. Paths above are a staging snapshot; fix the" >&2
    echo "duplicate in tests/ itself, then re-stage." >&2
    exit 1
fi
