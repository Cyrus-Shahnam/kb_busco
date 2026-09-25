#!/bin/bash

# sdkbase2 ships user-env.sh; sdkpython does not (lessons §3).
if [ -f /kb/deployment/user-env.sh ] ; then
  . /kb/deployment/user-env.sh
fi

python ./scripts/prepare_deploy_cfg.py ./deploy.cfg ./work/config.properties

if [ -f ./work/token ] ; then
  export KB_AUTH_TOKEN=$(<./work/token)
fi

if [ $# -eq 0 ] ; then
  sh ./scripts/start_server.sh
elif [ "${1}" = "test" ] ; then
  echo "Run Tests"
  make test
elif [ "${1}" = "async" ] ; then
  sh ./scripts/run_async.sh
elif [ "${1}" = "init" ] ; then
  # Runs once per data-version at registration, with /data writable.
  echo "Initialize module: downloading BUSCO lineage datasets into /data"
  if bash ./scripts/download_busco_data.sh /data ; then
    touch /data/__READY__
  else
    echo "Reference data download FAILED; /data/__READY__ not written"
    exit 1
  fi
elif [ "${1}" = "bash" ] ; then
  bash
elif [ "${1}" = "report" ] ; then
  export KB_SDK_COMPILE_REPORT_FILE=./work/compile_report.json
  make compile
else
  echo Unknown
fi
