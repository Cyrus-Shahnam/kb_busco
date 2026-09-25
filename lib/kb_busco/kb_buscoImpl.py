# -*- coding: utf-8 -*-
#BEGIN_HEADER
import logging
import os

from kb_busco.busco_utils import BuscoRunner
#END_HEADER


class kb_busco:
    '''
    Module Name:
    kb_busco

    Module Description:
    A KBase module: kb_busco

Wraps BUSCO v6.1.0 (https://gitlab.com/ezlab/busco) to assess genome assembly
completeness against near-universal single-copy orthologs (OrthoDB v12.2
lineage datasets). Genome mode on Assembly objects; output is a report.
    '''

    ######## WARNING FOR GEVENT USERS ####### noqa
    # Since asynchronous IO can lead to methods - even the same method -
    # interrupting each other, you must be *very* careful when using global
    # state. A method could easily clobber the state set by another while
    # the latter method is running.
    ######################################### noqa
    VERSION = "0.1.0"
    GIT_URL = ""
    GIT_COMMIT_HASH = ""

    #BEGIN_CLASS_HEADER
    #END_CLASS_HEADER

    # config contains contents of config file in a hash or None if it couldn't
    # be found
    def __init__(self, config):
        #BEGIN_CONSTRUCTOR
        self.callback_url = os.environ['SDK_CALLBACK_URL']
        self.shared_folder = config['scratch']
        logging.basicConfig(format='%(created)s %(levelname)s: %(message)s',
                            level=logging.INFO)
        #END_CONSTRUCTOR
        pass


    def run_busco(self, ctx, params):
        """
        :param params: instance of type "RunBuscoParams" (input_assembly_refs
           - one or more Assembly objects (required) workspace_name      -
           workspace for the report (required) lineage_mode        -
           "specific" | "auto" | "auto-prok" | "auto-euk" lineage_dataset    
           - e.g. "bacteria_odb12.2"; required when lineage_mode is
           "specific" euk_predictor       - "miniprot" (default) | "metaeuk"
           | "augustus"; eukaryotic lineages only contig_break        - Ns
           that split a scaffold into contigs for BBTools stats (default 10)
           evalue              - BLAST e-value cutoff (default 1e-3; BLAST
           pipelines only) limit               - candidate regions per BUSCO
           (default 3; BLAST pipelines only) skip_bbtools        - 1 to skip
           BBTools assembly statistics (default 0) The Narrative sends "" for
           untouched optional fields and numbers as strings; the runner
           coerces both.) -> structure: parameter "input_assembly_refs" of
           list of type "assembly_ref" (A workspace reference to a
           KBaseGenomeAnnotations.Assembly. @id ws
           KBaseGenomeAnnotations.Assembly), parameter "workspace_name" of
           String, parameter "lineage_mode" of String, parameter
           "lineage_dataset" of String, parameter "euk_predictor" of String,
           parameter "contig_break" of Long, parameter "evalue" of Double,
           parameter "limit" of Long, parameter "skip_bbtools" of Long
        :returns: instance of type "RunBuscoOutput" (busco_results - one
           compact summary per input assembly (counts, percentages, selected
           lineage, warnings)) -> structure: parameter "report_name" of
           String, parameter "report_ref" of String, parameter
           "busco_results" of list of unspecified object
        """
        # ctx is the context object
        # return variables are: output
        #BEGIN run_busco
        output = BuscoRunner(self.callback_url, self.shared_folder).run(params)
        #END run_busco

        # At some point might do deeper type checking...
        if not isinstance(output, dict):
            raise ValueError('Method run_busco return value ' +
                             'output is not type dict as required.')
        # return the results
        return [output]
    def status(self, ctx):
        #BEGIN_STATUS
        returnVal = {'state': "OK",
                     'message': "",
                     'version': self.VERSION,
                     'git_url': self.GIT_URL,
                     'git_commit_hash': self.GIT_COMMIT_HASH}
        #END_STATUS
        return [returnVal]
