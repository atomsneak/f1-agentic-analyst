#!/usr/bin/env bash
# Launchers. The system python has no fastapi, so a local venv holds the deps.
#
#   ./run.sh welcome    -> http://127.0.0.1:8103   weekend picker + session cards
#   ./run.sh            -> http://127.0.0.1:8099   live timing capture (writer)
#   ./run.sh archive    -> http://127.0.0.1:8100   past-race analysis (read-only)
#   ./run.sh standings  -> http://127.0.0.1:8101   championship standings (cached, offline)
#   ./run.sh backfill   -> tools/backfill_openf1.py (add --list, --race-id, --phase)
#   ./run.sh test       -> unit tests + real-DB smoke test
#
# Normally you don't start these by hand: systemd user units keep both surfaces
# up and restart them after a crash (see README.md).
#
# Recreate the venv if it is missing:
#   python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
set -euo pipefail
cd "$(dirname "$0")"
[ -x .venv/bin/python ] || { echo "venv yok: yukarıdaki komutlarla oluştur" >&2; exit 1; }

case "${1:-live}" in
  live)      exec .venv/bin/python server.py ;;
  archive)   exec .venv/bin/python archive.py ;;
  standings) exec .venv/bin/python standings_app.py ;;
  welcome)   exec .venv/bin/python welcome_app.py ;;
  test)
    # Unit tests need no database; the smoke test copies the real one and works
    # on the copy, so running this can never damage the archive.
    .venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v || exit 1
    # Browser-side behaviour (panel, keyboard offset, fetchJSON, column order)
    # then real-browser layout measurement. The second one needs the services
    # running and chrome-headless-shell installed; it skips itself otherwise.
    if command -v node >/dev/null 2>&1; then
      node tests/ui_test.js || exit 1
      node tests/layout_test.js || exit 1
      # PWA: gerçek service worker ile kurulum + çevrimdışı açılış.
      node tests/pwa_test.js || exit 1
      # Hafta sonu yüzeyi: kart açılabilirliği, dürüst sayı sözcükleri, mobil grid.
      node tests/welcome_test.js || exit 1
    else
      echo "node yok: tarayıcı tarafı testler atlandı" >&2
    fi
    exec .venv/bin/python tests/smoke_real_db.py --welcome ;;
  backfill) shift; exec .venv/bin/python tools/backfill_openf1.py "$@" ;;
  *) echo "kullanım: $0 [live|archive|standings|welcome|test|backfill ...]" >&2; exit 2 ;;
esac
