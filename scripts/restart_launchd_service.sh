#!/usr/bin/env bash
set -euo pipefail

LABEL="com.justingeiger.spot-delivery"
launchctl kickstart -k "gui/$(id -u)/${LABEL}"

echo "Restarted ${LABEL}"
