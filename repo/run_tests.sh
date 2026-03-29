#!/bin/bash
set -euo pipefail

# Unified test execution script with stable temp/cache paths
mkdir -p .pytest_tmp .pytest_runtime/cache

run_id="run_$(date +%s)_$$"
base_tmp=".pytest_runtime/tmp/${run_id}"
mkdir -p "$base_tmp"

python -m pytest unit_tests API_tests --basetemp "$base_tmp" "$@"
