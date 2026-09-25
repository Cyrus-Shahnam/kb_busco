#!/bin/bash
# Copy-drift / zero-byte / truncation / parse checks (lessons §13).
# Run from the module root: after copying files in, after `make`, and before registering.
M=kb_busco
fail=0
say() { printf '%-6s %s\n' "$1" "$2"; }

need=(kbase.yml $M.spec Dockerfile .dockerignore env-busco.yml Makefile deploy.cfg setup.sh
      scripts/entrypoint.sh scripts/prepare_deploy_cfg.py scripts/run_async.sh
      scripts/download_busco_data.sh data/refdata_lineages.txt
      lib/$M/${M}Impl.py lib/$M/busco_utils.py
      ui/narrative/methods/run_busco/spec.json ui/narrative/methods/run_busco/display.yaml
      test/${M}_server_test.py test/data/make_synthetic_genome.py
      lib/installed_clients/AssemblyUtilClient.py lib/installed_clients/KBaseReportClient.py)
for f in "${need[@]}"; do
  [ -s "$f" ] && say ok "$f" || { say FAIL "missing or EMPTY: $f"; fail=1; }
done
[ -f test_local/test.cfg ] || say WARN "test_local/test.cfg not created yet (setup.sh / first kb-sdk test does it)"
grep -l 'CHANGE_ME' kbase.yml Dockerfile ui/narrative/methods/run_busco/spec.json 2>/dev/null \
  | sed 's/^/WARN   placeholder not filled in (run setup.sh): /'

# Truncated pastes: compare against checksums shipped with the package.
if [ -f tools/MANIFEST.md5 ]; then
  bad=$(md5sum -c tools/MANIFEST.md5 2>/dev/null | grep -v ': OK$')
  [ -z "$bad" ] && say ok "all shipped files match tools/MANIFEST.md5" \
    || { echo "$bad"; say WARN "files differ from shipped checksums (fine only if you edited them deliberately)"; }
fi

say info "funcdefs: $(grep -c '^[[:space:]]*funcdef' $M.spec)"
empties=$(find . -name '*.py' -empty -not -path './test_local/*' -not -name '__init__.py')
[ -z "$empties" ] && say ok "no zero-byte .py files" || { echo "$empties"; say FAIL "zero-byte .py files"; fail=1; }

for f in $(find lib/$M test tools -name '*.py'); do
  python3 -c "import ast,sys;ast.parse(open(sys.argv[1]).read())" "$f" 2>/dev/null \
    || { say FAIL "python parse error: $f"; fail=1; }
done
for f in scripts/*.sh tools/*.sh setup.sh; do bash -n "$f" || { say FAIL "bash syntax: $f"; fail=1; }; done
python3 - <<'PY' || fail=1
import json, yaml
json.load(open('ui/narrative/methods/run_busco/spec.json'))
yaml.safe_load(open('ui/narrative/methods/run_busco/display.yaml'))
yaml.safe_load(open('kbase.yml'))
yaml.safe_load(open('env-busco.yml'))
print('ok     json/yaml parse')
PY

grep -q 'BuscoRunner' lib/$M/${M}Impl.py && say ok "Impl body present (survived make)" \
  || { say FAIL "BuscoRunner not referenced in Impl: did make clobber it?"; fail=1; }
grep -q -- '--java' Makefile 2>/dev/null && { say FAIL "Makefile has --java flags (Python-only module; remove them)"; fail=1; }
[ -d ui/narrative/methods/run_$M ] && { say FAIL "leftover kb-sdk example UI dir ui/narrative/methods/run_$M: delete it"; fail=1; }
grep -q '^  token *= *$\|^token *= *$' test_local/test.cfg 2>/dev/null && say WARN "test_local/test.cfg token is blank"

python3 tools/check_names.py || fail=1
[ $fail -eq 0 ] && echo "=== PACKAGE OK" || { echo "=== PACKAGE HAS PROBLEMS"; exit 1; }
