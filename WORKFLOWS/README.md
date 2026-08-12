# Workflow acquisition and setup

The CAMP experiments use six workflows. Four are unmodified nf-core workflows
downloaded at fixed releases; the two custom Nextflow workflows are included
under `WORKFLOWS/gatk` and `WORKFLOWS/minimap2`.

| Workflow | Source type | Frozen release | Paper role |
|---|---|---:|---|
| Sarek | nf-core | 3.6.0 | Matched Exp 0/1/2 cohort |
| Seqinspector | nf-core | 1.1.0 | Matched Exp 0/1/2 cohort |
| Taxprofiler | nf-core | 2.0.1 | Matched Exp 0/1/2 cohort |
| Viralmetagenome | nf-core | 1.1.3 | Matched Exp 0/1/2 cohort |
| GATK | Custom Nextflow | GATK 4.5.0.0 | Documented separately |
| Minimap2 | Custom Nextflow | Frozen artifact source | Documented separately |

## Position in the RQ1 end-to-end route

For a complete RQ1 reproduction, use this README in the following order:

1. verify the prerequisites and create `reproduction_root`;
2. download and verify the four nf-core releases;
3. prepare the common ENA/Kraken2 inputs, the Sarek inputs, and both custom
   workflow datasets;
4. complete the installation and worker smoke tests in
   [`AUDIT/README.md`](../AUDIT/README.md);
5. return here and run **Run all nf-core workflows with the audit helpers**,
   followed by **Run the custom workflows with the audit helpers**; and
6. continue with **Summarize the completed audits** in
   [`AUDIT/README.md`](../AUDIT/README.md).

Use the same `RUN_ROOT` value for Steps 5 and 6 and for the RQ1 commands in
[`SCRIPTS/README.md`](../SCRIPTS/README.md). The individual launch forms in
the workflow-specific sections are useful for testing one workflow; the two
**Run all** sections are the canonical six-workflow route.

## Prerequisites

Install these before downloading a workflow:

- Linux on the login and compute nodes;
- Java 17 or another release supported by the installed Nextflow version;
- Nextflow;
- Git;
- Slurm when using the supplied cluster launchers; and
- Apptainer/Singularity, unless another supported container profile is used.

Verify the basic environment:

```bash
java -version
nextflow -version
git --version
apptainer --version
```

STRACE and eBPF requirements for Exp 1 and Exp 2 are documented in
`AUDIT/README.md`.

## Keep downloaded sources reproducible

Run the commands from the artifact root. Keeping `NXF_HOME` inside a separate
reproduction directory makes the downloaded workflow sources and Nextflow
metadata easy to archive without adding them to Git accidentally.

```bash
artifact_root="$PWD"
reproduction_root="${TMPDIR:-/tmp}/camp-workflow-reproduction"
mkdir -p "$reproduction_root/nxf-home" "$reproduction_root/work" \
  "$reproduction_root/results" "$reproduction_root/apptainer-cache"
export NXF_HOME="$reproduction_root/nxf-home"
export NXF_APPTAINER_CACHEDIR="$reproduction_root/apptainer-cache"
```

The downloaded nf-core source will be under:

```text
$NXF_HOME/assets/nf-core/<workflow>
```

Do not commit `$NXF_HOME`, Nextflow work directories, downloaded containers,
or raw sequencing data to the artifact repository.

## Download all non-custom workflows

```bash
nextflow pull nf-core/sarek -r 3.6.0
nextflow pull nf-core/seqinspector -r 1.1.0
nextflow pull nf-core/taxprofiler -r 2.0.1
nextflow pull nf-core/viralmetagenome -r 1.1.3
```

Always supply `-r` when pulling or running a workflow. Omitting it can select a
different release later.

Confirm that Nextflow recognizes each frozen release:

```bash
nextflow run nf-core/sarek -r 3.6.0 --help >/dev/null
nextflow run nf-core/seqinspector -r 1.1.0 --help >/dev/null
nextflow run nf-core/taxprofiler -r 2.0.1 --help >/dev/null
nextflow run nf-core/viralmetagenome -r 1.1.3 --help >/dev/null
```

Record the environment with every reproduction:

```bash
nextflow -version > "$reproduction_root/nextflow-version.txt"
java -version 2> "$reproduction_root/java-version.txt"
apptainer --version > "$reproduction_root/apptainer-version.txt"
```

## Common ENA and Kraken2 setup

The supplied samplesheets contain the exact run selections from the
[European Nucleotide Archive](https://www.ebi.ac.uk/ena/browser/home).
Seqinspector and Viralmetagenome use the same 3,000 paired ENA runs;
Taxprofiler uses all 5,998 paired runs. The default configurations stream the
FASTQs from ENA over HTTPS, so the compute nodes need outbound HTTPS access.

The exact ENA file URLs, byte counts, and MD5 values are stored in the TSV
manifests. The following shell function optionally downloads and verifies a
manifest before a run. It requires `wget`, `awk`, `sed`, `xargs`, and `md5sum`.

```bash
download_ena_pairs() {
  local dataset_manifest="$1"
  local fastq_directory="$2"
  local url1_column="$3"
  local url2_column="$4"
  local md51_column="$5"
  local md52_column="$6"

  mkdir -p "$fastq_directory"
  awk -F $'\t' -v c1="$url1_column" -v c2="$url2_column" \
    'NR > 1 { print $c1; print $c2 }' "$dataset_manifest" |
    sed 's#^ftp://#https://#' |
    xargs -r -n 1 -P "${DOWNLOAD_WORKERS:-8}" \
      wget -c -nv -P "$fastq_directory"

  awk -F $'\t' -v c1="$url1_column" -v c2="$url2_column" \
      -v m1="$md51_column" -v m2="$md52_column" '
    NR > 1 {
      f1 = $c1; f2 = $c2
      sub(/^.*\//, "", f1); sub(/^.*\//, "", f2)
      print $m1 "  " f1
      print $m2 "  " f2
    }
  ' "$dataset_manifest" > "$fastq_directory/ena.md5"
  (cd "$fastq_directory" && md5sum -c ena.md5)
}
```

Seqinspector, Taxprofiler, and Viralmetagenome use the same
[Kraken2 Standard-8 archive](https://genome-idx.s3.amazonaws.com/kraken/k2_standard_08_GB_20260626.tar.gz).
Nextflow can stage it from the URL in the supplied configurations, or it can
be downloaded once to the shared filesystem:

```bash
mkdir -p DATA/kraken2
wget -c \
  https://genome-idx.s3.amazonaws.com/kraken/k2_standard_08_GB_20260626.tar.gz \
  -O DATA/kraken2/k2_standard_08_GB_20260626.tar.gz
echo '7685f43cce057c2ca18511c925399b72  DATA/kraken2/k2_standard_08_GB_20260626.tar.gz' |
  md5sum -c -
export KRAKEN2_DB="$PWD/DATA/kraken2/k2_standard_08_GB_20260626.tar.gz"
```

## Sarek 3.6.0

Repository: [nf-core/sarek](https://github.com/nf-core/sarek)

```bash
nextflow pull nf-core/sarek -r 3.6.0
```

The preserved source commit is:

```text
08887a88b66d24167db6633d022a939b1499e199
```

Verify it after the pull:

```bash
test "$(git -C "$NXF_HOME/assets/nf-core/sarek" rev-parse HEAD)" = \
  08887a88b66d24167db6633d022a939b1499e199
```

Experiment-specific setup:

- Dataset: GIAB NA12878 30x whole-genome sequencing data.
- Interval set: 1,495 non-overlapping GRCh38 primary-chromosome intervals.
- Configure the downloaded `main.nf` location, samplesheet, reference files,
  interval file, container cache, output directory, and work directory.
- The original cohort contains 6,012 matched task rows across 25 processes.
- Keep the same sample, intervals, resources, and clean-cache policy across
  Exp 0, Exp 1, and Exp 2.

The supplied samplesheet points to the public iGenomes
[read 1](https://ngi-igenomes.s3.amazonaws.com/test-data/sarek/NA12878_1.merged.fastq.gz)
and [read 2](https://ngi-igenomes.s3.amazonaws.com/test-data/sarek/NA12878_2.merged.fastq.gz)
objects:

```text
https://ngi-igenomes.s3.amazonaws.com/test-data/sarek/NA12878_1.merged.fastq.gz
https://ngi-igenomes.s3.amazonaws.com/test-data/sarek/NA12878_2.merged.fastq.gz
```

`WORKFLOWS/sarek/data/static_task_manifest_1495.tsv` contains the exact 1,495
HaplotypeCaller interval-task mappings used by RQ1 postprocessing.

Nextflow stages the two S3 objects during the launch below. To download the
approximately 92 GB compressed input first and use local files instead:

```bash
mkdir -p DATA/sarek
wget -c \
  https://ngi-igenomes.s3.amazonaws.com/test-data/sarek/NA12878_1.merged.fastq.gz \
  -O DATA/sarek/NA12878_1.merged.fastq.gz
wget -c \
  https://ngi-igenomes.s3.amazonaws.com/test-data/sarek/NA12878_2.merged.fastq.gz \
  -O DATA/sarek/NA12878_2.merged.fastq.gz

printf 'patient,status,sample,lane,fastq_1,fastq_2\nNA12878,0,NA12878,1,%s,%s\n' \
  "$PWD/DATA/sarek/NA12878_1.merged.fastq.gz" \
  "$PWD/DATA/sarek/NA12878_2.merged.fastq.gz" \
  > DATA/sarek/NA12878.local.csv
```

Add `--input "$PWD/DATA/sarek/NA12878.local.csv"` to the launch command to
use the local copy. `--genome GATK.GRCh38` causes nf-core/Sarek to stage its
GRCh38 iGenomes reference; the 1,495-interval BED is supplied in
`WORKFLOWS/sarek/data`.

Generic launch form after the Sarek experiment overlay is present:

```bash
nextflow run nf-core/sarek -r 3.6.0 \
  -profile apptainer \
  -c WORKFLOWS/sarek/experiment.config \
  -c AUDIT/helper_scripts/exp0/baseline.config \
  -work-dir "$reproduction_root/work/sarek-exp0" \
  --outdir "$reproduction_root/results/sarek-exp0"
```

## Seqinspector 1.1.0

Repository: [nf-core/seqinspector](https://github.com/nf-core/seqinspector)

```bash
nextflow pull nf-core/seqinspector -r 1.1.0
```

Experiment-specific setup:

- Dataset: 3,000 paired-end ENA metagenomic WGS records.
- The selected tool is Kraken2.
- `tools_bundle` is set to `null` to avoid unrelated modules that require
  additional reference data or run-folder metadata.
- Kraken2 receives `--memory-mapping --quick` through `task.ext.args`.
- Configure the samplesheet, Kraken2 database, output directory, container
  cache, and work directory before launch.
- The matched artifact contains 6,001 task rows across three processes.

The remote samplesheet in `WORKFLOWS/seqinspector/data` contains HTTPS links
to the exact 3,000 paired runs at `https://ftp.sra.ebi.ac.uk/`. The launch
command below streams those inputs. To predownload and verify its 6,000 FASTQ
files (46.1 GiB compressed), run:

`WORKFLOWS/seqinspector/data/static_task_manifest_3000.tsv` contains the exact
6,000 Kraken2 and Krona task mappings used by RQ1 postprocessing.

```bash
seq_manifest="$PWD/WORKFLOWS/seqinspector/data/ENA_metagenomic_WGS_3000.reproduction.tsv"
shared_fastq="$PWD/DATA/ena_metagenomic_wgs_3000/fastq"
download_ena_pairs "$seq_manifest" "$shared_fastq" 12 13 17 18

python3 WORKFLOWS/seqinspector/bin/build_local_samplesheet.py \
  --source "$seq_manifest" \
  --fastq-dir "$shared_fastq" \
  --out "$PWD/DATA/ena_metagenomic_wgs_3000/seqinspector.local.csv"
```

Add `--input "$PWD/DATA/ena_metagenomic_wgs_3000/seqinspector.local.csv"`
to the launch command when using the local copy. Set `KRAKEN2_DB` as shown in
the common setup to use the downloaded database archive.

Generic launch form:

```bash
nextflow run nf-core/seqinspector -r 1.1.0 \
  -profile apptainer \
  -c WORKFLOWS/seqinspector/experiment.config \
  -c AUDIT/helper_scripts/exp0/baseline.config \
  -work-dir "$reproduction_root/work/seqinspector-exp0" \
  --outdir "$reproduction_root/results/seqinspector-exp0"
```

## Taxprofiler 2.0.1

Repository: [nf-core/taxprofiler](https://github.com/nf-core/taxprofiler)

```bash
nextflow pull nf-core/taxprofiler -r 2.0.1
```

The preserved source commit is:

```text
70ecc15e49b4f1fcf79d876643b5d14b65c66178
```

Verify it after the pull:

```bash
test "$(git -C "$NXF_HOME/assets/nf-core/taxprofiler" rev-parse HEAD)" = \
  70ecc15e49b4f1fcf79d876643b5d14b65c66178
```

Experiment-specific setup:

- Dataset: 5,998 public paired-end Illumina metagenomic WGS runs from ENA.
- Classifier: Kraken2 only, with run merging disabled.
- Database: Kraken2 Standard-8 dated 2026-06-26.
- Kraken2 runs with `--memory-mapping`.
- Configure the samplesheet, database sheet/archive, container cache, output
  directory, and work directory before launch.
- The matched artifact contains 5,999 task rows across two processes.

The supplied HTTPS samplesheet contains the exact ENA URLs for all 5,998 runs.
The corresponding TSV contains the expected byte counts and MD5 values. The
default launch streams 11,996 FASTQ files (146.6 GiB compressed). To prefetch
and verify them:

`WORKFLOWS/taxprofiler/data/static_task_manifest_5998.tsv` contains the exact
5,998 Kraken2 task mappings used by RQ1 postprocessing.

```bash
tax_manifest="$PWD/WORKFLOWS/taxprofiler/data/ENA_metagenomic_WGS_5998.selected.tsv"
tax_fastq="$PWD/DATA/ena_metagenomic_wgs_5998/fastq"
download_ena_pairs "$tax_manifest" "$tax_fastq" 9 10 14 15

awk -F, -v OFS=, -v root="$tax_fastq" '
  NR == 1 { print; next }
  {
    sub(/^.*\//, "", $4); sub(/^.*\//, "", $5)
    $4 = root "/" $4; $5 = root "/" $5
    print
  }
' WORKFLOWS/taxprofiler/data/ENA_metagenomic_WGS_5998.https.csv \
  > DATA/ena_metagenomic_wgs_5998/taxprofiler.local.csv
```

Add `--input "$PWD/DATA/ena_metagenomic_wgs_5998/taxprofiler.local.csv"` to
the launch command to use the local FASTQs. To use the locally downloaded
Kraken2 archive, create a local database sheet and pass it with `--databases`:

```bash
printf 'tool,db_name,db_params,db_type,db_path\nkraken2,standard8_20260626,--memory-mapping --quick,short,%s\n' \
  "$KRAKEN2_DB" > DATA/kraken2/taxprofiler.local.csv
```

Generic launch form:

```bash
nextflow run nf-core/taxprofiler -r 2.0.1 \
  -profile apptainer \
  -c WORKFLOWS/taxprofiler/experiment.config \
  -c AUDIT/helper_scripts/exp0/baseline.config \
  -work-dir "$reproduction_root/work/taxprofiler-exp0" \
  --outdir "$reproduction_root/results/taxprofiler-exp0"
```

## Viralmetagenome 1.1.3

Repository: [nf-core/viralmetagenome](https://github.com/nf-core/viralmetagenome)

```bash
nextflow pull nf-core/viralmetagenome -r 1.1.3
```

Experiment-specific setup:

- Dataset: 3,000 paired-end ENA metagenomic WGS records.
- Select the read-level metagenomic-diversity path with
  `read_classifiers = 'kraken2'`.
- Disable assembly, polishing, variant calling, consensus QC, Kaiju, and
  Bracken.
- Leave preprocessing enabled as a pass-through with `skip_fastqc`,
  `skip_trimming`, and `skip_hostremoval` set to `true`. Fully bypassing the
  preprocessing path causes the workflow to inspect every input with
  `countFastq()` before classification.
- Kraken2 receives `--memory-mapping --quick`.
- Database: Kraken2 Standard-8 dated 2026-06-26.
- The matched artifact contains 9,003 task rows across six processes.

This workflow uses the same 3,000-run ENA selection and the same Kraken2
archive as Seqinspector. Its supplied samplesheet streams the HTTPS URLs. If
the shared FASTQs were not already downloaded for Seqinspector, run:

`WORKFLOWS/viralmetagenome/data/static_task_manifest_3000.tsv` contains the
exact 9,000 Kraken2, report-conversion, and cleanup task mappings used by RQ1
postprocessing.

```bash
viral_manifest="$PWD/WORKFLOWS/viralmetagenome/data/ENA_metagenomic_WGS_3000.reproduction.tsv"
shared_fastq="$PWD/DATA/ena_metagenomic_wgs_3000/fastq"
download_ena_pairs "$viral_manifest" "$shared_fastq" 11 12 16 17
```

Build the workflow-specific local samplesheet from that shared directory:

```bash
python3 WORKFLOWS/viralmetagenome/bin/build_local_samplesheet.py \
  --source "$viral_manifest" \
  --fastq-dir "$shared_fastq" \
  --out "$PWD/DATA/ena_metagenomic_wgs_3000/viralmetagenome.local.csv"
```

Add `--input "$PWD/DATA/ena_metagenomic_wgs_3000/viralmetagenome.local.csv"`
to the launch command when using local FASTQs. Set `KRAKEN2_DB` as shown in
the common setup to reuse the downloaded database.

Generic launch form:

```bash
nextflow run nf-core/viralmetagenome -r 1.1.3 \
  -profile apptainer \
  -c WORKFLOWS/viralmetagenome/experiment.config \
  -c AUDIT/helper_scripts/exp0/baseline.config \
  -work-dir "$reproduction_root/work/viralmetagenome-exp0" \
  --outdir "$reproduction_root/results/viralmetagenome-exp0"
```

## Custom workflow datasets

Run the commands below from the artifact root.

### GATK dataset

The GATK workflow does not download an external dataset. It uses the included
compressed synthetic reference, the 20-sample definition, and the 25-interval
definition:

```text
WORKFLOWS/gatk/data/hg38_synthetic.fa.gz
WORKFLOWS/gatk/data/samples_20.tsv
WORKFLOWS/gatk/data/intervals_25.tsv
```

Decompress the reference before running Nextflow:

```bash
gzip -dc WORKFLOWS/gatk/data/hg38_synthetic.fa.gz \
  > WORKFLOWS/gatk/data/hg38_synthetic.fa
```

The workflow generates the paired FASTQ files from this reference when it
runs. To generate the same 20 sample pairs manually:

```bash
gatk_data="$PWD/WORKFLOWS/gatk/data"
gatk_fastq="$PWD/DATA/gatk/fastq"
mkdir -p "$gatk_fastq"

tail -n +2 "$gatk_data/samples_20.tsv" |
while IFS=$'\t' read -r sample_id coverage seed; do
  python3 WORKFLOWS/gatk/bin/generate_paired_fastq.py \
    --reference "$gatk_data/hg38_synthetic.fa" \
    --sample-id "$sample_id" \
    --coverage "$coverage" \
    --seed "$seed" \
    --read1 "$gatk_fastq/${sample_id}_1.fastq.gz" \
    --read2 "$gatk_fastq/${sample_id}_2.fastq.gz"
done
```

The seeds and coverage values in `samples_20.tsv` make this generation
deterministic. The Nextflow workflow performs this generation itself, so the
manual FASTQ command is not required for a normal run.

### Minimap2 dataset

The Minimap2 workflow also has no external dataset download. It creates a
synthetic reference, annotations, a 1,500-window plan, and the corresponding
ONT-style reads from included scripts and fixed seeds.

The workflow performs all generation automatically. The equivalent standalone
commands are:

```bash
minimap_data="$PWD/DATA/minimap2"
mkdir -p "$minimap_data/reads"

python3 WORKFLOWS/minimap2/scripts/generate_reference.py \
  --out-fa "$minimap_data/genome.fa" \
  --out-bed "$minimap_data/genome_annotations.bed" \
  --seed 1234

python3 WORKFLOWS/minimap2/scripts/plan_windows.py \
  --n-windows 1500 \
  --out "$minimap_data/window_manifest_1500.tsv" \
  --seed 7777

tail -n +2 "$minimap_data/window_manifest_1500.tsv" |
while IFS=$'\t' read -r window_id filename window_type expected_peak_gb seed; do
  python3 WORKFLOWS/minimap2/scripts/generate_window_reads.py \
    --genome "$minimap_data/genome.fa" \
    --annotations "$minimap_data/genome_annotations.bed" \
    --window-id "$window_id" \
    --window-type "$window_type" \
    --seed "$seed" \
    --out "$minimap_data/reads/$filename"
done
```

The included `WORKFLOWS/minimap2/data/window_manifest_1500.tsv` contains the
same 1,500 window identifiers, types, filenames, and seeds.

## Single-workflow audit-mode example

The audit helper files are under `AUDIT/helper_scripts`:

```text
exp0/  baseline, without syscall auditing
exp1/  STRACE auditing
exp2/  eBPF auditing
```

Downloaded nf-core workflows use the generic `baseline.config`,
`strace.config`, and `ebpf.config` files in those directories. The custom
workflows use the workflow-specific `gatk.config` or `minimap2.config` file.
Supply the helper after the workflow's normal configuration with `-c`.

For example, change only the helper, result directory, and work directory to
run the three modes of a downloaded workflow:

```bash
mkdir -p "$reproduction_root/results/sarek-exp0" \
  "$reproduction_root/results/sarek-exp1" \
  "$reproduction_root/results/sarek-exp2"

nextflow run nf-core/sarek -r 3.6.0 -profile apptainer \
  -c WORKFLOWS/sarek/experiment.config \
  -c AUDIT/helper_scripts/exp0/baseline.config \
  -work-dir "$reproduction_root/work/sarek-exp0" \
  -with-trace "$reproduction_root/results/sarek-exp0/nextflow_trace.tsv" \
  -with-report "$reproduction_root/results/sarek-exp0/nextflow_report.html" \
  -with-timeline "$reproduction_root/results/sarek-exp0/nextflow_timeline.html" \
  -with-dag "$reproduction_root/results/sarek-exp0/nextflow_dag.html" \
  --outdir "$reproduction_root/results/sarek-exp0"

nextflow run nf-core/sarek -r 3.6.0 -profile apptainer \
  -c WORKFLOWS/sarek/experiment.config \
  -c AUDIT/helper_scripts/exp1/strace.config \
  -work-dir "$reproduction_root/work/sarek-exp1" \
  -with-trace "$reproduction_root/results/sarek-exp1/nextflow_trace.tsv" \
  -with-report "$reproduction_root/results/sarek-exp1/nextflow_report.html" \
  -with-timeline "$reproduction_root/results/sarek-exp1/nextflow_timeline.html" \
  -with-dag "$reproduction_root/results/sarek-exp1/nextflow_dag.html" \
  --outdir "$reproduction_root/results/sarek-exp1"

nextflow run nf-core/sarek -r 3.6.0 -profile apptainer \
  -c WORKFLOWS/sarek/experiment.config \
  -c AUDIT/helper_scripts/exp2/ebpf.config \
  -work-dir "$reproduction_root/work/sarek-exp2" \
  -with-trace "$reproduction_root/results/sarek-exp2/nextflow_trace.tsv" \
  -with-report "$reproduction_root/results/sarek-exp2/nextflow_report.html" \
  -with-timeline "$reproduction_root/results/sarek-exp2/nextflow_timeline.html" \
  -with-dag "$reproduction_root/results/sarek-exp2/nextflow_dag.html" \
  --outdir "$reproduction_root/results/sarek-exp2"
```

## Run all nf-core workflows with the audit helpers

For the overhead comparison, predownload the inputs using the commands above
and create the four local samplesheets. This keeps input transfer outside the
measured workflow lifecycle. The following function writes every run to the
layout consumed directly by the RQ1 postprocessing commands:

```bash
artifact_root="$PWD"
run_root="${RUN_ROOT:-$artifact_root/RQ1_RUNS}"
export EBPF_PYTHON=/usr/bin/python3
export EBPF_TRACER="$artifact_root/AUDIT/helper_scripts/exp2/ebpf_audit_py39.py"

run_nfcore() {
  workflow="$1"
  release="$2"
  mode="$3"
  case "$mode" in
    exp0) helper=baseline ;;
    exp1) helper=strace ;;
    exp2) helper=ebpf ;;
  esac
  case "$workflow" in
    sarek)
      input_arguments=(--input "$artifact_root/DATA/sarek/NA12878.local.csv")
      ;;
    seqinspector)
      input_arguments=(
        --input "$artifact_root/DATA/ena_metagenomic_wgs_3000/seqinspector.local.csv"
      )
      ;;
    taxprofiler)
      input_arguments=(
        --input "$artifact_root/DATA/ena_metagenomic_wgs_5998/taxprofiler.local.csv"
        --databases "$artifact_root/DATA/kraken2/taxprofiler.local.csv"
      )
      ;;
    viralmetagenome)
      input_arguments=(
        --input "$artifact_root/DATA/ena_metagenomic_wgs_3000/viralmetagenome.local.csv"
      )
      ;;
  esac

  run_dir="$run_root/$workflow/$mode"
  mkdir -p "$run_dir"
  for required in "${input_arguments[@]}"; do
    case "$required" in --*) ;; *) test -s "$required" ;; esac
  done
  NXF_LOG_FILE="$run_dir/nextflow.log" \
  nextflow run "nf-core/$workflow" -r "$release" \
    -profile apptainer \
    -c "$artifact_root/WORKFLOWS/$workflow/experiment.config" \
    -c "$artifact_root/AUDIT/helper_scripts/$mode/$helper.config" \
    -work-dir "$run_dir/work" \
    -with-trace "$run_dir/nextflow_trace.tsv" \
    -with-report "$run_dir/nextflow_report.html" \
    -with-timeline "$run_dir/nextflow_timeline.html" \
    -with-dag "$run_dir/nextflow_dag.html" \
    --outdir "$run_dir/results" \
    "${input_arguments[@]}"
}

for mode in exp0 exp1 exp2; do
  run_nfcore sarek 3.6.0 "$mode"
  run_nfcore seqinspector 1.1.0 "$mode"
  run_nfcore taxprofiler 2.0.1 "$mode"
  run_nfcore viralmetagenome 1.1.3 "$mode"
done
```

Run the modes sequentially, or submit each function body as a Slurm
coordinator job with dependencies. Keep the same local inputs, worker pool,
queue size, container cache policy, and empty work directory for all three
modes.

## Run the custom workflows with the audit helpers

Run these commands from the artifact root. Create a separate empty work and
result directory for each mode:

```bash
artifact_root="$PWD"
run_root="${RUN_ROOT:-$artifact_root/RQ1_RUNS}"
mkdir -p "$run_root"

run_custom() {
  workflow="$1"
  mode="$2"
  mkdir -p "$run_root/$workflow/$mode"
  NXF_LOG_FILE="$run_root/$workflow/$mode/nextflow.log" \
  nextflow run "$artifact_root/WORKFLOWS/$workflow/main.nf" \
    -profile slurm \
    -c "$artifact_root/AUDIT/helper_scripts/$mode/$workflow.config" \
    -with-trace "$run_root/$workflow/$mode/nextflow_trace.tsv" \
    -with-report "$run_root/$workflow/$mode/nextflow_report.html" \
    -with-timeline "$run_root/$workflow/$mode/nextflow_timeline.html" \
    -with-dag "$run_root/$workflow/$mode/nextflow_dag.html" \
    --outdir "$run_root/$workflow/$mode/results" \
    --work_dir "$run_root/$workflow/$mode/work" \
    -work-dir "$run_root/$workflow/$mode/work"
}
```

Keep `nextflow.log` with every run. The trace measures the task span, while
the log records the complete Nextflow launcher-to-shutdown lifecycle used for
the direct GATK workflow timing.

Before any Exp2 run, point the helpers at the system Python that provides BCC
and the included tracer:

```bash
export EBPF_PYTHON=/usr/bin/python3
export EBPF_TRACER="$artifact_root/AUDIT/helper_scripts/exp2/ebpf_audit_py39.py"
sudo -n "$EBPF_PYTHON" -c 'from bcc import BPF'
```

GATK requires Java, Apptainer, a GATK 4.5.0.0 jar, and BWA and Samtools
container images. Prepare it once, then run all three modes:

```bash
gzip -dc WORKFLOWS/gatk/data/hg38_synthetic.fa.gz \
  > WORKFLOWS/gatk/data/hg38_synthetic.fa
export GATK_JAR=/path/to/gatk-package-4.5.0.0-local.jar
export BWA_IMAGE=/path/to/bwa.sif
export SAMTOOLS_IMAGE=/path/to/samtools.sif
export APPTAINER_BIND="$artifact_root,$run_root"

run_custom gatk exp0
run_custom gatk exp1
run_custom gatk exp2
```

Minimap2 requires `minimap2` and `samtools` on every worker node:

```bash
run_custom minimap2 exp0
run_custom minimap2 exp1
run_custom minimap2 exp2
```

Change only the overlay, output directory, and work directory between modes.
Do not use `-resume` for a fresh overhead comparison: cached tasks do not
execute and invalidate the comparison. Use `-resume` only to recover the same
run from a documented infrastructure interruption.

Exp 2 requires BCC/eBPF support and elevated or delegated BPF privileges. Do
not run the entire scheduler client as root solely to obtain tracer access;
use the site's approved privileged helper, delegated capabilities, or job
configuration.

All six workflow triplets now share the layout
`$RUN_ROOT/<workflow>/exp0|exp1|exp2`. Continue with **Summarize the completed
audits** in [`AUDIT/README.md`](../AUDIT/README.md); do not run the normalizer
before those Exp1 and Exp2 summary files have been created.

## Expected run and postprocessing outputs

Immediately after Nextflow completes, check every run for:

```text
nextflow_trace.tsv
nextflow_report.html
nextflow_timeline.html
nextflow_dag.html
```

After running the audit summarizers in [`AUDIT/README.md`](../AUDIT/README.md)
and the normalization loop in [`SCRIPTS/README.md`](../SCRIPTS/README.md), also
check for:

```text
training_task_metrics.tsv
training_task_metrics_with_inputs.tsv
```

Exp 1 additionally produces a STRACE task summary. Exp 2 produces an eBPF task
summary and mmap page-fault percentages. The normalization and aggregation
programs used after these runs are under `SCRIPTS/RQ1/postprocessing`; their
commands and required inputs are listed in `SCRIPTS/README.md`.
