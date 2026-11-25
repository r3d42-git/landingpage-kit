#!/usr/bin/env bash
# Runs only against the isolated project created by the CI workflow.
set -euo pipefail
: "${RUNNER_TEMP:?}" "${DASHBOARD_IMAGE:?}" "${COMPOSE_PROJECT_NAME:?}"
work=$(mktemp -d "$RUNNER_TEMP/landingpage.XXXXXX")
configure=(docker compose run --rm --no-deps -T landingpage python tools/configure.py)
admin=(docker compose exec -T landingpage python tools/manage.py)
docker compose config --quiet
examples=examples
if [[ ${DASHBOARD_LANGUAGE:-de} == en ]]; then examples=examples/en; fi
"${configure[@]}" init < "$examples/dashboard.example.yaml"
openssl rand -hex 32 | "${configure[@]}" password --stdin
docker compose up --pull never --wait --wait-timeout 100 -d
curl --fail --silent http://127.0.0.1:8080/healthz | jq -e '.ok'
curl --fail --silent http://127.0.0.1:8080/api/dashboard > "$work/initial.json"
jq -e '.settings.title and (.groups | length > 0) and (.authenticated == false)' "$work/initial.json"
"${configure[@]}" certificate > "$work/ca.pem"
curl --fail --silent --cacert "$work/ca.pem" https://localhost:8443/healthz | jq -e '.ok'
"${admin[@]}" backup > "$work/initial.zip"
unzip -t "$work/initial.zip"
python - "$work/changed.yaml" "$examples/dashboard.example.yaml" <<'PY'
import sys
from pathlib import Path
import yaml
doc = yaml.safe_load(Path(sys.argv[2]).read_text())
doc["settings"]["title"] = "Installationstest"
Path(sys.argv[1]).write_text(yaml.safe_dump(doc, allow_unicode=True))
PY
revision=$(jq -r .revision "$work/initial.json")
docker compose exec -T landingpage python tools/configure.py preview < "$work/changed.yaml"
docker compose exec -T landingpage python tools/configure.py apply --revision "$revision" < "$work/changed.yaml"
"${admin[@]}" show | jq -e '.settings.title == "Installationstest"'
mkdir -p instance/metrics
python examples/write-disk-snapshot.py instance/metrics/disk-usage.json --path /
export COMPOSE_FILE=compose.yaml:deploy/compose.metrics.yaml
docker compose up --pull never --wait --wait-timeout 100 -d
docker compose exec -T landingpage python tools/configure.py sources < "$examples/integrations.example.yaml"
revision=$("${admin[@]}" show | jq -r .revision)
docker compose exec -T landingpage python tools/configure.py apply --revision "$revision" < "$examples/dashboard-monitoring.example.yaml"
measured='.items.dashboard.state == "ok" and (.items.disk.metrics | length > 0) and (.items.disk.progress | type == "number")'
for attempt in {1..40}; do
  curl --fail --silent http://127.0.0.1:8080/api/status > "$work/status.json"
  if jq -e "$measured" "$work/status.json" >/dev/null; then break; fi
  sleep 2
done
jq -e "$measured" "$work/status.json"
curl --fail --silent -H 'Accept-Language: en' http://127.0.0.1:8080/api/status | jq -e '[.items.disk.metrics[].label] | index("Used") != null'
curl --fail --silent -H 'Accept-Language: de' http://127.0.0.1:8080/api/status | jq -e '[.items.disk.metrics[].label] | index("Belegt") != null'
"${admin[@]}" restore --confirm < "$work/initial.zip"
docker compose restart landingpage
docker compose up --pull never --wait --wait-timeout 100 -d
"${admin[@]}" show | jq -e --slurpfile original "$work/initial.json" '.settings == $original[0].settings and .groups == $original[0].groups'
