"""Compare regenerated W2 tables and the paper's primary-budget results."""
from pathlib import Path
import json
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
for name in ['per_repetition_metrics.tsv', 'paired_summary.tsv']:
    actual = pd.read_csv(ROOT / 'results/downstream_allocation' / name, sep='\t')
    expected = pd.read_csv(ROOT / 'expected_results' / name, sep='\t')
    pd.testing.assert_frame_equal(actual, expected, check_exact=False, rtol=1e-12, atol=1e-10)
d = pd.read_csv(ROOT / 'results/downstream_allocation/per_repetition_metrics.tsv', sep='\t')
primary = d[d.budget.eq(.2) & d.evaluation.eq('after_first_update') & d.operating_point.eq('native')]
assert primary.groupby('method').size().to_dict() == {'random': 5, 'selective': 5}
means = primary.groupby('method')[['underallocations','requested_gib']].mean()
selective, random = means.loc['selective'], means.loc['random']
assert selective.underallocations == 265.8 and random.underallocations == 305.8
reduction = 100 * (1 - selective.underallocations / random.underallocations)
memory = 100 * (selective.requested_gib / random.requested_gib - 1)
assert f'{reduction:.1f}' == '13.1' and f'{memory:.1f}' == '2.4'
result = dict(status='passed', comparison='25 paired budget/seed runs; all regenerated table values match',
              selective_mean_underallocations=265.8, random_mean_underallocations=305.8,
              reduction_pct=float(reduction), more_requested_memory_pct=float(memory),
              verification_level='reaggregation of recorded task outcomes; no model retraining')
(ROOT / 'results/reproduction_validation.json').write_text(json.dumps(result, indent=2)+'\n')
print(json.dumps(result, indent=2))
