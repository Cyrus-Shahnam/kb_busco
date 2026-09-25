#!/usr/bin/env python3
"""Seeded known-truth fixtures for kb_busco (lessons §7, §11).

BUSCO's analogue of Racon's "planted errors" is planted *gene content*:

  control     every marker in the lineage, back-translated into a synthetic
              prokaryotic genome (RBS + ATG..TAA CDS, random intergenic spacers,
              both strands, split over several contigs)
  drop        the same genome with N_DROP known markers REMOVED
  dup         the same genome with N_DUP known markers present TWICE

The tests then assert that BUSCO moves by the planted amount: the removed IDs
become Missing and the duplicated IDs become Duplicated, measured against the
control. The control is what makes this defensible: a consensus marker sequence
does not always score as Complete, so absolute scores are not the claim. The
claim is the delta from the control.

Marker proteins come from the lineage dataset itself (the `ancestral` consensus
file, or refseq_db.faa.gz as a fallback). Nothing large is committed, and the
output is fully determined by the seed plus the dataset version.

Usage (standalone):
    python3 make_synthetic_genome.py /path/to/bacteria_odb12.2 outdir --seed 20260923
"""
import argparse
import gzip
import json
import os
import random

# Two preferred E. coli codons per amino acid; the first is used ~70% of the time.
CODONS = {
    'A': ('GCG', 'GCC'), 'R': ('CGT', 'CGC'), 'N': ('AAC', 'AAT'), 'D': ('GAT', 'GAC'),
    'C': ('TGC', 'TGT'), 'Q': ('CAG', 'CAA'), 'E': ('GAA', 'GAG'), 'G': ('GGC', 'GGT'),
    'H': ('CAT', 'CAC'), 'I': ('ATT', 'ATC'), 'L': ('CTG', 'TTA'), 'K': ('AAA', 'AAG'),
    'M': ('ATG', 'ATG'), 'F': ('TTT', 'TTC'), 'P': ('CCG', 'CCA'), 'S': ('AGC', 'TCT'),
    'T': ('ACC', 'ACG'), 'W': ('TGG', 'TGG'), 'Y': ('TAT', 'TAC'), 'V': ('GTG', 'GTT'),
}
ALIASES = {'B': 'D', 'Z': 'E', 'J': 'L', 'U': 'C', 'O': 'K', 'X': 'A'}
COMP = str.maketrans('ACGT', 'TGCA')


def read_fasta(path):
    opener = gzip.open if path.endswith('.gz') else open
    name, seq = None, []
    with opener(path, 'rt') as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            if line.startswith('>'):
                if name is not None:
                    yield name, ''.join(seq)
                name, seq = line[1:].split()[0], []
            else:
                seq.append(line)
    if name is not None:
        yield name, ''.join(seq)


def load_markers(lineage_dir):
    """Return ({busco_id: protein}, source_file) for markers that have an HMM."""
    hmm_dir = os.path.join(lineage_dir, 'hmms')
    hmm_ids = {f[:-4] for f in os.listdir(hmm_dir) if f.endswith('.hmm')}
    for fname in ('ancestral', 'refseq_db.faa.gz'):
        src = os.path.join(lineage_dir, fname)
        if not os.path.isfile(src):
            continue
        markers = {}
        for name, seq in read_fasta(src):
            bid = name if name in hmm_ids else name.split('_')[0]
            if bid in hmm_ids and bid not in markers and len(seq) >= 30:
                markers[bid] = seq.upper()
        if markers:
            return markers, fname
    raise FileNotFoundError('No usable marker protein file (ancestral / refseq_db.faa.gz) '
                            'in %s' % lineage_dir)


def random_dna(rng, n):
    return ''.join(rng.choice('ACGT') for _ in range(n))


def gene_unit(bid, copy, protein, seed):
    """RBS + CDS for one marker copy. Per-gene RNG, so identical across variants."""
    rng = random.Random('%s:%s:%d' % (seed, bid, copy))
    aas = [ALIASES.get(a, a) for a in protein if a not in '*-.']
    aas = [a for a in aas if a in CODONS]
    if not aas or aas[0] != 'M':
        aas = ['M'] + aas
    cds = ''.join(CODONS[a][0] if rng.random() < 0.7 else CODONS[a][1] for a in aas) + 'TAA'
    unit = 'AGGAGG' + random_dna(rng, 7) + cds
    if rng.random() < 0.5:
        unit = unit.translate(COMP)[::-1]
    return unit


def build_variant(gene_list, markers, seed, label, n_contigs=4):
    """gene_list: list of (busco_id, copy_index). Returns list of contig sequences."""
    rng = random.Random('%s:layout:%s' % (seed, label))
    order = list(gene_list)
    rng.shuffle(order)
    per = max(1, -(-len(order) // n_contigs))          # ceil division
    contigs = []
    for c in range(0, len(order), per):
        parts = [random_dna(rng, rng.randint(150, 400))]
        for bid, copy in order[c:c + per]:
            parts.append(gene_unit(bid, copy, markers[bid], seed))
            parts.append(random_dna(rng, rng.randint(80, 250)))
        contigs.append(''.join(parts))
    return contigs, order


def write_fasta(path, contigs):
    with open(path, 'w') as fh:
        for i, seq in enumerate(contigs, 1):
            fh.write('>contig_%d\n' % i)
            for j in range(0, len(seq), 80):
                fh.write(seq[j:j + 80] + '\n')


def build(lineage_dir, out_dir, n_drop=20, n_dup=10, seed=20260923):
    os.makedirs(out_dir, exist_ok=True)
    markers, source = load_markers(lineage_dir)
    ids = sorted(markers)
    n_drop = min(n_drop, len(ids) // 5)
    n_dup = min(n_dup, len(ids) // 5)
    rng = random.Random('%s:choose' % seed)
    dropped = sorted(rng.sample(ids, n_drop))
    duplicated = sorted(rng.sample(ids, n_dup))
    dropped_set = set(dropped)

    variants = {
        'control': [(b, 0) for b in ids],
        'drop': [(b, 0) for b in ids if b not in dropped_set],
        'dup': [(b, 0) for b in ids] + [(b, 1) for b in duplicated],
    }
    manifest = {
        'seed': seed, 'lineage': os.path.basename(os.path.normpath(lineage_dir)),
        'marker_source': source, 'n_markers': len(ids), 'marker_ids': ids,
        'dropped': dropped, 'duplicated': duplicated, 'variants': {},
    }
    for label, genes in variants.items():
        contigs, order = build_variant(genes, markers, seed, label)
        path = os.path.join(out_dir, 'synthetic_%s.fna' % label)
        write_fasta(path, contigs)
        manifest['variants'][label] = {
            'path': path, 'n_genes': len(order), 'n_contigs': len(contigs),
            'total_length': sum(len(c) for c in contigs),
            'gene_ids': sorted({b for b, _ in order}),
        }
    with open(os.path.join(out_dir, 'manifest.json'), 'w') as fh:
        json.dump(manifest, fh, indent=1)
    return manifest


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('lineage_dir')
    ap.add_argument('out_dir')
    ap.add_argument('--drop', type=int, default=20)
    ap.add_argument('--dup', type=int, default=10)
    ap.add_argument('--seed', type=int, default=20260923)
    a = ap.parse_args()
    m = build(a.lineage_dir, a.out_dir, a.drop, a.dup, a.seed)
    print(json.dumps({k: v for k, v in m.items() if k not in ('marker_ids', 'variants')},
                     indent=1))
    for k, v in m['variants'].items():
        print(k, v['n_genes'], 'genes', v['total_length'], 'bp', v['path'])
