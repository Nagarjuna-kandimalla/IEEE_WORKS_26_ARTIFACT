nextflow.enable.dsl = 2

process PREPARE_REFERENCE {
    tag 'reference'
    publishDir "${params.outdir}/reference", mode: 'copy', overwrite: false

    input:
    path reference

    output:
    path 'reference_bundle', emit: bundle

    script:
    """
    mkdir -p reference_bundle
    cp ${reference} reference_bundle/reference.fa
    apptainer exec --bind ${params.apptainer_bind} ${params.samtools_image} samtools faidx reference_bundle/reference.fa
    java -jar ${params.gatk_jar} CreateSequenceDictionary \\
      -R reference_bundle/reference.fa \\
      -O reference_bundle/reference.dict
    """
}

process BUILD_BWA_INDEX {
    tag 'reference_bwa_index'
    publishDir "${params.outdir}/reference", mode: 'copy', overwrite: false

    input:
    path reference_bundle

    output:
    path 'bwa_index', emit: index

    script:
    """
    mkdir -p bwa_index
    cp reference_bundle/reference.fa bwa_index/reference.fa
    cp reference_bundle/reference.fa.fai bwa_index/reference.fa.fai
    cp reference_bundle/reference.dict bwa_index/reference.dict
    apptainer exec --bind ${params.apptainer_bind} ${params.bwa_image} bwa index bwa_index/reference.fa
    """
}

process SIMULATE_FASTQ {
    tag { sample_id }
    publishDir "${params.outdir}/fastq", mode: 'copy', overwrite: false

    input:
    tuple val(sample_id), val(coverage), val(seed), path(reference_bundle)

    output:
    tuple val(sample_id), val(coverage), val(seed), path("${sample_id}_1.fastq.gz"), path("${sample_id}_2.fastq.gz"), emit: reads

    script:
    """
    python3 ${projectDir}/bin/generate_paired_fastq.py \\
      --reference reference_bundle/reference.fa \\
      --sample-id ${sample_id} \\
      --coverage ${coverage} \\
      --seed ${seed} \\
      --read1 ${sample_id}_1.fastq.gz \\
      --read2 ${sample_id}_2.fastq.gz
    """
}

process ALIGN_AND_INDEX {
    tag { sample_id }
    publishDir "${params.outdir}/bam", mode: 'copy', overwrite: false

    input:
    tuple val(sample_id), val(coverage), val(seed), path(read1), path(read2), path(bwa_index)

    output:
    tuple val(sample_id), val(coverage), val(seed), path("${sample_id}.bam"), path("${sample_id}.bam.bai"), emit: bam

    script:
    """
    apptainer exec --bind ${params.apptainer_bind} ${params.bwa_image} bwa mem -t ${task.cpus} \\
      -R '@RG\\tID:${sample_id}\\tSM:${sample_id}\\tPL:ILLUMINA' \\
      bwa_index/reference.fa ${read1} ${read2} | \\
      apptainer exec --bind ${params.apptainer_bind} ${params.samtools_image} samtools sort -@ ${task.cpus} -o ${sample_id}.bam -
    apptainer exec --bind ${params.apptainer_bind} ${params.samtools_image} samtools index -@ ${task.cpus} ${sample_id}.bam
    """
}

process STATIC_MANIFEST {
    tag 'static_manifest'
    publishDir "${params.outdir}/manifests", mode: 'copy', overwrite: false

    input:
    path samples
    path intervals
    path reference

    output:
    path 'static_task_manifest.tsv'

    script:
    """
    python3 ${projectDir}/bin/build_static_task_manifest.py \\
      --samples ${samples} \\
      --intervals ${intervals} \\
      --reference ${reference} \\
      --dataset-id ${params.dataset_id} \\
      --out static_task_manifest.tsv
    """
}

process HAPLOTYPECALLER {
    tag { "${sample_id}.scatter_${scatter_id}" }
    memory { "${predicted_memory_mb} MB" }
    publishDir "${params.outdir}/tasks", mode: 'copy', overwrite: false

    input:
    tuple val(sample_id), val(coverage), val(seed), path(bam), path(bai), val(scatter_id), val(chrom), val(start), val(end), val(contig_length), val(has_str_region), val(predicted_memory_mb), val(static_prediction_mb), val(history_prediction_mb), val(online_model_version), val(online_logical_task_id), val(online_decision_time), path(reference_bundle)

    output:
    path "${sample_id}.scatter_${scatter_id}", emit: task_result

    script:
    def taskPlanArg = params.task_plan ? "--task-plan ${params.task_plan}" : ''
    """
    python3 ${projectDir}/bin/run_haplotypecaller_task.py \\
      --sample-id ${sample_id} \\
      --coverage ${coverage} \\
      --seed ${seed} \\
      --scatter-id ${scatter_id} \\
      --chrom ${chrom} \\
      --start ${start} \\
      --end ${end} \\
      --contig-length ${contig_length} \\
      --has-str-region ${has_str_region} \\
      --reference reference_bundle/reference.fa \\
      --bam ${bam} \\
      --bai ${bai} \\
      --gatk-jar ${params.gatk_jar} \\
      --java-heap-mb ${params.java_heap_mb} \\
      --threads ${task.cpus} \\
      --instrumentation ${params.instrumentation} \\
      --dataset-id ${params.dataset_id} \\
      ${taskPlanArg} \\
      --ebpf-python ${params.ebpf_python} \\
      --ebpf-tracer-python ${params.ebpf_tracer_python} \\
      --ebpf-runner ${params.ebpf_runner} \\
      --ebpf-script ${params.ebpf_script} \\
      --online-logical-task-id '${online_logical_task_id}' \\
      --online-decision-time '${online_decision_time}' \\
      --online-static-prediction-mb ${static_prediction_mb} \\
      --online-history-prediction-mb ${history_prediction_mb} \\
      --online-allocation-mb ${predicted_memory_mb} \\
      --online-model-version '${online_model_version}' \\
      --online-system-config-id '${params.online_system_config_id}' \\
      --result-dir ${sample_id}.scatter_${scatter_id}
    """
}

process MERGE_TASK_METRICS {
    tag 'merge_task_metrics'
    cache false
    publishDir "${params.outdir}/metrics", mode: 'copy', overwrite: true

    input:
    path task_results

    output:
    path 'haplotypecaller_task_metrics.tsv'
    path 'haplotypecaller_task_metrics.csv'
    path 'model_training_haplotypecaller_task_metrics.tsv'

    script:
    """
    python3 ${projectDir}/bin/merge_task_metrics.py \\
      --task-root . \\
      --out-tsv haplotypecaller_task_metrics.tsv \\
      --out-csv haplotypecaller_task_metrics.csv \\
      --out-training model_training_haplotypecaller_task_metrics.tsv
    """
}

workflow {
    if (!(params.instrumentation in ['none', 'strace', 'ebpf', 'selective'])) {
        error "--instrumentation must be none, strace, ebpf, or selective"
    }
    if (!file(params.reference).exists()) {
        error "Reference not found: ${params.reference}"
    }
    if (!file(params.samples).exists()) {
        error "Sample manifest not found: ${params.samples}"
    }
    if (!file(params.intervals).exists()) {
        error "Interval manifest not found: ${params.intervals}"
    }
    if (!file(params.gatk_jar).exists()) {
        error "GATK jar not found: ${params.gatk_jar}"
    }
    if (params.instrumentation == 'selective' && (!params.task_plan || !file(params.task_plan).exists())) {
        error "Selective mode requires an existing --task_plan CSV"
    }

    reference_input = Channel.fromPath(params.reference, checkIfExists: true)
    samples_input = Channel.fromPath(params.samples, checkIfExists: true)
    intervals_input = Channel.fromPath(params.intervals, checkIfExists: true)

    sample_rows = Channel
        .fromPath(params.samples, checkIfExists: true)
        .splitCsv(header: true, sep: '\t')
        .map { row -> tuple(row.sample_id, row.coverage as Integer, row.seed as Integer) }

    interval_rows = Channel
        .fromPath(params.intervals, checkIfExists: true)
        .splitCsv(header: true, sep: '\t')
        .map { row -> tuple(row.scatter_id, row.chrom, row.start as Integer, row.end as Integer, row.contig_length as Integer, row.has_str_region) }

    PREPARE_REFERENCE(reference_input)
    BUILD_BWA_INDEX(PREPARE_REFERENCE.out.bundle)
    STATIC_MANIFEST(samples_input, intervals_input, reference_input)

    sample_with_reference = sample_rows.combine(PREPARE_REFERENCE.out.bundle)
    SIMULATE_FASTQ(sample_with_reference)

    reads_with_index = SIMULATE_FASTQ.out.reads.combine(BUILD_BWA_INDEX.out.index)
    ALIGN_AND_INDEX(reads_with_index)

    scatter_tasks = ALIGN_AND_INDEX.out.bam.combine(interval_rows)
    keyed_scatter = scatter_tasks.map {
        sample_id, coverage, seed, bam, bai, scatter_id, chrom, start, end,
        contig_length, has_str_region ->
        tuple(
            "${sample_id}.scatter_${scatter_id}",
            sample_id, coverage, seed, bam, bai, scatter_id, chrom, start, end,
            contig_length, has_str_region
        )
    }
    if (params.online_memory_plan) {
        memory_plan = Channel
            .fromPath(params.online_memory_plan, checkIfExists: true)
            .splitCsv(header: true, sep: '\t')
            .map { row ->
                tuple(
                    row.task_instance,
                    row.recommended_allocation_mb as Integer,
                    row.static_prediction_mb as Double,
                    row.history_prediction_mb as Double,
                    row.model_version,
                    row.logical_task_id,
                    row.decision_time
                )
            }
        planned_scatter = keyed_scatter.join(memory_plan).map {
            task_instance, sample_id, coverage, seed, bam, bai, scatter_id,
            chrom, start, end, contig_length, has_str_region,
            allocation_mb, static_mb, history_mb, model_version,
            logical_task_id, decision_time ->
            tuple(
                sample_id, coverage, seed, bam, bai, scatter_id, chrom, start,
                end, contig_length, has_str_region, allocation_mb, static_mb,
                history_mb, model_version, logical_task_id, decision_time
            )
        }
    } else {
        planned_scatter = keyed_scatter.map {
            task_instance, sample_id, coverage, seed, bam, bai, scatter_id,
            chrom, start, end, contig_length, has_str_region ->
            tuple(
                sample_id, coverage, seed, bam, bai, scatter_id, chrom, start,
                end, contig_length, has_str_region, 4096, 0.0, 0.0,
                "static-4gb", "", ""
            )
        }
    }
    scatter_with_reference = planned_scatter.combine(PREPARE_REFERENCE.out.bundle)
    HAPLOTYPECALLER(scatter_with_reference)

    MERGE_TASK_METRICS(HAPLOTYPECALLER.out.task_result.collect())
}
