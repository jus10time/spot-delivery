#!/usr/bin/env bash
set -euo pipefail

LABEL="com.justingeiger.spot-delivery"

if launchctl print "gui/$(id -u)/${LABEL}" >/dev/null 2>&1; then
  echo "Service loaded: ${LABEL}"
  launchctl print "gui/$(id -u)/${LABEL}" | sed -n '1,60p'
else
  echo "Service not loaded: ${LABEL}"
fi

echo
echo "HTTP check:"
curl -s -o /dev/null -w 'HTTP %{http_code}\n' http://127.0.0.1:3040/ || true
