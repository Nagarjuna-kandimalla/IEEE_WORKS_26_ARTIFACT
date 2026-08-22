# RQ5 results

RQ5 retains the successful Bowtie2 direct A+P+C cold/warm `v4` result tables and
the revised figure without an end-to-end runtime panel.

```text
csv/bowtie2_cold_attempts.csv.gz
csv/bowtie2_warm_attempts.csv.gz
csv/bowtie2_cold_task_instances.tsv.gz
csv/bowtie2_warm_task_instances.tsv.gz
figures/fig_rq5_bowtie_feedback.png
figures/fig_rq5_bowtie_feedback.pdf
```

The attempt tables preserve 6,016 cold attempts and 6,015 warm attempts. The
task-instance tables each contain one row for each of 6,000 logical tasks and
are the direct figure inputs.

Regenerate or repeat the experiment by following
[`../../SCRIPTS/RQ5/README.md`](../../SCRIPTS/RQ5/README.md). The packaged-data
route needs only the two task-instance tables; the attempt tables provide
additional per-attempt evidence, including retries.

Verify the retained files from the artifact root:

```bash
gzip -t RESULTS/RQ5/csv/bowtie2_cold_attempts.csv.gz
gzip -t RESULTS/RQ5/csv/bowtie2_warm_attempts.csv.gz
gzip -t RESULTS/RQ5/csv/bowtie2_cold_task_instances.tsv.gz
gzip -t RESULTS/RQ5/csv/bowtie2_warm_task_instances.tsv.gz
test "$(gzip -cd RESULTS/RQ5/csv/bowtie2_cold_attempts.csv.gz | wc -l)" -eq 6017
test "$(gzip -cd RESULTS/RQ5/csv/bowtie2_warm_attempts.csv.gz | wc -l)" -eq 6016
test "$(gzip -cd RESULTS/RQ5/csv/bowtie2_cold_task_instances.tsv.gz | wc -l)" -eq 6001
test "$(gzip -cd RESULTS/RQ5/csv/bowtie2_warm_task_instances.tsv.gz | wc -l)" -eq 6001

echo '23d6a0fee8be06e8d795d09d994c5df7f60092b3e626f610de91d8e87012f820  RESULTS/RQ5/csv/bowtie2_cold_attempts.csv.gz' | sha256sum -c -
echo 'a10bfc8b2ccc0f205f27f062ffc355d3deea6165a4eac907c83d7179d374dbf5  RESULTS/RQ5/csv/bowtie2_warm_attempts.csv.gz' | sha256sum -c -
echo '0fc92d92db85fbc244fc5b6b3fc1bdd71605df8922641271a2e09c1b87cef84b  RESULTS/RQ5/csv/bowtie2_cold_task_instances.tsv.gz' | sha256sum -c -
echo 'c7458fcda6bde477d9fe3ef70fd5abb5e30499e0dde95e486a073c835ad3b897  RESULTS/RQ5/csv/bowtie2_warm_task_instances.tsv.gz' | sha256sum -c -
echo '83975dd337287ca6985bf85067dba30d040b7fbf4c9e2f21244cb24cab6b85f5  RESULTS/RQ5/figures/fig_rq5_bowtie_feedback.png' | sha256sum -c -
```
