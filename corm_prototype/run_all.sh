#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
for stage in \
  contract_space random6_contracts generic_compiler exact_programs scaling fault_fraction \
  local_capacity large64 turnover32 repeated_damage stateful_vm rule110
do
  echo "== $stage =="
  python3 -u run_experiments.py --stage "$stage"
done
python3 aggregate_results.py
pytest -q
