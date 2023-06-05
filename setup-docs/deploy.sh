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

# We have a `switch` statement that knows how to deploy either Janeway
# or WJS in every instance.
#
# The SSH_ORIGINAL_COMMANDs have the form
# - deploy-pp-janeway --> to deploy Janeway on the pre-production instance
# - deploy-pp-wjs -->  to deploy WJS on the pre-production instance
# - deploy-dev-janeway ...

set -e

# -- CONFIGURATION DEFAULTS START --
# The path to the clone of the Janeway repos. This contains the `src` folder.
JANEWAY_ROOT=/home/wjs/janeway

# The path to the `bin` folder of the virtual env. This contains `python` and `pip`
VENV_BIN=/home/wjs/.virtualenvs/janeway-venv/bin

# The uwsgi vassal file to "touch" in order to reload the application server
UWSGI_VASSAL=/home/wjs/uwsgi/janeway.ini

# The git branches where the code lives
JANEWAY_BRANCH=jcom

# The user and password of the deploy token
DEPLOY_TOKEN_USER=***
DEPLOY_TOKEN_PASSWORD=***

# -- CONFIGURATION DEFAULTS END --

function set_derivable_variables() {
    PIP="${VENV_BIN}/pip"
    PYTHON="${VENV_BIN}/python"
    MANAGE_DIR="${JANEWAY_ROOT}/src"
}

function deploy_janeway() {
    set_derivable_variables
    cd "$JANEWAY_ROOT"
    git pull --ff-only https://"${DEPLOY_TOKEN_USER}":"${DEPLOY_TOKEN_PASSWORD}"@gitlab.sissamedialab.it/wjs/janeway.git $JANEWAY_BRANCH
    cd "$MANAGE_DIR"
    "$PYTHON" manage.py migrate
    "$PYTHON" manage.py collectstatic --noinput
    "$PYTHON" manage.py compilemessages --settings core.settings

    touch --no-dereference "$UWSGI_VASSAL"
}

function deploy_wjs() {
    set_derivable_variables

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

    touch --no-dereference "$UWSGI_VASSAL"
}

function set_prod_variables() {
    JANEWAY_ROOT=/home/wjs/janeway
    VENV_BIN=/home/wjs/.virtualenvs/janeway-venv/bin
    UWSGI_VASSAL=/home/wjs/uwsgi/janeway.ini
    JANEWAY_BRANCH=jcom
}

function set_pp_variables() {
    JANEWAY_ROOT=/home/wjs/janeway-pp
    VENV_BIN=/home/wjs/.virtualenvs/janeway-pp/bin
    UWSGI_VASSAL=/home/wjs/uwsgi/janeway-pp.ini
    JANEWAY_BRANCH=jcom
}

function set_dev_variables() {
    JANEWAY_ROOT=/home/wjs/janeway-dev
    VENV_BIN=/home/wjs/.virtualenvs/janeway-dev/bin
    UWSGI_VASSAL=/home/wjs/uwsgi/janeway-dev.ini
    JANEWAY_BRANCH=wjs-develop
    WJS_BRANCH=wjs-develop
}

case "$SSH_ORIGINAL_COMMAND" in
    # Production
    "deploy-prod-janeway")
        set_prod_variables
        deploy_janeway
        ;;
    "deploy-pp-wjs")
        set_prod_variables
        deploy_wjs
        ;;
    # Pre-production
    "deploy-pp-janeway")
        set_pp_variables
        deploy_janeway
        ;;
    "deploy-pp-wjs")
        set_pp_variables
        deploy_wjs
        ;;
    # Development
    "deploy-dev-janeway")
        set_dev_variables
        deploy_janeway
        ;;
    "deploy-dev-wjs")
        set_dev_variables
        deploy_wjs
        ;;
    # Test (?)
    "deploy-test-janeway")
        echo "Not implemented!"
        exit 1
    ;;
    "deploy-test-wjs")
        echo "Not implemented!"
        exit 1
    ;;
    *)
        echo "Unknown command $SSH_ORIGINAL_COMMAND"
        exit 1
        ;;
esac
