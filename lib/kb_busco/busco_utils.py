import collections
import glob
import html
import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import uuid

log = logging.getLogger(__name__)

# Overridable so the runner can be exercised outside the container (§3).
BUSCO_ENV_BIN = os.environ.get('BUSCO_ENV_BIN', '/opt/conda/envs/busco/bin')
BUSCO_EXE = os.path.join(BUSCO_ENV_BIN, 'busco')
# Read-only reference data populated by `entrypoint.sh init` at registration.
REFDATA_ROOT = os.environ.get('BUSCO_REFDATA', '/data/busco_downloads')
EXPECTED_BUSCO_VERSION = '6.1.0'

LINEAGE_RE = re.compile(r'^[a-z][a-z0-9_]*_odb\d+(\.\d+)?$')
LINEAGE_MODES = {
    'specific': None,
    'auto': '--auto-lineage',
    'auto-prok': '--auto-lineage-prok',
    'auto-euk': '--auto-lineage-euk',
}
# miniprot is BUSCO's default for eukaryotic genome mode, so it needs no flag.
EUK_PREDICTORS = {'miniprot': None, 'metaeuk': '--metaeuk', 'augustus': '--augustus'}

DEFAULTS = {
    'lineage_mode': 'specific',
    'lineage_dataset': '',
    'euk_predictor': 'miniprot',
    'contig_break': 10,     # BUSCO default
    'evalue': 1e-3,         # BUSCO default
    'limit': 3,             # BUSCO default
    'skip_bbtools': 0,
}

STATUSES = ('Complete', 'Duplicated', 'Fragmented', 'Missing')
MIN_TOTAL_LENGTH = 100000       # warn: assembly probably too small to assess
LOW_COMPLETENESS_PCT = 50.0     # warn: wrong lineage or very incomplete assembly
HIGH_DUPLICATION_PCT = 10.0     # warn: haplotype duplication / contamination
ONE_LINE_RE = re.compile(
    r'C:(?P<C>[\d.]+)%\[S:(?P<S>[\d.]+)%,D:(?P<D>[\d.]+)%\],'
    r'F:(?P<F>[\d.]+)%,M:(?P<M>[\d.]+)%,n:(?P<n>\d+)(?:,E:(?P<E>[\d.]+)%)?')
SUMMARY_NAME_RE = re.compile(
    r'short_summary\.(?:specific|generic)\.([a-z0-9_]+_odb\d+(?:\.\d+)?)\.')


# --------------------------------------------------------------------------
# Parameter handling
# --------------------------------------------------------------------------
def _blank(v):
    return v is None or (isinstance(v, str) and v.strip() == '')


def coerce_params(params):
    """Validate and normalise app params. Raises ValueError with a readable message.

    The Narrative submits "" for untouched optional fields and sends numerics as
    strings (lessons §7), so blanks fall back to DEFAULTS and numbers are parsed.
    Runs before anything touches the network or the workspace, so the negative
    tests are fast.
    """
    if not isinstance(params, dict):
        raise ValueError('params must be a mapping')

    if _blank(params.get('workspace_name')):
        raise ValueError('workspace_name is required')

    refs = params.get('input_assembly_refs')
    if isinstance(refs, str):
        refs = [refs]
    refs = [r.strip() for r in (refs or []) if not _blank(r)]
    refs = list(collections.OrderedDict.fromkeys(refs))   # de-dupe, keep order
    if not refs:
        raise ValueError('input_assembly_refs is required: select at least one Assembly')

    p = dict(DEFAULTS)
    for key in DEFAULTS:
        v = params.get(key)
        if not _blank(v):
            p[key] = v.strip() if isinstance(v, str) else v
    p['workspace_name'] = params['workspace_name'].strip()
    p['input_assembly_refs'] = refs

    p['lineage_mode'] = str(p['lineage_mode']).lower()
    if p['lineage_mode'] not in LINEAGE_MODES:
        raise ValueError('lineage_mode must be one of %s, got %r'
                         % (sorted(LINEAGE_MODES), p['lineage_mode']))

    p['lineage_dataset'] = str(p['lineage_dataset']).strip().lower()
    if p['lineage_mode'] == 'specific':
        if not p['lineage_dataset']:
            raise ValueError('lineage_dataset is required when lineage_mode is "specific" '
                             '(e.g. bacteria_odb12.2), or choose an auto-lineage mode')
        if not LINEAGE_RE.match(p['lineage_dataset']):
            raise ValueError('lineage_dataset %r does not look like a BUSCO dataset name '
                             '(expected e.g. bacteria_odb12.2; see `busco --list-datasets`)'
                             % p['lineage_dataset'])

    p['euk_predictor'] = str(p['euk_predictor']).lower()
    if p['euk_predictor'] not in EUK_PREDICTORS:
        raise ValueError('euk_predictor must be one of %s, got %r'
                         % (sorted(EUK_PREDICTORS), p['euk_predictor']))

    try:
        p['contig_break'] = int(float(str(p['contig_break'])))
        p['limit'] = int(float(str(p['limit'])))
        p['evalue'] = float(str(p['evalue']))
    except ValueError as e:
        raise ValueError('contig_break, limit and evalue must be numeric: %s' % e)
    if p['contig_break'] < 0:
        raise ValueError('contig_break must be >= 0')
    if p['limit'] < 1:
        raise ValueError('limit must be >= 1')
    if p['evalue'] <= 0:
        raise ValueError('evalue must be > 0')

    p['skip_bbtools'] = 1 if str(p['skip_bbtools']).strip().lower() in ('1', 'true', 'yes') else 0
    return p


# --------------------------------------------------------------------------
# Output parsing (module-level so it can be unit-tested without BUSCO)
# --------------------------------------------------------------------------
def parse_full_table(path):
    """Return {status: set(busco_ids)} counted from full_table.tsv.

    Duplicated BUSCOs have one row per copy, so we count unique IDs.
    """
    ids = {s: set() for s in STATUSES}
    with open(path) as fh:
        for line in fh:
            if not line.strip() or line.startswith('#'):
                continue
            cols = line.rstrip('\n').split('\t')
            if len(cols) >= 2 and cols[1] in ids:
                ids[cols[1]].add(cols[0])
    return ids


def parse_one_line(text):
    m = ONE_LINE_RE.search(text or '')
    if not m:
        return None
    out = {k: float(v) for k, v in m.groupdict().items() if v is not None and k != 'n'}
    out['n'] = int(m.group('n'))
    return out


def _find_key(obj, pred):
    """Depth-first search of nested dicts for the first key matching pred."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if pred(k):
                return v
        for v in obj.values():
            hit = _find_key(v, pred)
            if hit is not None:
                return hit
    return None


def parse_run_dir(run_dir):
    """Parse one BUSCO genome-mode output folder. Returns a dict or None."""
    specific = sorted(glob.glob(os.path.join(run_dir, 'short_summary.specific.*.json')))
    generic = sorted(glob.glob(os.path.join(run_dir, 'short_summary.generic.*.json')))
    summary_json = (specific or generic or [None])[0]
    if summary_json is None:
        return None
    with open(summary_json) as fh:
        data = json.load(fh)

    ds = data.get('lineage_dataset') if isinstance(data.get('lineage_dataset'), dict) else {}
    lineage = ds.get('name')
    if not lineage:
        m = SUMMARY_NAME_RE.search(os.path.basename(summary_json))
        lineage = m.group(1) if m else None

    full_table = os.path.join(run_dir, 'run_%s' % lineage, 'full_table.tsv') if lineage else ''
    if not os.path.isfile(full_table):
        cands = sorted(glob.glob(os.path.join(run_dir, 'run_*', 'full_table.tsv')))
        full_table = cands[0] if cands else ''
    if not full_table:
        return None
    ids = parse_full_table(full_table)
    counts = {s: len(v) for s, v in ids.items()}
    n = sum(counts.values())

    one_line = _find_key(data, lambda k: 'one_line' in k.lower())
    json_pct = parse_one_line(one_line) if isinstance(one_line, str) else None

    def pct(x):
        return round(100.0 * x / n, 1) if n else 0.0

    results = data.get('results') if isinstance(data.get('results'), dict) else {}
    stats_re = re.compile(r'scaffold|contig|total length|gap|n50', re.I)
    assembly_stats = {k: v for k, v in results.items() if stats_re.search(k)}

    return {
        'run_dir': run_dir,
        'summary_json': summary_json,
        'summary_txt': summary_json[:-5] + '.txt',
        'full_table': full_table,
        'summary_kind': 'specific' if specific else 'generic',
        'lineage': lineage,
        'lineage_creation_date': ds.get('creation_date'),
        'dataset_n': ds.get('number_of_buscos'),
        'one_line_summary': one_line if isinstance(one_line, str) else None,
        'json_pct': json_pct,
        'counts': counts,
        'n': n,
        'pct': {
            'C': pct(counts['Complete'] + counts['Duplicated']),
            'S': pct(counts['Complete']),
            'D': pct(counts['Duplicated']),
            'F': pct(counts['Fragmented']),
            'M': pct(counts['Missing']),
        },
        'ids': {s: sorted(v) for s, v in ids.items()},
        'assembly_stats': assembly_stats,
    }


def fasta_stats(path):
    n, total = 0, 0
    with open(path) as fh:
        for line in fh:
            if line.startswith('>'):
                n += 1
            else:
                total += len(line.strip())
    return n, total


def dataset_domain(lineage_dir):
    """Read domain= from dataset.cfg (prokaryota / eukaryota / viruses), if present."""
    cfg = os.path.join(lineage_dir or '', 'dataset.cfg')
    if os.path.isfile(cfg):
        with open(cfg) as fh:
            for line in fh:
                if line.strip().lower().startswith('domain='):
                    return line.split('=', 1)[1].strip().lower()
    return None


def _safe(name, maxlen=50):
    return re.sub(r'[^A-Za-z0-9_.-]', '_', name)[:maxlen] or 'assembly'


# --------------------------------------------------------------------------
# Runner
# --------------------------------------------------------------------------
class BuscoRunner:

    def __init__(self, callback_url, scratch, threads=None):
        self.callback_url = callback_url
        self.scratch = scratch
        self.threads = int(threads or os.environ.get('BUSCO_THREADS')
                           or min(os.cpu_count() or 1, 16))
        self.job_dir = os.path.join(scratch, 'kb_busco_' + uuid.uuid4().hex[:10])
        self.runs_dir = os.path.join(self.job_dir, 'runs')
        self.logs_dir = os.path.join(self.job_dir, 'logs')
        # Shared cache for run-time downloads; persists across runs in one scratch.
        self.dl_dir = os.path.join(scratch, 'busco_downloads')
        for d in (self.job_dir, self.runs_dir, self.logs_dir, self.dl_dir):
            os.makedirs(d, exist_ok=True)
        self.commands = []
        self.warnings = []      # (scope, message)
        self.notes = []         # (scope, message)
        self.results = []       # full per-assembly results incl. ID lists (tests use this)
        self.version_text = ''

    # ---- subprocess -------------------------------------------------------
    @staticmethod
    def busco_env():
        env = dict(os.environ)
        env['PATH'] = BUSCO_ENV_BIN + os.pathsep + env.get('PATH', '')
        return env

    def _run(self, cmd, log_name, env=None):
        """Run cmd, tee output to a log file. Returns (returncode, tail_text)."""
        self.commands.append(shlex.join(cmd))
        log.info('Running: %s', shlex.join(cmd))
        log_path = os.path.join(self.logs_dir, log_name)
        tail = collections.deque(maxlen=60)
        with open(log_path, 'w') as lf:
            proc = subprocess.Popen(cmd, cwd=self.job_dir, env=env or self.busco_env(),
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    universal_newlines=True, bufsize=1)
            for line in proc.stdout:
                lf.write(line)
                tail.append(line.rstrip('\n'))
                log.info(line.rstrip('\n'))
            rc = proc.wait()
        return rc, '\n'.join(tail)

    def busco_version(self):
        rc, out = self._run([BUSCO_EXE, '--version'], 'busco_version.log')
        self.version_text = out.strip()
        if rc != 0:
            raise RuntimeError('Could not run %s --version:\n%s' % (BUSCO_EXE, out))
        if EXPECTED_BUSCO_VERSION not in out:
            self.warnings.append(('all', 'Expected BUSCO %s but found: %s'
                                  % (EXPECTED_BUSCO_VERSION, out.strip())))
        return self.version_text

    # ---- lineage datasets -------------------------------------------------
    def ensure_lineage(self, name):
        """Return (path_to_lineage_dir, source). Prefers read-only /data refdata."""
        local = os.path.join(REFDATA_ROOT, 'lineages', name)
        if os.path.isfile(os.path.join(local, 'dataset.cfg')):
            return local, 'reference data (/data)'
        cached = os.path.join(self.dl_dir, 'lineages', name)
        if os.path.isfile(os.path.join(cached, 'dataset.cfg')):
            return cached, 'scratch cache'
        # --download must be the last argument (user guide).
        rc, tail = self._run([BUSCO_EXE, '--download_path', self.dl_dir, '--download', name],
                             'download_%s.log' % _safe(name))
        if rc != 0 or not os.path.isfile(os.path.join(cached, 'dataset.cfg')):
            raise ValueError(
                'Could not obtain BUSCO lineage dataset %r. Check the exact name with '
                '`busco --list-datasets` (v6.1 names end in _odb12.2).\nLast output:\n%s'
                % (name, tail))
        self.notes.append(('all', 'Lineage %s was not in reference data; downloaded at '
                                  'run time. Add it to data/refdata_lineages.txt to cache it.'
                           % name))
        return cached, 'downloaded at run time'

    def prepare_auto_download_dir(self):
        """Expose read-only /data datasets inside the writable download dir via symlinks.

        Auto-lineage needs a download_path it can write to (it may fetch whatever
        is missing), so we can't point it straight at read-only /data.
        """
        linked = 0
        for sub in ('lineages', 'placement_files'):
            src = os.path.join(REFDATA_ROOT, sub)
            if not os.path.isdir(src):
                continue
            dst_root = os.path.join(self.dl_dir, sub)
            os.makedirs(dst_root, exist_ok=True)
            for entry in os.listdir(src):
                dst = os.path.join(dst_root, entry)
                if not os.path.lexists(dst):
                    os.symlink(os.path.join(src, entry), dst)
                    linked += 1
        fv = os.path.join(REFDATA_ROOT, 'file_versions.tsv')
        if os.path.isfile(fv) and not os.path.exists(os.path.join(self.dl_dir, 'file_versions.tsv')):
            shutil.copy(fv, self.dl_dir)
        self.notes.append(('all', 'Auto-lineage: linked %d reference-data entries; anything '
                                  'else is downloaded at run time.' % linked))

    def augustus_config(self):
        """Augustus writes species params during training, so it needs a writable copy."""
        src = os.environ.get('AUGUSTUS_CONFIG_PATH') or os.path.join(
            os.path.dirname(BUSCO_ENV_BIN), 'config')
        if not os.path.isdir(src):
            self.warnings.append(('all', 'Augustus config dir not found at %s; the Augustus '
                                         'pipeline will probably fail.' % src))
            return None
        dst = os.path.join(self.job_dir, 'augustus_config')
        if not os.path.isdir(dst):
            shutil.copytree(src, dst, symlinks=True)
        return dst

    # ---- inputs -----------------------------------------------------------
    def fetch_assembly(self, ref):
        from installed_clients.AssemblyUtilClient import AssemblyUtil
        au = AssemblyUtil(self.callback_url)
        r = au.get_assembly_as_fasta({'ref': ref})
        path = os.path.abspath(r['path'])   # BUSCO runs with cwd=job_dir
        name = r.get('assembly_name') or ref
        n_seq, total = fasta_stats(path)
        if n_seq == 0 or total == 0:
            raise ValueError('Assembly %s (%s) has no sequence' % (name, ref))
        return path, name, n_seq, total

    # ---- one BUSCO run ----------------------------------------------------
    def run_one(self, idx, ref, p, lineage_path, au_config):
        fasta, name, n_seq, total = self.fetch_assembly(ref)
        fasta = os.path.abspath(fasta)
        scope = name
        if total < MIN_TOTAL_LENGTH:
            self.warnings.append((scope, 'Assembly is only %d bp; BUSCO scores on very small '
                                         'inputs are not meaningful.' % total))
        out_name = 'asm%02d_%s' % (idx + 1, _safe(name))
        cmd = [BUSCO_EXE, '-i', fasta, '-m', 'genome', '-o', out_name,
               '--out_path', self.runs_dir, '-c', str(self.threads), '-f',
               '--opt-out-run-stats', '--download_path', self.dl_dir]
        if p['lineage_mode'] == 'specific':
            # A full path disables BUSCO's automated dataset management (user guide),
            # so read-only /data is safe; --offline stops any network attempt.
            cmd += ['-l', lineage_path, '--offline']
        else:
            cmd.append(LINEAGE_MODES[p['lineage_mode']])
        flag = EUK_PREDICTORS[p['euk_predictor']]
        if flag:
            cmd.append(flag)
        if p['contig_break'] != DEFAULTS['contig_break']:
            cmd += ['--contig_break', str(p['contig_break'])]
        if p['evalue'] != DEFAULTS['evalue']:
            cmd += ['-e', repr(p['evalue'])]
        if p['limit'] != DEFAULTS['limit']:
            cmd += ['--limit', str(p['limit'])]
        if p['skip_bbtools']:
            cmd.append('--skip_bbtools')

        env = self.busco_env()
        if au_config:
            env['AUGUSTUS_CONFIG_PATH'] = au_config
        rc, tail = self._run(cmd, '%s.log' % out_name, env=env)
        run_dir = os.path.join(self.runs_dir, out_name)
        if rc != 0:
            raise RuntimeError('BUSCO failed on %s (%s), exit code %d. Last output:\n%s'
                               % (name, ref, rc, tail))
        res = parse_run_dir(run_dir)
        if res is None:
            raise RuntimeError('BUSCO exited 0 on %s but produced no short_summary / '
                               'full_table.tsv in %s. Last output:\n%s' % (name, run_dir, tail))
        res.update({'ref': ref, 'name': name, 'n_seq': n_seq, 'total_length': total,
                    'out_name': out_name})
        self._check_result(scope, res, p)
        res['zip'] = shutil.make_archive(os.path.join(self.job_dir, 'busco_' + out_name),
                                         'zip', root_dir=self.runs_dir, base_dir=out_name)
        return res

    def _check_result(self, scope, res, p):
        """Output-relative sanity checks (§8: empty-output checks are not enough)."""
        if res['n'] == 0:
            self.warnings.append((scope, 'full_table.tsv contains no BUSCO rows.'))
            return
        if res['dataset_n'] and int(res['dataset_n']) != res['n']:
            self.warnings.append((scope, 'full_table.tsv lists %d BUSCOs but the dataset '
                                         'declares %s.' % (res['n'], res['dataset_n'])))
        jp = res['json_pct']
        if jp and abs(jp['C'] - res['pct']['C']) > 0.15:
            self.warnings.append((scope, 'BUSCO summary says C:%.1f%% but full_table.tsv gives '
                                         '%.1f%%.' % (jp['C'], res['pct']['C'])))
        if jp and jp.get('E'):
            self.notes.append((scope, '%.1f%% of Complete matches contain internal stop codons '
                                      '(Miniprot "E" value); do not use those sequences '
                                      'downstream without checking.' % jp['E']))
        if res['pct']['C'] < LOW_COMPLETENESS_PCT:
            hint = ('check that %s matches this organism' % res['lineage']
                    if p['lineage_mode'] == 'specific' else 'the assembly may be very incomplete')
            self.warnings.append((scope, 'Completeness is %.1f%%: %s.' % (res['pct']['C'], hint)))
        if res['pct']['D'] > HIGH_DUPLICATION_PCT:
            self.warnings.append((scope, 'Duplication is %.1f%%: possible retained haplotypes, '
                                         'contamination, or genuine duplication.'
                                  % res['pct']['D']))
        if p['lineage_mode'] != 'specific':
            if res['summary_kind'] == 'generic':
                self.warnings.append((scope, 'Auto-lineage could not place this assembly; the '
                                             'parent dataset %s is reported.' % res['lineage']))
            else:
                self.notes.append((scope, 'Auto-lineage selected %s.' % res['lineage']))

    # ---- plot + report ----------------------------------------------------
    def make_plot(self, results):
        plot_dir = os.path.join(self.job_dir, 'plot')
        os.makedirs(plot_dir, exist_ok=True)
        for r in results:
            shutil.copy(r['summary_json'], plot_dir)
        try:
            rc, tail = self._run([BUSCO_EXE, '--plot', plot_dir, '--plot_percentages'],
                                 'busco_plot.log')
        except OSError as e:
            rc, tail = 1, str(e)
        pngs = sorted(glob.glob(os.path.join(plot_dir, '*.png')))
        if rc != 0 or not pngs:
            self.warnings.append(('all', 'BUSCO plot could not be generated (non-fatal).'))
            return []
        return pngs

    def write_tsv(self, results):
        path = os.path.join(self.job_dir, 'busco_summary.tsv')
        cols = ['assembly', 'ref', 'lineage', 'lineage_date', 'one_line_summary',
                'complete_single', 'complete_duplicated', 'fragmented', 'missing', 'n',
                'C_pct', 'S_pct', 'D_pct', 'F_pct', 'M_pct', 'n_sequences', 'total_length']
        with open(path, 'w') as fh:
            fh.write('\t'.join(cols) + '\n')
            for r in results:
                c = r['counts']
                row = [r['name'], r['ref'], r['lineage'] or '', r['lineage_creation_date'] or '',
                       r['one_line_summary'] or '', c['Complete'], c['Duplicated'],
                       c['Fragmented'], c['Missing'], r['n'], r['pct']['C'], r['pct']['S'],
                       r['pct']['D'], r['pct']['F'], r['pct']['M'], r['n_seq'],
                       r['total_length']]
                fh.write('\t'.join(str(x) for x in row) + '\n')
        return path

    def write_html(self, p, results, pngs):
        e = html.escape
        html_dir = os.path.join(self.job_dir, 'html')
        os.makedirs(html_dir, exist_ok=True)
        img_tags = []
        for png in pngs:
            shutil.copy(png, html_dir)
            img_tags.append('<img src="%s" style="max-width:100%%">' % e(os.path.basename(png)))

        rows = []
        for i, r in enumerate(results, 1):
            c, pc = r['counts'], r['pct']
            lineage = e(r['lineage'] or '?')
            if r['lineage_creation_date']:
                lineage += '<br><small>%s</small>' % e(str(r['lineage_creation_date']))
            rows.append(
                '<tr><td>%d</td><td>%s</td><td>%s</td><td><code>%s</code></td>'
                '<td>%.1f</td><td>%d</td><td>%d</td><td>%d</td><td>%d</td><td>%d</td>'
                '<td>%d / %s</td></tr>'
                % (i, e(r['name']), lineage,
                   e(r['one_line_summary'] or 'C:%.1f%%' % pc['C']),
                   pc['C'], c['Complete'], c['Duplicated'], c['Fragmented'], c['Missing'],
                   r['n'], r['n_seq'], format(r['total_length'], ',')))

        def items(pairs):
            if not pairs:
                return '<p>None.</p>'
            return '<ul>%s</ul>' % ''.join('<li><b>%s</b>: %s</li>' % (e(s), e(m))
                                           for s, m in pairs)

        stats_blocks = ''.join(
            '<details><summary>%s</summary><pre>%s</pre></details>'
            % (e(r['name']), e(json.dumps(r['assembly_stats'], indent=1)))
            for r in results if r['assembly_stats'])
        params_shown = {k: v for k, v in p.items() if k != 'workspace_name'}

        doc = """<!DOCTYPE html><html><head><meta charset="utf-8"><title>BUSCO</title>
<style>body{font-family:sans-serif;margin:1em}table{border-collapse:collapse}
td,th{border:1px solid #ccc;padding:4px 8px;text-align:right}td:nth-child(2),td:nth-child(3),
td:nth-child(4){text-align:left}.warn{background:#fff4e5;padding:.5em;border-left:4px solid #f0a020}
pre{background:#f6f6f6;padding:.5em;white-space:pre-wrap;word-break:break-all}</style></head><body>
<h2>BUSCO completeness</h2>
<p>%s</p>
<div style="overflow-x:auto"><table>
<tr><th>#</th><th>Assembly</th><th>Lineage (date)</th><th>Summary</th><th>C %%</th><th>S</th>
<th>D</th><th>F</th><th>M</th><th>n</th><th>Seqs / bp</th></tr>
%s</table></div>
<p><small>C = complete (S single-copy + D duplicated), F = fragmented, M = missing,
n = markers searched. Counts are taken from each run's full_table.tsv.</small></p>
%s
<h3>Warnings</h3><div class="warn">%s</div>
<h3>Notes</h3>%s
<h3>Assembly statistics (BBTools, from BUSCO summary)</h3>%s
<h3>Parameters</h3><pre>%s</pre>
<h3>Commands executed</h3><pre>%s</pre>
<h3>Citation</h3><p>Tegenfeldt F. et al. OrthoDB and BUSCO update: annotation of orthologs with
wider sampling of genomes. Nucleic Acids Res. 2025;53(D1):D516-D522.
doi:10.1093/nar/gkae987. BUSCO lineage datasets are CC BY-ND 4.0. When reporting, give the
dataset name <i>and</i> its creation date.</p>
</body></html>""" % (
            e(self.version_text or 'BUSCO'),
            '\n'.join(rows),
            ''.join(img_tags),
            items(self.warnings),
            items(self.notes),
            stats_blocks or '<p>Not available.</p>',
            e(json.dumps(params_shown, indent=1)),
            e('\n\n'.join(self.commands)))
        with open(os.path.join(html_dir, 'index.html'), 'w') as fh:
            fh.write(doc)
        return html_dir

    def make_report(self, p, results, pngs):
        from installed_clients.KBaseReportClient import KBaseReport
        html_dir = self.write_html(p, results, pngs)
        tsv = self.write_tsv(results)
        file_links = [{'path': tsv, 'name': os.path.basename(tsv),
                       'description': 'BUSCO summary table (all assemblies)'}]
        for r in results:
            file_links.append({'path': r['zip'], 'name': os.path.basename(r['zip']),
                               'description': 'Full BUSCO output for %s' % r['name']})
        msg = '\n'.join('%s: %s' % (r['name'], r['one_line_summary'] or 'C:%.1f%%' % r['pct']['C'])
                        for r in results)
        rep = KBaseReport(self.callback_url).create_extended_report({
            'message': msg,
            'objects_created': [],          # QC app: no new data objects
            'direct_html_link_index': 0,
            'html_links': [{'path': html_dir, 'name': 'index.html',
                            'description': 'BUSCO completeness report'}],
            'file_links': file_links,
            'report_object_name': 'kb_busco_report_' + uuid.uuid4().hex,
            'workspace_name': p['workspace_name'],
            'html_window_height': 600,
        })
        return rep['name'], rep['ref']

    # ---- entry point ------------------------------------------------------
    def run(self, params):
        p = coerce_params(params)           # fail fast, before any I/O
        self.busco_version()

        lineage_path = None
        if p['lineage_mode'] == 'specific':
            lineage_path, source = self.ensure_lineage(p['lineage_dataset'])
            self.notes.append(('all', 'Lineage %s from %s.' % (p['lineage_dataset'], source)))
            domain = dataset_domain(lineage_path)
            if domain and domain.startswith('prok') and p['euk_predictor'] != 'miniprot':
                self.warnings.append(('all', 'euk_predictor=%s has no effect with the prokaryotic '
                                             'dataset %s (BUSCO uses Prodigal); flag not passed.'
                                      % (p['euk_predictor'], p['lineage_dataset'])))
                p['euk_predictor'] = 'miniprot'     # i.e. pass no predictor flag
        else:
            self.prepare_auto_download_dir()
            if p['lineage_dataset']:
                self.notes.append(('all', 'lineage_dataset %r ignored in %s mode.'
                                   % (p['lineage_dataset'], p['lineage_mode'])))

        if p['euk_predictor'] != 'augustus' and (p['evalue'] != DEFAULTS['evalue']
                                                 or p['limit'] != DEFAULTS['limit']):
            self.warnings.append(('all', 'evalue/limit only affect BLAST-based pipelines '
                                         '(Augustus, prokaryotic transcriptome); they have no '
                                         'effect here.'))
        if p['skip_bbtools'] and p['contig_break'] != DEFAULTS['contig_break']:
            self.warnings.append(('all', 'contig_break has no effect when BBTools is skipped.'))
        au_config = self.augustus_config() if p['euk_predictor'] == 'augustus' else None

        self.results = [self.run_one(i, ref, p, lineage_path, au_config)
                        for i, ref in enumerate(p['input_assembly_refs'])]
        pngs = self.make_plot(self.results)
        report_name, report_ref = self.make_report(p, self.results, pngs)

        compact = []
        for r in self.results:
            compact.append({
                'ref': r['ref'], 'name': r['name'], 'lineage': r['lineage'],
                'one_line_summary': r['one_line_summary'], 'counts': r['counts'],
                'n': r['n'], 'pct': r['pct'],
                'warnings': [m for s, m in self.warnings if s in (r['name'], 'all')],
            })
        return {'report_name': report_name, 'report_ref': report_ref,
                'busco_results': compact}
