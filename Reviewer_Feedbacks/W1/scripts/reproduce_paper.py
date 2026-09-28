#!/usr/bin/env python3
"""Reproduce the three W1 comparisons in the eight-page paper, entirely locally."""
import argparse
import hashlib
import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from paper_metrics import coverage_match, metrics
from validate_inputs import validate

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')


def compare_reference(actual, expected, keys, columns):
    a = actual.set_index(keys).sort_index()
    e = expected.set_index(keys).sort_index()
    if not a.index.equals(e.index):
        raise ValueError('Reference comparison identities differ.')
    np.testing.assert_allclose(a[columns].to_numpy(float),
                               e[columns].to_numpy(float), rtol=1e-12, atol=1e-8)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'results')
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    started = datetime.now(timezone.utc).isoformat()
    manifest = json.loads((ROOT / 'input_manifest.json').read_text())
    for item in manifest['files']:
        if sha(ROOT / item['path']) != item['sha256']:
            raise ValueError('Input checksum mismatch: ' + item['path'])
    protocol = json.loads((ROOT / 'protocol.json').read_text())
    pairs = validate(out)
    rows = []
    rq3_pair = None
    for rq, baseline, b, c in pairs:
        factor = float(c.first_allocation_mib.sum() / b.first_allocation_mib.sum())
        for name, condition, frame, scale in [
            (baseline, 'native', b, 1.0),
            ('CAMP', 'native_and_equal_budget', c, 1.0),
            (baseline, 'scaled_to_CAMP', b, factor),
        ]:
            rows.append({'comparison': rq, 'method': name, 'condition': condition,
                         **metrics(frame, scale)})
        np.testing.assert_allclose(metrics(b, factor)['requested_gib'],
                                   metrics(c)['requested_gib'], rtol=1e-12)
        task = pd.DataFrame({
            'logical_task_id': b.index, 'workflow': b.workflow.to_numpy(),
            'process': b.process.to_numpy(), 'peak_mib': b.actual_peak_mib.to_numpy(),
            'runtime_seconds': b.runtime_seconds.to_numpy(),
            'runtime_missing': b.runtime_seconds.isna().to_numpy(),
            'baseline_native_mib': b.first_allocation_mib.to_numpy(),
            'baseline_scaled_mib': b.first_allocation_mib.to_numpy() * factor,
            'camp_native_mib': c.first_allocation_mib.to_numpy(),
        })
        task.to_csv(out / f'{rq}_paired_requests.tsv.gz', sep='\t', index=False,
                    compression={'method': 'gzip', 'mtime': 0})
        if rq == 'rq3':
            rq3_pair = (b, c)
        print(f'{rq}: equal budget verified; {baseline} {metrics(b, factor)["underallocations"]}, '
              f'CAMP {metrics(c)["underallocations"]} underallocations.', flush=True)

    table = pd.DataFrame(rows)
    reference = pd.read_csv(ROOT / 'inputs/reference_results/native_and_equal_budget.csv')
    compare_reference(table, reference, ['comparison', 'method', 'condition'],
                      list(metrics(pairs[0][2])))
    table.to_csv(out / 'native_and_equal_budget.csv', index=False)

    b, c = rq3_pair
    points = []
    camp_budget = metrics(c)['requested_gib']
    for name, frame in [('CAMP', c), ('Sizey', b)]:
        for condition, m in [
            ('native', metrics(frame)),
            ('equal_CAMP_budget', metrics(frame, camp_budget / metrics(frame)['requested_gib'])),
            ('minimum_95pct_coverage', coverage_match(frame, protocol['coverage_target'])),
        ]:
            points.append({'method': name, 'condition': condition,
                           'requested_gib': m['requested_gib'], 'multiplier': m['factor'],
                           'underallocated_tasks': m['underallocations'],
                           'coverage_pct': m['coverage_pct']})
    points = pd.DataFrame(points)
    compare_reference(points, pd.read_csv(ROOT / 'inputs/reference_results/rq3_operating_points.csv'),
                      ['method', 'condition'],
                      ['requested_gib', 'multiplier', 'underallocated_tasks', 'coverage_pct'])
    points.to_csv(out / 'rq3_operating_points.csv', index=False)

    def row(rq, method):
        return table[(table.comparison == rq) & (table.method == method) &
                     (table.condition != 'native')].iloc[0]

    access, ca, sizey, cs = row('rq2', 'Access'), row('rq2', 'CAMP'), row('rq3', 'Sizey'), row('rq3', 'CAMP')
    matched = points[points.condition == 'minimum_95pct_coverage'].set_index('method')
    mc, ms = matched.loc['CAMP'], matched.loc['Sizey']
    display = {
        'access_factor': f'{access.factor:.3f}',
        'access_budget_gib': f'{ca.requested_gib:,.2f}',
        'access_underallocations': int(access.underallocations),
        'camp_access_underallocations': int(ca.underallocations),
        'camp_unused_gib_hours': f'{ca.positive_unused_gib_hours:.2f}',
        'access_unused_gib_hours': f'{access.positive_unused_gib_hours:.2f}',
        'unused_reduction_pct': f'{100 * (1 - ca.positive_unused_gib_hours / access.positive_unused_gib_hours):.1f}',
        'sizey_factor': f'{sizey.factor:.4f}',
        'sizey_budget_gib': f'{cs.requested_gib:,.2f}',
        'camp_sizey_underallocations': int(cs.underallocations),
        'sizey_underallocations': int(sizey.underallocations),
        'underallocation_reduction_pct': f'{100 * (1 - cs.underallocations / sizey.underallocations):.1f}',
        'coverage_pct': f'{mc.coverage_pct:.3f}',
        'matched_underallocations': int(mc.underallocated_tasks),
        'camp_coverage_budget_gib': f'{mc.requested_gib:,.2f}',
        'sizey_coverage_budget_gib': f'{ms.requested_gib:,.2f}',
        'memory_reduction_pct': f'{100 * (1 - mc.requested_gib / ms.requested_gib):.1f}',
    }
    expected = json.loads((ROOT / 'inputs/paper_claims.json').read_text())['expected_display']
    if display != expected or mc.underallocated_tasks != ms.underallocated_tasks:
        raise ValueError('Regenerated values do not match the manuscript: ' + repr(display))
    write_json(out / 'paper_values.json', display)
    lines = [
        '# W1 paper results — reproduced from original frozen predictions', '',
        '| Comparison | Reported result |', '|---|---|',
        f'| Access, equal budget | {display["access_budget_gib"]} GiB each; Access 2 versus CAMP 31 underallocations. |',
        f'| Access, unused memory-time | CAMP {display["camp_unused_gib_hours"]} versus Access {display["access_unused_gib_hours"]} GiB-hours; {display["unused_reduction_pct"]}% less. |',
        f'| Sizey, equal budget | {display["sizey_budget_gib"]} GiB each; CAMP 287 versus Sizey 1,119 underallocations; {display["underallocation_reduction_pct"]}% fewer. |',
        f'| Sizey, matched coverage | {display["coverage_pct"]}% and 1,066 underallocations each; CAMP {display["camp_coverage_budget_gib"]} versus Sizey {display["sizey_coverage_budget_gib"]} GiB; {display["memory_reduction_pct"]}% less. |', '',
        'All original-result checks, pre-organization numerical comparisons, and manuscript rounding checks passed.', '',
        'RQ2 retains all 5,026 tasks and the original zero weighting for 449 missing runtimes in memory-time only. RQ3 retains 21,337 tasks with positive runtimes.',
        'Coverage matching is retrospective on recorded peaks; models and histories are fixed. These are aggregate request sums, not concurrent RAM. No training or workload execution occurs.', '',
    ]
    (out / 'paper_results.md').write_text('\n'.join(lines))
    write_json(out / 'completion.json', {
        'status': 'completed', 'started_utc': started,
        'completed_utc': datetime.now(timezone.utc).isoformat(),
        'python': sys.version, 'platform': platform.platform(),
        'numpy': np.__version__, 'pandas': pd.__version__,
        'input_checksums_verified': len(manifest['files']),
        'original_result_validation': 'passed', 'pre_organization_results_match': True,
        'manuscript_values_match': True, 'training_rerun': False,
        'bootstrap_performed': False, 'plots_generated': False,
        'analysis_sha256': sha(Path(__file__)), 'protocol_sha256': sha(ROOT / 'protocol.json'),
    })
    output_files = sorted(p for p in out.iterdir() if p.is_file() and p.name != 'OUTPUT_SHA256SUMS.txt')
    (out / 'OUTPUT_SHA256SUMS.txt').write_text(''.join(f'{sha(p)}  {p.name}\n' for p in output_files))
    print('Paper values and historical references match. No bootstrap or plots generated.', flush=True)


if __name__ == '__main__':
    main()
