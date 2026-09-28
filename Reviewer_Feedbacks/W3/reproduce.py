"""Recompute the paper's native 32-versus-30 mmap ablation from task predictions."""
from pathlib import Path
import hashlib
import json
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
expected = json.loads((ROOT / 'inputs/expected_primary.json').read_text())
records = []
identity = None
for mode in ['read','read_mmap']:
    p = ROOT / 'runs' / mode / 'seed_1996/task_predictions.tsv.gz'
    d = pd.read_csv(p, sep='\t').sort_values('logical_task_id').reset_index(drop=True)
    assert len(d) == 5026 and d.logical_task_id.is_unique and d.seed.eq(1996).all()
    ids = d[['logical_task_id','workflow','process','actual_peak_mib']]
    if identity is None: identity = ids
    else: pd.testing.assert_frame_equal(identity, ids, check_exact=True)
    under = int((d.first_allocation_mib < d.actual_peak_mib).sum())
    budget = float(d.first_allocation_mib.sum()/1024)
    mdape = float(100*np.median(abs(d.point_q50_prediction_mib/d.actual_peak_mib-1)))
    assert under == expected[mode]['underallocations']
    np.testing.assert_allclose([budget,mdape], [expected[mode]['request_gib'],expected[mode]['mdape_pct']],rtol=1e-12,atol=1e-10)
    assert d.initial_oom.astype(bool).sum() == under
    records.append(dict(signal=mode,seed=1996,tasks=len(d),underallocations=under,
                        requested_gib=budget,mdape_pct=mdape,
                        prediction_sha256=hashlib.sha256(p.read_bytes()).hexdigest()))
assert [r['underallocations'] for r in records] == [32,30]
out = ROOT / 'results';out.mkdir(exist_ok=True)
pd.DataFrame(records).to_csv(out/'paper_native_metrics.csv',index=False)
validation=dict(status='passed',tasks_per_arm=5026,seed=1996,read_underallocations=32,
                read_mmap_underallocations=30,request_scaling=False,
                original_prediction_bytes_match=all(r['prediction_sha256']==expected[r['signal']]['prediction_sha256'] for r in records),
                verification_level='metrics recomputed from original task predictions; no fresh training')
(out/'reproduction_validation.json').write_text(json.dumps(validation,indent=2)+'\n')
print(json.dumps(validation,indent=2))
