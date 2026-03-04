#!/usr/bin/env bash
set -euo pipefail

"$(dirname "$0")/stop_local.sh"
"$(dirname "$0")/start_local.sh"
