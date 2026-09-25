#!/bin/bash
# One-time setup for kb_busco. Run from anywhere:   bash setup.sh
# It asks for your details, fills them into the files, generates the KBase
# service clients, compiles, runs every consistency check, and (optionally)
# stores your dev token for `kb-sdk test`. Safe to re-run.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

echo "== kb_busco setup =="
read -rp "KBase username (module owner): " KB_USER
read -rp "Contact email: " KB_EMAIL
read -rp "Docker base image [kbase/sdkpython:3.8.0] (use the tag your last module built with): " BASE
BASE=${BASE:-kbase/sdkpython:3.8.0}
[ -n "$KB_USER" ] && [ -n "$KB_EMAIL" ] || { echo "username and email are required"; exit 1; }

sed -i "s/CHANGE_ME_kbase_username/${KB_USER}/g" kbase.yml ui/narrative/methods/run_busco/spec.json
sed -i "s/CHANGE_ME@your.org/${KB_EMAIL}/g" Dockerfile ui/narrative/methods/run_busco/spec.json
sed -i "s|^FROM .*|FROM ${BASE}|" Dockerfile
echo "-- details written to kbase.yml, spec.json, Dockerfile"

command -v kb-sdk >/dev/null || { echo "kb-sdk is not on PATH; install it first"; exit 1; }

echo "-- generating KBase service clients (lib/installed_clients)"
for c in AssemblyUtil KBaseReport; do
  if [ -s "lib/installed_clients/${c}Client.py" ]; then echo "   $c: present"
  else kb-sdk install "$c"; fi
done

echo "-- compiling"
make
kb-sdk validate
python3 tools/check_names.py

if [ ! -f test_local/test.cfg ]; then
  echo "-- creating test_local/ (kb-sdk test stops after creating it on first run)"
  kb-sdk test || true
fi
if [ -f test_local/test.cfg ]; then
  read -rsp "KBase developer token for tests (hidden; blank to skip): " TOKEN; echo
  if [ -n "$TOKEN" ]; then
    if grep -q '^test_token=' test_local/test.cfg; then
      sed -i "s|^test_token=.*|test_token=${TOKEN}|" test_local/test.cfg
      echo "   token saved to test_local/test.cfg (git-ignored)"
    else
      echo "   no test_token= line found; add your token to test_local/test.cfg by hand"
    fi
  fi
fi

bash tools/verify_package.sh

read -rp "Run the full test suite now with kb-sdk test? First build is slow. [y/N] " GO
if [[ "${GO:-n}" =~ ^[Yy] ]]; then kb-sdk test; else echo "Next: kb-sdk test"; fi
