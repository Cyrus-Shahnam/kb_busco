FROM kbase/sdkpython:3.8.0
LABEL maintainer="ac.shahnam"

USER root

# ---- system bits (slow, rarely changes) ------------------------------------
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl bzip2 ca-certificates zip \
 && rm -rf /var/lib/apt/lists/*

# Not bundled in sdkpython
RUN pip install --no-cache-dir jsonrpcbase

# ---- micromamba via RUN curl with fallback (ADD <url> ignores proxies) -----
ENV MAMBA_ROOT_PREFIX=/opt/micromamba
RUN ( curl -fsSL https://micro.mamba.pm/api/micromamba/linux-64/latest \
        | tar -xvj -C /usr/local bin/micromamba ) \
 || ( curl -fsSL https://github.com/mamba-org/micromamba-releases/releases/latest/download/micromamba-linux-64.tar.bz2 \
        | tar -xvj -C /usr/local bin/micromamba )

# ---- BUSCO env (the slow conda solve; keep it early) ----------------------
COPY env-busco.yml /tmp/env-busco.yml
RUN micromamba create -y -p /opt/conda/envs/busco -f /tmp/env-busco.yml \
 && micromamba clean -a -y

# Build check with the RUNTIME PATH scoped to this one RUN (BUSCO calls its
# helpers by bare name). Hard-fail on the ones every genome run needs.
RUN export PATH=/opt/conda/envs/busco/bin:$PATH \
 && busco --version \
 && for t in prodigal hmmsearch miniprot metaeuk tblastn stats.sh java; do \
        command -v "$t" >/dev/null || { echo "MISSING helper: $t"; exit 1; }; done \
 && for t in augustus run_sepp.py; do \
        command -v "$t" >/dev/null || echo "WARN optional helper not found: $t"; done \
 && ( micromamba list -p /opt/conda/envs/busco | grep -E '^\s*sepp\s' || true ) \
 && ( ls -d /opt/conda/envs/busco/config/species >/dev/null 2>&1 \
        && echo "augustus config OK" || echo "WARN no augustus config dir" ) \
 && echo BUSCO_ENV_OK

# ---- volatile layers AFTER the conda env so they don't bust its cache -----
RUN pip install --no-cache-dir pytest pytest-cov coverage pyyaml jinja2

# Server python must be untouched by the conda env
RUN python -c "import sys; print(sys.executable)" && python -c "import pytest, jsonrpcbase, jinja2"

COPY ./ /kb/module
RUN mkdir -p /kb/module/work && chmod -R a+rw /kb/module
WORKDIR /kb/module

RUN make all

ENTRYPOINT [ "./scripts/entrypoint.sh" ]
CMD [ ]
