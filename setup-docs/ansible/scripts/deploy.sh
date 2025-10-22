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
VENV_BIN=/home/wjs/.virtualenvs/janeway/bin

# The WJS systemd unit to restart
WJS_SERVICE=daphne.service

# The git branches where the code lives
JANEWAY_BRANCH=wjs-develop

# The name of the qcluster systemd unit
QCLUSTER_SERVICE="qcluster.service"

# The token name and value used to "pull" git repos.
# When it expires, create a new token at the WJS group level, with scope "read-repo"
# (and role "Reporter", probably useless...)
DEPLOY_TOKEN_USER=***
DEPLOY_TOKEN_PASSWORD=***

# When this is set (to any non-zero-lenght string), add `--pre` to `pip install wjs`
PIP_PRE=""
# -- CONFIGURATION DEFAULTS END --

function set_derivable_variables() {
    PIP="${VENV_BIN}/pip"
    PYTHON="${VENV_BIN}/python"
    MANAGE_DIR="${JANEWAY_ROOT}/src"
}

function deploy_janeway() {
    set_derivable_variables
    echo "Deploying branch $JANEWAY_BRANCH into $JANEWAY_ROOT"
    cd "$JANEWAY_ROOT"
    git pull --ff-only https://"${DEPLOY_TOKEN_USER}":"${DEPLOY_TOKEN_PASSWORD}"@gitlab.sissamedialab.it/wjs/janeway.git $JANEWAY_BRANCH
    "$PIP" install -r requirements.txt -c constraints.txt
    # TODO: might want to `pip install wjs.jcom-profile` to allow for newer packages from wjs
    cd "$MANAGE_DIR"
    "$PYTHON" -mmanage migrate
    "$PYTHON" -mmanage sync_translation_fields --noinput
    "$PYTHON" -mmanage load_default_settings
    "$PYTHON" -mmanage collectstatic --noinput
    "$PYTHON" -mmanage compilemessages --settings core.settings

    systemctl --user restart "$WJS_SERVICE"
    systemctl --user restart "$QCLUSTER_SERVICE"
}

function deploy_wjs() {
    set_derivable_variables

    # If given, the first argument to this function will be used to pip install the pacakge.
    # It should be in the form such as
    # "git+https://${DEPLOY_TOKEN_USER}:${DEPLOY_TOKEN_PASSWORD}@gitlab.sissamedialab.it/wjs/wjs-profile-project@${TAGNAME}#egg=wjs.jcom_profile"
    if [[ -n "$1" ]]; then
        "$PIP" uninstall --yes wjs.jcom_profile
        "$PIP" install --no-cache-dir "$1"
    else
        if [[ -z "$PIP_PRE" ]]
        then
            "$PIP" install -U wjs.jcom_profile
        else
            "$PIP" install --pre -U wjs.jcom_profile
        fi
    fi

    cd "$MANAGE_DIR"

    "$PYTHON" -mmanage run_customizations

    "$PYTHON" -mmanage migrate
    "$PYTHON" -mmanage sync_translation_fields --noinput

    "$PYTHON" -mmanage build_assets
    "$PYTHON" -mmanage collectstatic --noinput

    systemctl --user restart "$WJS_SERVICE"
    systemctl --user restart "$QCLUSTER_SERVICE"
}

function deploy_submission() {
    set_derivable_variables

    # If given, the first argument to this function will be used to pip install the pacakge.
    # It should be in the form such as
    # "git+https://${DEPLOY_TOKEN_USER}:${DEPLOY_TOKEN_PASSWORD}@gitlab.sissamedialab.it/wjs/wjs-profile-project@${TAGNAME}#egg=wjs-submission"
    if [[ -n "$1" ]]; then
        "$PIP" uninstall --yes wjs_submission
        "$PIP" install --no-cache-dir "$1"
    else
        if [[ -z "$PIP_PRE" ]]
        then
            "$PIP" install -U wjs_submission
        else
            "$PIP" install --pre -U wjs_submission
        fi
    fi

    # Do _not_ install jcomassistant. Since May '24 it's a service.
    # No: "$PIP" install -U "jcomassistant"

    cd "$MANAGE_DIR"

    # "$PYTHON" -mmanage run_customizations

    "$PYTHON" -mmanage migrate
    "$PYTHON" -mmanage sync_translation_fields --noinput

    "$PYTHON" -mmanage build_assets
    "$PYTHON" -mmanage collectstatic --noinput

    systemctl --user restart "$WJS_SERVICE"
    systemctl --user restart "$QCLUSTER_SERVICE"
}

function set_prod_variables() {
    JANEWAY_ROOT=/home/wjs/janeway
    VENV_BIN=/home/wjs/.virtualenvs/janeway-venv/bin
    WJS_SERVICE="daphne.service"
    JANEWAY_BRANCH=wjs-production
    QCLUSTER_SERVICE="qcluster.service"
}

function set_pp_variables() {
    JANEWAY_ROOT=/home/wjs/janeway-pp
    VENV_BIN=/home/wjs/.virtualenvs/janeway-pp/bin
    WJS_SERVICE="daphne-pp.service"
    JANEWAY_BRANCH=wjs-production
    # Permit install pre-release pkgs in pre-prod
    # this allows us to test pkg install when needed.
    PIP_PRE="yes please"
    QCLUSTER_SERVICE="qcluster-pp.service"
}

function set_dev_variables() {
    JANEWAY_ROOT=/home/wjs/janeway-dev
    VENV_BIN=/home/wjs/.virtualenvs/janeway-dev/bin
    WJS_SERVICE="daphne-dev.service"
    JANEWAY_BRANCH=wjs-develop
    PIP_PRE="yes please"
    QCLUSTER_SERVICE="qcluster-dev.service"
}

function set_test_variables() {
    JANEWAY_ROOT=/home/wjs/janeway-test
    VENV_BIN=/home/wjs/.virtualenvs/janeway-test/bin
    WJS_SERVICE="daphne-test.service"
    JANEWAY_BRANCH=wjs-production
    PIP_PRE="yes please"
    QCLUSTER_SERVICE="qcluster-test.service"
}

shopt -s extglob
case "$SSH_ORIGINAL_COMMAND" in
    # Production
    "deploy-prod-janeway")
        set_prod_variables
        deploy_janeway
        ;;
    "deploy-prod-wjs")
        set_prod_variables
        deploy_wjs
        ;;
    # Pre-production
    "deploy-pp-janeway")
        set_pp_variables
        deploy_janeway
        ;;
    "deploy-pp-wjs" | "deploy")
        # TODO: drop the "deploy" pattern when dropping "master" branch
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
    # Install a given tag on dev:
    # Don't be too generous with the pattern here: watch out for sh injections!
    # Remember Bobby Tables https://xkcd.com/327/
    "deploy-dev-wjs:"+([[:word:]]))
        set_dev_variables
        TAGNAME=$(echo "$SSH_ORIGINAL_COMMAND"|sed 's/deploy-dev-wjs://')
        echo "Installing wjs.jcom_profile at ${TAGNAME}"
        # temporary workaround: pull latest changes from wjs-themes and wjs-submission also
        # set_derivable_variables
        # "$PIP" uninstall --yes wjs-submission && "$PIP" install --no-cache-dir "git+https://${DEPLOY_TOKEN_USER}:${DEPLOY_TOKEN_PASSWORD}@gitlab.sissamedialab.it/wjs/wjs-submission-project"
        # "$PIP" uninstall --yes wjs-themes && "$PIP" install --no-cache-dir "git+https://${DEPLOY_TOKEN_USER}:${DEPLOY_TOKEN_PASSWORD}@gitlab.sissamedialab.it/wjs/wjs-themes"

        deploy_wjs "git+https://${DEPLOY_TOKEN_USER}:${DEPLOY_TOKEN_PASSWORD}@gitlab.sissamedialab.it/wjs/wjs-profile-project@${TAGNAME}#egg=wjs.jcom_profile"
        ;;
    # Test
    "deploy-test-janeway")
        set_test_variables
        deploy_janeway
        ;;
    # Install a given tag on test:
    # Don't be too generous with the pattern here: watch out for sh injections!
    # Remember Bobby Tables https://xkcd.com/327/
    "deploy-test-wjs:"+([[:word:]]))
        set_test_variables
        TAGNAME=$(echo "$SSH_ORIGINAL_COMMAND"|sed 's/deploy-test-wjs://')
        echo "Installing wjs.jcom_profile at ${TAGNAME}"
        deploy_wjs "git+https://${DEPLOY_TOKEN_USER}:${DEPLOY_TOKEN_PASSWORD}@gitlab.sissamedialab.it/wjs/wjs-profile-project@${TAGNAME}#egg=wjs.jcom_profile"
        ;;
    "deploy-dev-wjs-submission:"+([[:word:]]))
        set_dev_variables
        TAGNAME=$(echo "$SSH_ORIGINAL_COMMAND"|sed 's/deploy-dev-wjs-submission://')
        echo "Installing wjs-submission at ${TAGNAME}"
        set_derivable_variables

        deploy_submission "git+https://${DEPLOY_TOKEN_USER}:${DEPLOY_TOKEN_PASSWORD}@gitlab.sissamedialab.it/wjs/wjs-submission-project@${TAGNAME}#egg=wjs-submission"
        ;;
    *)
        echo "Unknown command $SSH_ORIGINAL_COMMAND"
        exit 1
        ;;
esac
