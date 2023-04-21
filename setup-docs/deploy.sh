#!/usr/bin/env bash

# This file is a template of the deploy procedure.
#
# WARNING: editing this file has no effect. The modified file must be
# manually installed to the destination servers by someone with access
# to the server.
#
# The idea is:
# - the WJS group on gitlab has a private key
# - the public key of the above is added to the authorized_keys of a server
# - the public key on the server has a ForceCommand that point to a copy of this file
#   see:
#       https://serverfault.com/a/749484 and https://serverfault.com/a/803873
#       http://man.openbsd.org/OpenBSD-current/man5/sshd_config.5#ForceCommand
#
# This file cannot be part of the deploy procedure for security reasons :)

set -e

# -- CONFIGURATION START --
# The path to the clone of the Janeway repos. This contains the `src` folder.
JANEWAY_ROOT=/home/wjs/janeway

# The path to the `bin` folder of the virtual env. This contains `python` and `pip`
VENV_BIN=/home/wjs/.virtualenvs/janeway-venv/bin

# The uwsgi vassal file to "touch" in order to reload the application server
UWSGI_VASSAL=/home/wjs/uwsgi/janeway.ini

# The user and password of the deploy token
DEPLOY_TOKEN_USER=***
DEPLOY_TOKEN_PASSWORD=***

# -- CONFIGURATION END --

PIP="${VENV_BIN}/pip"
PYTHON="${VENV_BIN}/python"
MANAGE_DIR="${JANEWAY_ROOT}/src"

case "$SSH_ORIGINAL_COMMAND" in
    "deploy-janeway")
        cd "$JANEWAY_ROOT"
        git pull --ff-only https://"${DEPLOY_TOKEN_USER}":"${DEPLOY_TOKEN_PASSWORD}"@gitlab.sissamedialab.it/wjs/janeway.git jcom
        cd "$MANAGE_DIR"
        "$PYTHON" manage.py migrate
        "$PYTHON" manage.py collectstatic --noinput
        "$PYTHON" manage.py compilemessages --settings core.settings
        ;;

    *)
        "$PIP" install -U "wjs.jcom-profile"
        "$PIP" install -U "jcomassistant"

        cd "$MANAGE_DIR"

        "$PYTHON" manage.py link_plugins
        "$PYTHON" manage.py install_themes

        "$PYTHON" manage.py migrate jcom_profile

        "$PYTHON" manage.py build_assets
        "$PYTHON" manage.py collectstatic --noinput

        "$PYTHON" manage.py add_coauthors_submission_email_settings
        "$PYTHON" manage.py add_generic_analytics_code_setting
        "$PYTHON" manage.py add_publication_alert_settings
        "$PYTHON" manage.py add_user_as_main_author_setting
        ;;
esac

touch --no-dereference "$UWSGI_VASSAL"
