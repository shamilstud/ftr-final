#!/usr/bin/env bash
# Runs the whole matrix in sequence (about 1 hour). Single experiments: python scripts/run_exp.py <1..8>.
cd "$(dirname "$0")"
export PYTHONIOENCODING=utf-8
for n in 1 2 3 4 5 6 7 8; do python scripts/run_exp.py "$n"; done
python scripts/build_summary.py
