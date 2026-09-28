from pathlib import Path
import pandas as pd
import json
ROOT=Path(__file__).resolve().parents[1]
for part in ['initial','test']:
    read=pd.read_pickle(ROOT/'data/read'/f'{part}.pkl')
    full=pd.read_pickle(ROOT/'data/read_mmap'/f'{part}.pkl')
    allowed=[c for c in full.columns if 'consumed' in c]
    pd.testing.assert_frame_equal(read.drop(columns=allowed),full.drop(columns=allowed),check_exact=True)
    assert (read.ablation_consumed_bytes==read.ebpf_read_return_bytes).all()
    assert (full.ablation_consumed_bytes==full.ebpf_total_consumed_bytes).all()
(ROOT/'results/feature_validation.json').write_text(json.dumps({
    'passed':True,'allowed_changes':'consumption definition and consumption-history summaries only',
    'raw_measurements_preserved':True,'task_ids_and_non_consumption_features_identical':True},indent=2)+'\n')
print('Feature parity validated.')
