# CAMP conceptual connections

This page explains the logic of CAMP without installation steps, commands, or
file-manipulation details.

## The complete idea

A workflow is made of tasks. Before a task starts, the scheduler must reserve
memory, but the task's true peak memory and data consumption are still
unknown. CAMP turns information from already completed tasks into a causal
memory decision for the next task. Runtime auditing supplies a consumption
signal; causal history converts completed observations into reusable context;
models predict the next task's likely consumption and peak memory; calibration
adds a safety margin; and selective auditing decides which future tasks are
most valuable to measure when auditing everything is too expensive.

The conceptual sequence is:

```text
workflow task definition
        |
        v
prelaunch information (A)
        |
        +-------- prior completed-task peak history (P)
        |                         |
        |                         v
        |                 A+P memory view
        |
        +-------- prior audited consumption history and predicted C
                                  |
                                  v
                         A+P+C memory view
                                  |
                                  v
                 quantile prediction and calibration
                                  |
                                  v
                    first memory allocation request
                                  |
                     task executes and is observed
                                  |
              +-------------------+-------------------+
              |                                       |
              v                                       v
      history becomes richer                 audit cost is measured
              |                                       |
              +-------------------+-------------------+
                                  v
                     selective-auditing gates
                                  |
                                  v
             choose the most useful next tasks to audit
```

## A, P, and C

`A` is what is available before launch without observing earlier runtime
behavior. It describes the task, process, workflow version, input sizes,
requested threads and heap, interval or coverage information, and worker
configuration. It is the static anchor.

`P` is causal peak-memory history. It describes only tasks that have already
completed: their peak-memory quantiles, support, spread, confidence, and
similarity to the current task. It provides evidence about how related tasks
behaved, without looking at the current task's outcome.

`C` is causal consumed-data information. It contains prior consumption
history and a prediction of current consumption, denoted `C-hat`. The current
task's measured consumption is never used as its own input. The intent is to
distinguish tasks that look similar statically but touch different amounts of
their available data.

The principal comparison in RQ2 is:

```text
Access = A + P
CAMP   = A + P + C
```

RQ3 additionally evaluates the static `A` view because the external Sizey
baseline is aligned with a static-feature and peak-feedback setting.

## Why auditing is upstream of prediction

Peak RSS says how much resident memory the task reached. Consumption says how
much data was actually read or faulted into memory. They are related but not
identical. STRACE observes successful read-family return bytes. eBPF observes
those read bytes and attributable file-backed mmap page-fault bytes. The eBPF
quantity used by CAMP is therefore:

```text
consumed bytes = successful read-return bytes + mmap page-fault bytes
```

Auditing completed tasks creates the historical consumption labels used to
learn `C-hat` and the consumed-history portion of `C`. It is not legitimate to
use the current task's final consumption when deciding that task's initial
memory request.

## Why history has several scopes

An exact task signature can be very informative but may have little support.
A workflow-wide history has more support but is less specific. CAMP therefore
searches a hierarchy from exact and input-related matches through similar
tasks, process context, workflow context, and global fallback. Support and
confidence state how much evidence exists at the chosen scope. This creates a
controlled transition from specific evidence to broad fallback for cold or
rare tasks.

## Why a prediction is not yet an allocation

A median prediction describes a typical value; it does not guarantee that a
memory request will cover a rare upper-tail event. CAMP fits several quantiles
and then calibrates candidate allocation rules with historical log residuals.
The selected rule seeks the configured global and workflow coverage targets
while minimizing the amount requested. The final request is rounded upward to
MiB.

Consequently, better median error does not automatically mean fewer
underallocations. Underallocation is controlled by upper-tail quality and the
selected safety correction. This is why RQ2 separately reports accuracy,
coverage, requests, unused memory-time, and underallocations.

## Why chronological splits need not improve monotonically

Increasing the development fraction also changes the future holdout. The last
10% of tasks can have a different distribution from the last 15%, and each
split independently refits its models and selects a discrete allocation
policy. More development data therefore does not guarantee a monotonic fall
in holdout underallocations. The RQ2 five-seed/five-split experiment measures
this model-and-policy sensitivity rather than variation between new workflow
executions.

## Why selective auditing comes after allocation modeling

Full auditing yields the richest history but has a runtime cost. Once CAMP can
estimate allocation risk and information value from pre-outcome features, it
can prioritize a limited audit budget. The three-gate selector divides the
budget among:

- risk: tasks likely to be underallocated or to have large shortfalls;
- information: tasks whose audit is likely to improve knowledge;
- discovery: tasks that preserve exploration and reduce blind spots.

Uniform random selection tests whether any equal-size sample would work.
Process-stratified random selection tests whether merely spreading the budget
across processes explains the benefit. CAMP's selector must beat both at the
same task count to demonstrate value beyond sample size and process balance.

## How the research questions connect

RQ1 establishes that the consumption signal exists, defines what STRACE and
eBPF measure, and quantifies their overhead. RQ2 asks whether causal consumed
information changes memory prediction and allocation relative to A+P, and
then tests seed and chronological-split stability. RQ3 places CAMP beside
Sizey on the same task population and assignments. RQ4 closes the loop by
asking which tasks should be audited when only a fraction can be selected.

Together, the questions form one system argument:

```text
measure a useful signal
    -> use it causally for memory allocation
    -> compare the resulting allocator with an external method
    -> acquire future signal selectively when full measurement is costly
```
