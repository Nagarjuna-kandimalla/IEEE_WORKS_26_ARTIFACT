#!/usr/bin/env bash
set -euo pipefail
package_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
python3.9 -m venv "$package_root/.venv"
"$package_root/.venv/bin/python" -m pip install -r "$package_root/requirements.txt"
