#!/usr/bin/env bash
set -Eeuo pipefail
[[ $EUID -eq 0 ]] || { echo 'Run with sudo.' >&2; exit 1; }
verifier_base=/opt/ripcars-verifier
[[ -f "$verifier_base/previous-release" ]] || { echo 'No previous release is recorded.' >&2; exit 1; }
verifier_previous="$(cat "$verifier_base/previous-release")"
[[ "$verifier_previous" == "$verifier_base/releases/"* && -f "$verifier_previous/STAGED_OK" ]] || { echo 'Previous release is not a verified candidate.' >&2; exit 1; }
verifier_old="$(readlink -f "$verifier_base/current")"
"$verifier_previous/.venv/bin/python" "$verifier_previous/scripts/preflight.py" --env-file /etc/ripcars-verifier.env
ln -s "$verifier_previous" "$verifier_base/current.rollback"
mv -Tf "$verifier_base/current.rollback" "$verifier_base/current"
printf '%s\n' "$verifier_old" > "$verifier_base/previous-release"
install -m 0644 "$verifier_previous/ripcars-verifier.service" /etc/systemd/system/ripcars-verifier.service
systemctl daemon-reload
systemctl restart ripcars-verifier.service
systemctl --no-pager --full status ripcars-verifier.service
echo 'Code rollback completed. Persistent data and current configuration were preserved.'
