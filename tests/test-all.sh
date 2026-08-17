#!/usr/bin/bash
# Run every pad-translation suite. Usage: test-all.sh [-v]
#
# NOTE: run from this directory and do NOT put the mapper's path on your command line -
# the suites sweep orphans with `pkill -f '/pad-keymap\.py'`, which would match your own
# shell if its arguments mention that path.
set -u
cd "$(dirname "$0")"
rc=0
for t in test-pad-keymap.py test-pad-keymap-edge.py test-partydeck-flow.py test-lastmile.py; do
    echo "───────────────────────────────────────────────  $t"
    python3 "./$t" "$@" || rc=1
done
echo "───────────────────────────────────────────────"
[ $rc -eq 0 ] && echo "ALL SUITES PASSED" || echo "SOME SUITES FAILED"
exit $rc
