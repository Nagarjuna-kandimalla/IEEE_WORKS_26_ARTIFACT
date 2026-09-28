"""The request-scaling metrics reported for R3-W1; no training or resampling."""
import numpy as np


def metrics(frame, factor=1.0):
    request = frame.first_allocation_mib.to_numpy(float) * factor
    peak = frame.actual_peak_mib.to_numpy(float)
    runtime = frame.runtime_seconds.fillna(0.0).to_numpy(float)
    under = request < peak
    return {
        'tasks': len(frame), 'factor': float(factor),
        'missing_runtime_tasks': int(frame.runtime_seconds.isna().sum()),
        'underallocations': int(under.sum()),
        'coverage_pct': float(100 * (~under).sum() / len(frame)),
        'requested_gib': float(request.sum() / 1024),
        'positive_unused_gib_hours': float(
            (np.maximum(request - peak, 0) * runtime).sum() / (1024 * 3600)),
    }


def coverage_match(frame, target):
    """Preserve the original operating-point script's threshold calculation."""
    if not 0 < target <= 1:
        raise ValueError('Coverage target must be in (0, 1].')
    request = frame.first_allocation_mib.to_numpy(float)
    peak = frame.actual_peak_mib.to_numpy(float)
    total = request.sum() / 1024
    thresholds = np.sort((peak / request) * total)
    count = int(np.ceil(target * len(frame)))
    budget = float(thresholds[count - 1])
    result = metrics(frame, budget / total)
    covered = int(np.searchsorted(thresholds, budget, side='right'))
    # Confirm actual scaled requests agree with the original threshold method.
    if len(frame) - result['underallocations'] != covered:
        raise ValueError('Floating-point coverage boundary requires investigation.')
    if np.searchsorted(thresholds, budget, side='left') >= count:
        raise ValueError('Selected budget is not the first threshold meeting target.')
    return result
