#!/usr/bin/env bash
# Run a local SonarQube analysis before raising a PR.
#
# One-time setup (already done for this checkout): colima + docker + sonar-scanner
# installed via Homebrew, a `sonarqube` container created, and a scanner token
# generated into .sonar-local/token (gitignored). This script only starts what
# isn't already running, then runs the scan.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

SONAR_URL="http://localhost:9000"
TOKEN_FILE="$REPO_ROOT/.sonar-local/token"

if [[ ! -f "$TOKEN_FILE" ]]; then
  echo "Missing $TOKEN_FILE — run the one-time setup first (see conversation history / README)." >&2
  exit 1
fi

if ! colima status >/dev/null 2>&1; then
  echo "Starting colima..."
  colima start
fi

if ! docker inspect -f '{{.State.Running}}' sonarqube >/dev/null 2>&1; then
  echo "Container 'sonarqube' does not exist — creating it..."
  docker run -d --name sonarqube \
    -p 9000:9000 \
    -v sonarqube_data:/opt/sonarqube/data \
    -v sonarqube_extensions:/opt/sonarqube/extensions \
    -v sonarqube_logs:/opt/sonarqube/logs \
    sonarqube:community
elif [[ "$(docker inspect -f '{{.State.Running}}' sonarqube)" != "true" ]]; then
  echo "Starting existing 'sonarqube' container..."
  docker start sonarqube >/dev/null
fi

echo "Waiting for SonarQube to be UP..."
for _ in $(seq 1 60); do
  resp="$(curl -s "$SONAR_URL/api/system/status" || true)"
  if echo "$resp" | grep -q '"status":"UP"'; then
    break
  fi
  sleep 5
done
if ! echo "$resp" | grep -q '"status":"UP"'; then
  echo "SonarQube did not become ready in time. Last status: $resp" >&2
  exit 1
fi

TOKEN="$(cat "$TOKEN_FILE")"
REPORT_FILE="$REPO_ROOT/.scannerwork/report-task.txt"

sonar-scanner \
  -Dsonar.host.url="$SONAR_URL" \
  -Dsonar.token="$TOKEN" \
  -Dsonar.projectBaseDir="$REPO_ROOT"

# The scanner only submits the report; SonarQube processes it asynchronously.
# Poll the compute-engine task so the quality gate query below reflects this run.
CE_TASK_URL="$(grep '^ceTaskUrl=' "$REPORT_FILE" | cut -d= -f2-)"
echo
echo "Waiting for server-side report processing..."
for _ in $(seq 1 30); do
  task_resp="$(curl -s -u "$TOKEN:" "$CE_TASK_URL")"
  task_status="$(echo "$task_resp" | python3 -c "import json,sys; print(json.load(sys.stdin)['task']['status'])")"
  if [[ "$task_status" = "SUCCESS" || "$task_status" = "FAILED" || "$task_status" = "CANCELED" ]]; then
    break
  fi
  sleep 2
done
echo "Report processing status: $task_status"

echo
echo "Quality gate status:"
curl -s -u "$TOKEN:" "$SONAR_URL/api/qualitygates/project_status?projectKey=optics-framework" \
  | python3 -m json.tool

echo
echo "Dashboard: $SONAR_URL/dashboard?id=optics-framework"
