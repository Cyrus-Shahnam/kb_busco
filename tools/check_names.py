#!/usr/bin/env python3
"""Name-chain check (lessons §2, §15). Run from the module root after every `make`.

Checks kbase.yml / KIDL module / Impl class / Makefile SERVICE vars /
spec.json service-mapping, then for every UI method: funcdef, def, #BEGIN/#END,
display.yaml coverage and input_mapping wiring.
"""
import glob
import json
import os
import re
import sys

import yaml

errors, warns = [], []
k = yaml.safe_load(open('kbase.yml'))['module-name'].strip()
spec = open(k + '.spec').read()
impl = open('lib/%s/%sImpl.py' % (k, k)).read()
mk = open('Makefile').read() if os.path.exists('Makefile') else ''

names = {'kbase.yml': k,
         'KIDL module': (re.search(r'^\s*module\s+(\w+)', spec, re.M) or [None, None])[1],
         'Impl class': (re.search(r'^class\s+(\w+)', impl, re.M) or [None, None])[1]}
for var in ('SERVICE', 'SERVICE_CAPS'):
    m = re.search(r'^%s\s*=\s*(\S+)' % var, mk, re.M)
    names['Makefile ' + var] = m.group(1) if m else None
m = re.search(r'^SPEC_FILE\s*=\s*(\S+)', mk, re.M)
if not m or m.group(1) != k + '.spec':
    errors.append('Makefile SPEC_FILE should be %s.spec (got %s)' % (k, m and m.group(1)))

funcdefs = set(re.findall(r'^\s*funcdef\s+(\w+)', spec, re.M))
ui_methods = set()
for d in sorted(glob.glob('ui/narrative/methods/*/')):
    tag = d.rstrip('/').split('/')[-1]
    sj = json.load(open(os.path.join(d, 'spec.json')))
    smap = sj['behavior']['service-mapping']
    names['spec.json[%s] service-mapping.name' % tag] = smap['name']
    meth = smap['method']
    ui_methods.add(meth)
    if tag != meth:
        warns.append('UI dir %r != method %r (allowed, but confusing)' % (tag, meth))
    if meth not in funcdefs:
        errors.append('%s: method %r has no funcdef in %s.spec' % (tag, meth, k))
    if not re.search(r'^\s*def\s+%s\s*\(' % meth, impl, re.M):
        errors.append('%s: no "def %s(" in Impl' % (tag, meth))
    for marker in ('#BEGIN ' + meth, '#END ' + meth):
        if marker not in impl:
            errors.append('%s: %r missing from Impl' % (tag, marker))
    sjp = {p['id'] for p in sj['parameters']}
    dy = set((yaml.safe_load(open(os.path.join(d, 'display.yaml'))) or {})
             .get('parameters', {}) or {})
    im = {x['input_parameter'] for x in smap['input_mapping'] if 'input_parameter' in x}
    if sjp != dy:
        errors.append('%s: spec.json vs display.yaml params differ: %s' % (tag, sorted(sjp ^ dy)))
    if not sjp <= im:
        errors.append('%s: params not wired into input_mapping: %s' % (tag, sorted(sjp - im)))
    if im - sjp:
        errors.append('%s: input_mapping references unknown params: %s' % (tag, sorted(im - sjp)))

for f in sorted(funcdefs - ui_methods):
    warns.append('funcdef %r has no UI method dir (fine if not a Narrative app)' % f)
if len(set(names.values())) != 1:
    errors.append('module name mismatch: %s' % json.dumps(names, indent=1))

for w in warns:
    print('WARN ', w)
for e in errors:
    print('ERROR', e)
if errors:
    sys.exit(1)
print('all consistent:', k, '| methods:', ', '.join(sorted(ui_methods)))
