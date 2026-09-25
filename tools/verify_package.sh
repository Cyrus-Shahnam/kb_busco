#!/usr/bin/env python3
"""Real-data known truth at genome scale (lessons §11, adapted to BUSCO).

1. Run the app on a real reference assembly  -> the CONTROL run.
2. Download its zip; take run_<lineage>/full_table.tsv.
3. This script N-masks K randomly chosen single-copy Complete BUSCO loci
   (seeded) and writes a JSON manifest of exactly which IDs were masked.
4. Import the masked FASTA, run the app again with the same lineage.
5. Expect ~K more Missing than the control, and specifically the masked IDs.

N-masking (not excision) keeps the coordinates and contig count unchanged, so
the only thing that differs from the control is the planted loss.

    python3 tools/mask_buscos.py genome.fna full_table.tsv masked.fna --k 25 --seed 1
"""
import argparse
import json
import random


def read_fasta(path):
    seqs, order, name = {}, [], None
    with open(path) as fh:
        for line in fh:
            line = line.rstrip('\n')
            if line.startswith('>'):
                name = line[1:].split()[0]
                order.append(name)
                seqs[name] = []
            elif name:
                seqs[name].append(line.strip())
    return order, {k: ''.join(v) for k, v in seqs.items()}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('fasta')
    ap.add_argument('full_table')
    ap.add_argument('out_fasta')
    ap.add_argument('--k', type=int, default=25)
    ap.add_argument('--seed', type=int, default=1)
    ap.add_argument('--pad', type=int, default=200, help='bp masked beyond each gene end')
    a = ap.parse_args()

    loci = []
    with open(a.full_table) as fh:
        for line in fh:
            if line.startswith('#'):
                continue
            c = line.rstrip('\n').split('\t')
            if len(c) >= 5 and c[1] == 'Complete':
                try:
                    lo, hi = sorted((int(c[3]), int(c[4])))
                except ValueError:
                    continue
                loci.append((c[0], c[2], lo, hi))
    if len(loci) < a.k:
        raise SystemExit('only %d single-copy Complete loci available' % len(loci))
    chosen = sorted(random.Random(a.seed).sample(loci, a.k))

    order, seqs = read_fasta(a.fasta)
    masked_bp = 0
    for bid, contig, lo, hi in chosen:
        s = seqs[contig]
        lo0, hi0 = max(0, lo - 1 - a.pad), min(len(s), hi + a.pad)
        seqs[contig] = s[:lo0] + 'N' * (hi0 - lo0) + s[hi0:]
        masked_bp += hi0 - lo0
    with open(a.out_fasta, 'w') as fh:
        for n in order:
            fh.write('>%s\n' % n)
            for i in range(0, len(seqs[n]), 80):
                fh.write(seqs[n][i:i + 80] + '\n')
    manifest = {'seed': a.seed, 'k': a.k, 'pad': a.pad, 'masked_bp': masked_bp,
                'masked_ids': [c[0] for c in chosen],
                'loci': [dict(zip(('id', 'contig', 'start', 'end'), c)) for c in chosen]}
    with open(a.out_fasta + '.manifest.json', 'w') as fh:
        json.dump(manifest, fh, indent=1)
    print('masked %d loci (%d bp) -> %s' % (a.k, masked_bp, a.out_fasta))


if __name__ == '__main__':
    main()
