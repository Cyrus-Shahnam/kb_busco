"""Render deploy.cfg (a jinja2 template) from work/config.properties or KBASE_ENDPOINT."""
import os
import sys
from configparser import ConfigParser

from jinja2 import Template

if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: <program> <deploy_cfg_template_file> <file_with_properties>")
        sys.exit(1)
    with open(sys.argv[1]) as fh:
        text = fh.read()
    t = Template(text)
    config = ConfigParser()
    if os.path.isfile(sys.argv[2]):
        config.read(sys.argv[2])
    elif "KBASE_ENDPOINT" in os.environ:
        ep = os.environ.get("KBASE_ENDPOINT")
        props = ("[global]\n"
                 "kbase_endpoint = " + ep + "\n"
                 "job_service_url = " + ep + "/userandjobstate\n"
                 "workspace_url = " + ep + "/ws\n"
                 "shock_url = " + ep + "/shock-api\n"
                 "handle_url = " + ep + "/handle_service\n"
                 "srv_wiz_url = " + ep + "/service_wizard\n"
                 "njsw_url = " + ep + "/njs_wrapper\n")
        if "AUTH_SERVICE_URL" in os.environ:
            props += "auth_service_url = " + os.environ.get("AUTH_SERVICE_URL") + "\n"
        elif "auth2services" in ep:
            props += "auth_service_url = " + ep + "/auth/api/legacy/KBase/Sessions/Login\n"
        props += ("auth_service_url_allow_insecure = "
                  + os.environ.get("AUTH_SERVICE_URL_ALLOW_INSECURE", "false") + "\n")
        for key in os.environ:
            if key.startswith("KBASE_SECURE_CONFIG_PARAM_"):
                props += key[len("KBASE_SECURE_CONFIG_PARAM_"):] + " = " + os.environ.get(key) + "\n"
        config.read_string(props)
    else:
        raise ValueError("Neither " + sys.argv[2] + " file nor KBASE_ENDPOINT env-variable found")
    output = t.render(dict(config.items("global")))
    with open(sys.argv[1] + ".orig", "w") as fh:
        fh.write(text)
    with open(sys.argv[1], "w") as fh:
        fh.write(output)
