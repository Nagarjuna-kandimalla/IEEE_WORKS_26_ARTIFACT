#!/usr/bin/env python3
"""Validate the bundled original predictions, native metrics, and task splits.

Reads local copies only. Never trains, accesses S3, or modifies the artifact.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from paper_metrics import metrics

ROOT = Path(__file__).resolve().parents[1]
ART = ROOT / 'inputs/original_artifact'
WORKFLOWS = ('gatk', 'minimap2', 'sarek', 'seqinspector', 'taxprofiler', 'viralmetagenome')
INPUTS: set[Path] = set()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path, sep='\t'):
    INPUTS.add(Path(path))
    return pd.read_csv(path, sep=sep, low_memory=False)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def module(name, path):
    INPUTS.add(path)
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def dump(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, allow_nan=False) + '\n')


def same_numbers(a, b, message):
    require(np.allclose(np.asarray(a, dtype=float), np.asarray(b, dtype=float),
                        rtol=1e-12, atol=1e-9, equal_nan=True), message)


def align(a, b, canonical, expected):
    require(len(a) == len(b) == expected, 'Wrong cohort size')
    for frame in (a, b):
        require(frame.logical_task_id.nunique() == expected, 'Duplicate IDs')
        require(frame.seed.eq(1996).all(), 'Wrong seed')
    a = a.set_index('logical_task_id').sort_index()
    b = b.set_index('logical_task_id').sort_index()
    require(a.index.equals(b.index), 'Method task IDs differ')
    for col in ('workflow', 'process'):
        require(a[col].equals(b[col]), 'Method identities differ: ' + col)
    for col in ('actual_peak_mib', 'runtime_seconds'):
        same_numbers(a[col], b[col], 'Method measurements differ: ' + col)
    source = canonical.set_index('logical_task_id').loc[a.index]
    same_numbers(a.actual_peak_mib, source.peak_memory_bytes / 2**20, 'Canonical peaks differ')
    same_numbers(a.runtime_seconds, source.runtime_seconds, 'Canonical runtimes differ')
    for frame in (a, b):
        for col in ('first_allocation_mib', 'actual_peak_mib', 'raw_prediction_mib'):
            require(np.isfinite(frame[col]).all(), 'Nonfinite ' + col)
        require(frame.first_allocation_mib.gt(0).all(), 'Nonpositive requests')
        require(frame.actual_peak_mib.gt(0).all(), 'Nonpositive peaks')
    return a, b


def validate(out):
    canonical = read(ART / 'DATA/RQ2/camp_ml_cohort_33516.csv.gz', ',')
    require(len(canonical) == canonical.logical_task_id.nunique() == 33516, 'Canonical cohort differs')
    rq2 = [read(ART / f'RESULTS/RQ2/csv/{v}_task_predictions.tsv.gz')
           for v in ('a_plus_p', 'a_plus_p_plus_c')]
    a, c = align(*rq2, canonical, 5026)
    manifests = pd.concat([read(ART / f'SCRIPTS/RQ2/data/split_manifests/{w}_1996.tsv') for w in WORKFLOWS])
    require(set(manifests.loc[manifests.role.eq('test'), 'logical_task_id']) == set(a.index), 'RQ2 split differs')
    require(int(a.runtime_seconds.isna().sum()) == 449, 'RQ2 missingness differs')
    for f, under, total, waste in [(a, 87, 26067.44921875, 63.469356039255764),
                                    (c, 31, 34277.5546875, 39.8737959309503)]:
        m = metrics(f)
        require(m['underallocations'] == under, 'RQ2 native underallocations differ')
        same_numbers(m['requested_gib'], total, 'RQ2 native total differs')
        same_numbers(m['positive_unused_gib_hours'], waste, 'RQ2 native waste differs')

    archive = read(ROOT / 'inputs/archived_rq3_matched_task_predictions.tsv.gz')
    fields_checked = {}
    for view, directory in [('A+P', 'a_plus_p'), ('A+P+C', 'a_plus_p_plus_c')]:
        latest = read(ART / f'DATA/RQ4/inputs/seed_1996/{directory}/task_predictions.tsv.gz').set_index('logical_task_id').sort_index()
        recovered = archive.loc[archive.method.eq('CAMP') & archive.feature_view.eq(view)].set_index('logical_task_id').sort_index()
        require(latest.index.equals(recovered.index), 'Archived CAMP IDs differ from latest')
        common = sorted(set(latest) & set(recovered))
        for col in common:
            x, y = latest[col], recovered[col]
            if pd.api.types.is_numeric_dtype(x) and pd.api.types.is_numeric_dtype(y):
                same_numbers(x, y, 'Archived CAMP field differs: ' + col)
            else:
                require(x.astype('string').fillna('<NA>').equals(y.astype('string').fillna('<NA>')), 'Archived CAMP field differs: ' + col)
        fields_checked[view] = common

    # Use the authoritative implementation to reconstruct Sizey's exact split.
    harness = ART / 'SCRIPTS/RQ3/harness/scripts'
    sys.path.insert(0, str(harness))
    prepare = module('original_prepare', harness / 'prepare_data.py')
    INPUTS.add(harness / 'common.py')
    cfg_path = ART / 'SCRIPTS/RQ3/harness/config/experiment.json'
    INPUTS.add(cfg_path)
    cfg = json.loads(cfg_path.read_text())
    eligible = prepare.eligible_rows(canonical, require_runtime=True, minimum_process_rows=cfg['primary_cohort']['minimum_process_rows'])
    require(len(eligible) == 30478, 'Sizey eligible cohort differs')
    split = pd.concat([prepare.split_manifest(prepare.sizey_input(group.reset_index(drop=True)), str(w), 1996)
                       for w, group in eligible.groupby('workflow', sort=True)], ignore_index=True)
    split.to_csv(out / 'rq3_reconstructed_split.tsv.gz', sep='\t', index=False)
    require(int(split.role.eq('train').sum()) == 9141, 'Sizey training count differs')
    required_ids = set(split.loc[split.role.eq('test'), 'logical_task_id'])
    s = archive.loc[archive.method.eq('Sizey')].copy()
    camp = archive.loc[archive.method.eq('CAMP') & archive.feature_view.eq('A+P+C')].copy()
    s, camp = align(s, camp, canonical, 21337)
    require(set(s.index) == required_ids, 'Sizey test membership differs')
    positions = split.loc[split.role.eq('test')].set_index('logical_task_id').loc[s.index, 'replay_position']
    same_numbers(s.replay_position, positions, 'Sizey replay order differs')
    require(s.runtime_seconds.gt(0).all(), 'RQ3 missing/invalid runtime')

    summary = module('original_rq3_summary', ART / 'SCRIPTS/RQ3/scripts/summarize_comparison.py')
    expected = read(ART / 'RESULTS/RQ3/csv/global_metrics.csv', ',').set_index(['method', 'feature_view']).sort_index()
    observed = summary.summary_table(summary.common_metrics(archive), ['method', 'feature_view']).set_index(['method', 'feature_view']).sort_index()
    require(observed.index.equals(expected.index), 'RQ3 summary methods differ')
    require(set(observed) == set(expected), 'RQ3 summary columns differ')
    for col in expected:
        same_numbers(observed[col], expected[col], 'RQ3 original metric differs: ' + col)
    observed.to_csv(out / 'rq3_reproduced_global_metrics.csv')
    checks = {'status': 'passed', 'rq2_tasks': 5026, 'rq2_missing_runtimes': 449,
              'rq3_tasks': 21337, 'rq3_training_tasks': 9141, 'rq3_missing_runtimes': 0,
              'rq3_archived_vs_current_fields_checked': fields_checked,
              'rq3_native_metric_columns_checked': list(expected.columns),
              'rq3_archive_original_source': json.loads((ROOT / 'protocol.json').read_text())['archived_prediction_source'],
              'rq3_archive_status': 'original predictions recovered; current split, canonical targets, current CAMP fields, and all native RQ3 metrics agree',
              'training_rerun': False, 'artifact_commit': json.loads((ROOT / 'input_manifest.json').read_text())['artifact_commit']}
    dump(out / 'validation_manifest.json', checks)
    return [('rq2', 'Access', a, c), ('rq3', 'Sizey', s, camp)]

