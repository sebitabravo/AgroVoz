#!/usr/bin/env bash
# Runner raíz para que el QA del repositorio ejecute backend y landing.
set -euo pipefail

make test
bun --cwd landing test
