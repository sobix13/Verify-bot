#!/usr/bin/env bash
set -Eeuo pipefail
[[ $EUID -eq 0 ]] || { echo 'Run with sudo after pointing the verifier DNS name to this VPS.' >&2; exit 1; }
verifier_domain="${1:?Usage: sudo bash scripts/configure_https.sh verify.example.com admin@example.com}"
verifier_email="${2:?Supply the certificate contact email.}"
verifier_source="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
command -v nginx >/dev/null || { echo 'Install nginx first.' >&2; exit 1; }
command -v certbot >/dev/null || { echo 'Install certbot and python3-certbot-nginx first.' >&2; exit 1; }
verifier_config=/etc/nginx/sites-available/ripcars-verifier
[[ ! -e "$verifier_config" && ! -e /etc/nginx/sites-enabled/ripcars-verifier ]] || { echo 'Verifier nginx configuration already exists. Review it manually; it was not overwritten.' >&2; exit 1; }
python3 - "$verifier_source" "$verifier_domain" "$verifier_config" <<'PY'
import re,sys
from pathlib import Path
source,domain,target=sys.argv[1:]
sys.path.insert(0,source)
from scripts.preflight import read_env
env=read_env('/etc/ripcars-verifier.env')
if not re.fullmatch(r'(?=.{1,253}$)[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+',domain): raise SystemExit('Invalid public DNS name.')
if env['PUBLIC_URL'].rstrip('/')!='https://'+domain: raise SystemExit('Domain must match PUBLIC_URL in the environment file.')
port=int(env.get('WEB_PORT','8092'))
if env.get('WEB_HOST','127.0.0.1')!='127.0.0.1': raise SystemExit('This nginx template requires WEB_HOST=127.0.0.1.')
text=(Path(source)/'scripts/nginx-verifier.conf.template').read_text().replace('@@DOMAIN@@',domain).replace('@@PORT@@',str(port))
with open(target,'x') as file: file.write(text)
PY
ln -s "$verifier_config" /etc/nginx/sites-enabled/ripcars-verifier
if ! nginx -t; then
  rm -f /etc/nginx/sites-enabled/ripcars-verifier "$verifier_config"
  echo 'nginx validation failed. The new verifier file was removed; existing virtual hosts were preserved.' >&2
  exit 1
fi
systemctl reload nginx
certbot --nginx --non-interactive --agree-tos --redirect -m "$verifier_email" -d "$verifier_domain"
nginx -t
systemctl reload nginx
echo 'HTTPS configured. Run /verifier probe to check the public connection site.'
