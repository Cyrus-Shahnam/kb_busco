# -*- coding: utf-8 -*-
"""kb_busco tests.

Fast tier (no BUSCO run):  parameter validation, blank or stringified Narrative
params, output parsers.
Known-truth tier (3 small BUSCO runs, a few minutes): a synthetic genome built
from the lineage's own markers, with planted deletions and duplications,
measured against a control (see test/data/make_synthetic_genome.py).

"Ran without error" is not the assertion. Moving by the planted amount is.
"""
import json
import os
import sys
import tempfile
import time
import unittest

import requests
from configparser import ConfigParser

from kb_busco.kb_buscoImpl import kb_busco
from kb_busco.kb_buscoServer import MethodContext
from kb_busco.authclient import KBaseAuth as _KBaseAuth
from kb_busco.busco_utils import (BuscoRunner, DEFAULTS, coerce_params, parse_full_table,
                                   parse_one_line)
from installed_clients.AssemblyUtilClient import AssemblyUtil

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data'))
import make_synthetic_genome as synth  # noqa: E402

TEST_LINEAGE = os.environ.get('BUSCO_TEST_LINEAGE', 'bacteria_odb12.2')
SEED = 20260923
N_DROP = 20
N_DUP = 10
# Loose thresholds so a BUSCO/dataset bump doesn't make the suite flaky (§7).
MIN_CONTROL_COMPLETE_FRAC = 0.5
MIN_DROP_RECALL = 0.8
MIN_DUP_RECALL = 0.6


class kb_buscoTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        token = os.environ.get('KB_AUTH_TOKEN', None)
        config_file = os.environ.get('KB_DEPLOYMENT_CONFIG', None)
        cls.cfg = {}
        config = ConfigParser()
        config.read(config_file)
        for nameval in config.items('kb_busco'):
            cls.cfg[nameval[0]] = nameval[1]
        auth_client = _KBaseAuth(cls.cfg['auth-service-url'])
        user_id = auth_client.get_user(token)
        cls.ctx = MethodContext(None)
        cls.ctx.update({'token': token,
                        'user_id': user_id,
                        'provenance': [{'service': 'kb_busco',
                                        'method': 'please_never_use_it_in_production',
                                        'method_params': []}],
                        'authenticated': 1})
        cls.wsURL = cls.cfg['workspace-url']
        cls.token = token
        cls.serviceImpl = kb_busco(cls.cfg)
        cls.scratch = cls.cfg['scratch']
        cls.callback_url = os.environ['SDK_CALLBACK_URL']
        cls.wsName = 'test_kb_busco_' + str(int(time.time() * 1000))
        cls.ws_call('create_workspace', {'workspace': cls.wsName})
        cls._known_truth = None     # built lazily; fast tests don't pay for it

    @classmethod
    def ws_call(cls, method, arg):
        """Plain JSON-RPC to the Workspace (no generated WorkspaceClient needed)."""
        r = requests.post(cls.wsURL, headers={'Authorization': cls.token}, json={
            'version': '1.1', 'id': '1', 'method': 'Workspace.' + method, 'params': [arg]})
        body = r.json()
        if 'error' in body:
            raise RuntimeError('Workspace.%s failed: %s' % (method, body['error']))
        return body['result']

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, 'wsName'):
            cls.ws_call('delete_workspace', {'workspace': cls.wsName})
            print('Test workspace was deleted')

    # ------------------------------------------------------------------ fast
    def test_missing_assemblies_raises(self):
        with self.assertRaisesRegex(ValueError, 'input_assembly_refs'):
            self.serviceImpl.run_busco(self.ctx, {'workspace_name': self.wsName,
                                                  'lineage_dataset': TEST_LINEAGE})

    def test_blank_assembly_list_raises(self):
        with self.assertRaisesRegex(ValueError, 'input_assembly_refs'):
            self.serviceImpl.run_busco(self.ctx, {'workspace_name': self.wsName,
                                                  'input_assembly_refs': [''],
                                                  'lineage_dataset': TEST_LINEAGE})

    def test_missing_workspace_raises(self):
        with self.assertRaisesRegex(ValueError, 'workspace_name'):
            self.serviceImpl.run_busco(self.ctx, {'input_assembly_refs': ['1/2/3']})

    def test_specific_mode_needs_dataset(self):
        with self.assertRaisesRegex(ValueError, 'lineage_dataset is required'):
            self.serviceImpl.run_busco(self.ctx, {'workspace_name': self.wsName,
                                                  'input_assembly_refs': ['1/2/3'],
                                                  'lineage_mode': 'specific',
                                                  'lineage_dataset': ''})

    def test_bad_dataset_name_raises(self):
        with self.assertRaisesRegex(ValueError, 'does not look like'):
            coerce_params({'workspace_name': 'w', 'input_assembly_refs': ['1/2/3'],
                           'lineage_dataset': 'E. coli'})

    def test_bad_mode_raises(self):
        with self.assertRaisesRegex(ValueError, 'lineage_mode'):
            coerce_params({'workspace_name': 'w', 'input_assembly_refs': ['1/2/3'],
                           'lineage_mode': 'guess'})

    def test_blank_optionals_fall_back_to_defaults(self):
        p = coerce_params({'workspace_name': 'w', 'input_assembly_refs': '1/2/3',
                           'lineage_mode': '', 'lineage_dataset': 'Bacteria_odb12.2',
                           'euk_predictor': '', 'contig_break': '', 'evalue': '',
                           'limit': '', 'skip_bbtools': ''})
        self.assertEqual(p['input_assembly_refs'], ['1/2/3'])
        self.assertEqual(p['lineage_mode'], 'specific')
        self.assertEqual(p['lineage_dataset'], 'bacteria_odb12.2')
        for k in ('euk_predictor', 'contig_break', 'evalue', 'limit', 'skip_bbtools'):
            self.assertEqual(p[k], DEFAULTS[k], k)

    def test_stringified_numerics_are_coerced(self):
        p = coerce_params({'workspace_name': 'w', 'input_assembly_refs': ['a', 'a', 'b'],
                           'lineage_dataset': 'bacteria_odb12.2', 'contig_break': '25',
                           'evalue': '1e-5', 'limit': '4.0', 'skip_bbtools': '1'})
        self.assertEqual(p['input_assembly_refs'], ['a', 'b'])      # de-duplicated
        self.assertEqual((p['contig_break'], p['evalue'], p['limit'], p['skip_bbtools']),
                         (25, 1e-5, 4, 1))

    def test_parsers(self):
        pc = parse_one_line('C:97.9%[S:49.4%,D:48.5%],F:0.2%,M:1.9%,n:1990,E:8.6%')
        self.assertEqual(pc['n'], 1990)
        self.assertAlmostEqual(pc['E'], 8.6)
        with tempfile.NamedTemporaryFile('w', suffix='.tsv', delete=False) as fh:
            fh.write('# BUSCO version is: 6.1.0\n# Busco id\tStatus\tSequence\n'
                     'a1\tComplete\tc1\nb2\tDuplicated\tc1\nb2\tDuplicated\tc2\n'
                     'c3\tFragmented\tc3\nd4\tMissing\n')
        ids = parse_full_table(fh.name)
        self.assertEqual({k: len(v) for k, v in ids.items()},
                         {'Complete': 1, 'Duplicated': 1, 'Fragmented': 1, 'Missing': 1})

    # ------------------------------------------------------- known truth
    def known_truth(self):
        """Build the fixtures, run BUSCO once on all three (batch path), cache it."""
        cls = type(self)
        if cls._known_truth is not None:
            return cls._known_truth
        runner = BuscoRunner(self.callback_url, self.scratch)
        lineage_dir, source = runner.ensure_lineage(TEST_LINEAGE)
        print('test lineage %s from %s' % (lineage_dir, source))
        # Fixtures go to scratch: AssemblyUtil mounts scratch, not test/ (§7).
        fixture_dir = os.path.join(self.scratch, 'fixtures_%d' % SEED)
        manifest = synth.build(lineage_dir, fixture_dir, N_DROP, N_DUP, SEED)

        au = AssemblyUtil(self.callback_url)
        refs = {}
        for label in ('control', 'drop', 'dup'):
            refs[label] = au.save_assembly_from_fasta({
                'file': {'path': manifest['variants'][label]['path']},
                'workspace_name': self.wsName,
                'assembly_name': 'synthetic_%s' % label})
        out = runner.run({'workspace_name': self.wsName,
                          'input_assembly_refs': [refs['control'], refs['drop'], refs['dup']],
                          'lineage_mode': 'specific', 'lineage_dataset': TEST_LINEAGE})
        by_label = dict(zip(('control', 'drop', 'dup'), runner.results))
        for label, r in by_label.items():
            print('%-8s %s  counts=%s' % (label, r['one_line_summary'], r['counts']))
        cls._known_truth = (manifest, by_label, out)
        return cls._known_truth

    def test_kt0_fixture_sanity(self):
        m, _, _ = self.known_truth()
        v = m['variants']
        self.assertEqual(v['control']['n_genes'], m['n_markers'])
        self.assertEqual(v['drop']['n_genes'], m['n_markers'] - len(m['dropped']))
        self.assertEqual(v['dup']['n_genes'], m['n_markers'] + len(m['duplicated']))
        self.assertFalse(set(m['dropped']) & set(v['drop']['gene_ids']))
        for label, info in v.items():
            n_seq, total = 0, 0
            with open(info['path']) as fh:
                for line in fh:
                    n_seq += line.startswith('>')
                    total += 0 if line.startswith('>') else len(line.strip())
            self.assertEqual((n_seq, total), (info['n_contigs'], info['total_length']), label)

    def test_kt1_control_is_the_floor(self):
        m, r, out = self.known_truth()
        ctrl = r['control']
        self.assertEqual(ctrl['n'], m['n_markers'],
                         'full_table must list every marker in the dataset')
        complete = ctrl['counts']['Complete'] + ctrl['counts']['Duplicated']
        self.assertGreaterEqual(complete / ctrl['n'], MIN_CONTROL_COMPLETE_FRAC,
                                'control recovered too few markers: %s' % ctrl['counts'])
        self.assertTrue(out['report_ref'])
        self.assertEqual(len(out['busco_results']), 3)

    def test_kt2_planted_deletions_become_missing(self):
        m, r, _ = self.known_truth()
        ctrl_found = set(r['control']['ids']['Complete']) | set(r['control']['ids']['Duplicated'])
        detectable = [b for b in m['dropped'] if b in ctrl_found]
        if len(detectable) < 5:
            self.skipTest('only %d dropped markers were detectable in the control'
                          % len(detectable))
        now_missing = set(r['drop']['ids']['Missing'])
        recall = sum(b in now_missing for b in detectable) / len(detectable)
        print('deletions: %d detectable, recall %.2f' % (len(detectable), recall))
        self.assertGreaterEqual(recall, MIN_DROP_RECALL)
        c_ctrl = len(ctrl_found)
        c_drop = r['drop']['counts']['Complete'] + r['drop']['counts']['Duplicated']
        self.assertGreaterEqual(c_ctrl - c_drop, MIN_DROP_RECALL * len(detectable))

    def test_kt3_planted_duplications_become_duplicated(self):
        m, r, _ = self.known_truth()
        ctrl_single = set(r['control']['ids']['Complete'])
        detectable = [b for b in m['duplicated'] if b in ctrl_single]
        if len(detectable) < 3:
            self.skipTest('only %d duplicated markers were single-copy in the control'
                          % len(detectable))
        now_dup = set(r['dup']['ids']['Duplicated'])
        recall = sum(b in now_dup for b in detectable) / len(detectable)
        print('duplications: %d detectable, recall %.2f' % (len(detectable), recall))
        self.assertGreaterEqual(recall, MIN_DUP_RECALL)
