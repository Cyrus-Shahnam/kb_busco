/*
A KBase module: kb_busco

Wraps BUSCO v6.1.0 (https://gitlab.com/ezlab/busco) to assess genome assembly
completeness against near-universal single-copy orthologs (OrthoDB v12.2
lineage datasets). Genome mode on Assembly objects; output is a report.
*/
module kb_busco {

    /* A workspace reference to a KBaseGenomeAnnotations.Assembly.
       @id ws KBaseGenomeAnnotations.Assembly
    */
    typedef string assembly_ref;

    /*
    input_assembly_refs - one or more Assembly objects (required)
    workspace_name      - workspace for the report (required)
    lineage_mode        - "specific" | "auto" | "auto-prok" | "auto-euk"
    lineage_dataset     - e.g. "bacteria_odb12.2"; required when lineage_mode is "specific"
    euk_predictor       - "miniprot" (default) | "metaeuk" | "augustus"; eukaryotic lineages only
    contig_break        - Ns that split a scaffold into contigs for BBTools stats (default 10)
    evalue              - BLAST e-value cutoff (default 1e-3; BLAST pipelines only)
    limit               - candidate regions per BUSCO (default 3; BLAST pipelines only)
    skip_bbtools        - 1 to skip BBTools assembly statistics (default 0)

    The Narrative sends "" for untouched optional fields and numbers as strings;
    the runner coerces both.
    */
    typedef structure {
        list<assembly_ref> input_assembly_refs;
        string workspace_name;
        string lineage_mode;
        string lineage_dataset;
        string euk_predictor;
        int contig_break;
        float evalue;
        int limit;
        int skip_bbtools;
    } RunBuscoParams;

    /*
    busco_results - one compact summary per input assembly (counts, percentages,
                    selected lineage, warnings)
    */
    typedef structure {
        string report_name;
        string report_ref;
        list<UnspecifiedObject> busco_results;
    } RunBuscoOutput;

    funcdef run_busco(RunBuscoParams params)
        returns (RunBuscoOutput output) authentication required;
};
