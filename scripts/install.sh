#!/usr/bin/env bash
set -Eeuo pipefail
verifier_source="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ "${1:-}" == --dry-run ]]; then
  python3 - "$verifier_source" <<'PY'
from pathlib import Path
import sys
p=Path(sys.argv[1])
for name in ('VERSION','main.py','requirements-lock.txt','ripcars-verifier.service','scripts/stage_release.py','run_tests.sh'):
    if not (p/name).is_file(): raise SystemExit('Missing required source file: '+name)
print('Dry run passed. Installation stages a tested release, preserves existing environment and data, and only replaces ripcars-verifier.service and its current symlink.')
PY
  exit 0
fi
[[ $EUID -eq 0 ]] || { echo 'Run this installer with sudo.' >&2; exit 1; }
command -v python3 >/dev/null || { echo 'Install python3 and python3-venv first.' >&2; exit 1; }
python3 -c 'import sys,venv; assert sys.version_info >= (3,11), "Python 3.11+ is required"'
verifier_base=/opt/ripcars-verifier
verifier_candidate="$verifier_base/releases/$(cat "$verifier_source/VERSION")-$(date -u +%Y%m%dT%H%M%SZ)-$RANDOM"
mkdir -p "$verifier_base/releases"
python3 "$verifier_source/scripts/stage_release.py" --source "$verifier_source" --destination "$verifier_candidate"
getent group ripcars-bots >/dev/null || groupadd --system ripcars-bots
id ripcars-verifier >/dev/null 2>&1 || useradd --system --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin --gid ripcars-bots ripcars-verifier
usermod -aG ripcars-bots ripcars-verifier
install -d -m 0700 -o ripcars-verifier -g ripcars-bots /var/lib/ripcars-verifier /var/lib/ripcars-verifier/backups
if [[ ! -d /var/lib/ripcars-bots ]]; then install -d -m 2770 -o root -g ripcars-bots /var/lib/ripcars-bots; fi
[[ "$(stat -c %G /var/lib/ripcars-bots)" == ripcars-bots ]] || { echo 'Shared coordination folder has another group. Review COORDINATION.md; ownership was preserved.' >&2; exit 1; }
chmod g+rws /var/lib/ripcars-bots
for verifier_shared in /var/lib/ripcars-bots/coordination.sqlite3{,-wal,-shm}; do
  if [[ -e "$verifier_shared" ]]; then
    [[ "$(stat -c %G "$verifier_shared")" == ripcars-bots ]] || { echo 'Shared coordination file has another group. Review it before installing.' >&2; exit 1; }
    chmod g+rw "$verifier_shared"
  fi
done
if [[ ! -f /etc/ripcars-verifier.env ]]; then "$verifier_candidate/.venv/bin/python" "$verifier_candidate/scripts/configure_env.py"; fi
"$verifier_candidate/.venv/bin/python" "$verifier_candidate/scripts/preflight.py" --env-file /etc/ripcars-verifier.env
if [[ -L "$verifier_base/current" ]]; then
  "$verifier_candidate/.venv/bin/python" "$verifier_candidate/scripts/create_backup.py" --env-file /etc/ripcars-verifier.env --service-user ripcars-verifier
fi
chown -R root:root "$verifier_candidate"
chmod -R go-w "$verifier_candidate"
install -m 0644 "$verifier_candidate/ripcars-verifier.service" /etc/systemd/system/ripcars-verifier.service
if [[ -L "$verifier_base/current" ]]; then readlink -f "$verifier_base/current" > "$verifier_base/previous-release"; fi
ln -s "$verifier_candidate" "$verifier_base/current.new"
mv -Tf "$verifier_base/current.new" "$verifier_base/current"
systemctl daemon-reload
systemctl enable ripcars-verifier.service
systemctl restart ripcars-verifier.service
systemctl --no-pager --full status ripcars-verifier.service
echo 'Install completed. Configure HTTPS, then finish /verifier panel. New server settings start paused.'
