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
#
# One of the most delicate parts of this script is the main "case" (switch) statement.
# It decides what to do by matching the SSH_ORIGINAL_COMMAND against its patterns.
#
# Note that we could want to deploy Janeway or one of the other 4
# projects (wjs-profile, wjs-submission, wjs-themes and wjs-search) onto 4
# "standard" instances (prod, pre-prod, test, dev). We thus have 5x4 = 20
# patterns.
#
# Also note that for test and dev instances, we want to allow for the deployment
# of arbitrary branches.
#
# The patterns of the case statement are thus in this form:
# - deploy-<INSTANCE>-<PROJECT>[<OPTIONAL COMMIT SHA>]
#
#
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
WJS_SERVICE=gunicorn.service

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

function manage_setup() {
    # Common management commands run after deployment
    # Should be called from within deploy_* functions
    cd "$MANAGE_DIR"
    "$PYTHON" -mmanage migrate
    "$PYTHON" -mmanage sync_translation_fields --noinput
    "$PYTHON" -mmanage collectstatic --noinput
    "$PYTHON" -mmanage compilemessages --settings core.settings
    "$PYTHON" -mmanage build_assets
    systemctl --user restart "$WJS_SERVICE"
    systemctl --user restart "$QCLUSTER_SERVICE"
}

function deploy_janeway() {
    set_derivable_variables
    echo "Deploying branch $JANEWAY_BRANCH into $JANEWAY_ROOT"
    cd "$JANEWAY_ROOT"
    git pull --ff-only https://"${DEPLOY_TOKEN_USER}":"${DEPLOY_TOKEN_PASSWORD}"@gitlab.sissamedialab.it/wjs/janeway.git $JANEWAY_BRANCH
    "$PIP" install -r requirements.txt -c constraints.txt

    cd "$MANAGE_DIR"
    "$PYTHON" -mmanage load_default_settings
    manage_setup
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
    manage_setup
}

function deploy_submission() {
    set_derivable_variables

    # If given, the first argument to this function will be used to pip install the pacakge.
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

    manage_setup
}

function deploy_themes() {
    set_derivable_variables

    # If given, the first argument to this function will be used to pip install the pacakge.
    if [[ -n "$1" ]]; then
        "$PIP" uninstall --yes wjs-themes
        "$PIP" install --no-cache-dir "$1"
    else
        if [[ -z "$PIP_PRE" ]]
        then
            "$PIP" install -U wjs-themes
        else
            "$PIP" install --pre -U wjs-themes
        fi
    fi

    manage_setup
}

function deploy_search() {
    set_derivable_variables

    # If given, the first argument to this function will be used to pip install the pacakge.
    if [[ -n "$1" ]]; then
        "$PIP" uninstall --yes wjs-user-search
        "$PIP" install --no-cache-dir "$1"
    else
        if [[ -z "$PIP_PRE" ]]
        then
            "$PIP" install -U wjs-user-search
        else
            "$PIP" install --pre -U wjs-user-search
        fi
    fi

    manage_setup
}

function set_prod_variables() {
    JANEWAY_ROOT=/home/wjs/janeway
    VENV_BIN=/home/wjs/.virtualenvs/janeway/bin
    WJS_SERVICE="gunicorn.service"
    JANEWAY_BRANCH=wjs-production
    QCLUSTER_SERVICE="qcluster.service"
}

function set_pp_variables() {
    JANEWAY_ROOT=/home/wjs/janeway-pp
    VENV_BIN=/home/wjs/.virtualenvs/janeway-pp/bin
    WJS_SERVICE="gunicorn-pp.service"
    JANEWAY_BRANCH=wjs-production
    # Permit install pre-release pkgs in pre-prod
    # this allows us to test pkg install when needed.
    PIP_PRE="yes please"
    QCLUSTER_SERVICE="qcluster-pp.service"
}

function set_dev_variables() {
    JANEWAY_ROOT=/home/wjs/janeway-dev
    VENV_BIN=/home/wjs/.virtualenvs/janeway-dev/bin
    WJS_SERVICE="gunicorn-dev.service"
    JANEWAY_BRANCH=wjs-develop
    PIP_PRE="yes please"
    QCLUSTER_SERVICE="qcluster-dev.service"
}

function set_test_variables() {
    JANEWAY_ROOT=/home/wjs/janeway-test
    VENV_BIN=/home/wjs/.virtualenvs/janeway-test/bin
    WJS_SERVICE="gunicorn-test.service"
    JANEWAY_BRANCH=wjs-production
    PIP_PRE="yes please"
    QCLUSTER_SERVICE="qcluster-test.service"
}

shopt -s extglob
case "$SSH_ORIGINAL_COMMAND" in
    # ========================================
    # PRODUCTION INSTANCE
    # ========================================
    "deploy-prod-janeway")
        set_prod_variables
        deploy_janeway
        ;;
    "deploy-prod-wjs")
        set_prod_variables
        deploy_wjs
        ;;
    "deploy-prod-wjs-submission")
        set_prod_variables
        deploy_submission
        ;;
    "deploy-prod-wjs-themes")
        set_prod_variables
        deploy_themes
        ;;
    "deploy-prod-wjs-search")
        set_prod_variables
        deploy_search
        ;;

    # ========================================
    # PRE-PRODUCTION INSTANCE
    # ========================================
    "deploy-pp-janeway")
        set_pp_variables
        deploy_janeway
        ;;
    "deploy-pp-wjs")
        set_pp_variables
        deploy_wjs
        ;;
    "deploy-pp-wjs-submission")
        set_pp_variables
        deploy_submission
        ;;
    "deploy-pp-wjs-themes")
        set_pp_variables
        deploy_themes
        ;;
    "deploy-pp-wjs-search")
        set_pp_variables
        deploy_search
        ;;

    # ========================================
    # TEST INSTANCE
    # ========================================
    "deploy-test-janeway")
        set_test_variables
        deploy_janeway
        ;;
    "deploy-test-wjs")
        set_test_variables
        deploy_wjs
        ;;
    "deploy-test-wjs-submission")
        set_test_variables
        deploy_submission
        ;;
    "deploy-test-wjs-themes")
        set_test_variables
        deploy_themes
        ;;
    "deploy-test-wjs-search")
        set_test_variables
        deploy_search
        ;;
    # Test instance with specific tag/commit
    # Don't be too generous with the pattern here: watch out for sh injections!
    # Remember Bobby Tables https://xkcd.com/327/
    "deploy-test-janeway:"+([[:word:]]))
        set_test_variables
        TAGNAME=$(echo "$SSH_ORIGINAL_COMMAND"|sed 's/deploy-test-janeway://')
        echo "Installing janeway at ${TAGNAME}"
        JANEWAY_BRANCH="${TAGNAME}"
        deploy_janeway
        ;;
    "deploy-test-wjs:"+([[:word:]]))
        set_test_variables
        TAGNAME=$(echo "$SSH_ORIGINAL_COMMAND"|sed 's/deploy-test-wjs://')
        echo "Installing wjs.jcom_profile at ${TAGNAME}"
        deploy_wjs "git+https://${DEPLOY_TOKEN_USER}:${DEPLOY_TOKEN_PASSWORD}@gitlab.sissamedialab.it/wjs/wjs-profile-project@${TAGNAME}#egg=wjs.jcom_profile"
        ;;
    "deploy-test-wjs-submission:"+([[:word:]]))
        set_test_variables
        TAGNAME=$(echo "$SSH_ORIGINAL_COMMAND"|sed 's/deploy-test-wjs-submission://')
        echo "Installing wjs-submission at ${TAGNAME}"
        deploy_submission "git+https://${DEPLOY_TOKEN_USER}:${DEPLOY_TOKEN_PASSWORD}@gitlab.sissamedialab.it/wjs/wjs-submission-project@${TAGNAME}#egg=wjs-submission"
        ;;
    "deploy-test-wjs-themes:"+([[:word:]]))
        set_test_variables
        TAGNAME=$(echo "$SSH_ORIGINAL_COMMAND"|sed 's/deploy-test-wjs-themes://')
        echo "Installing wjs-themes at ${TAGNAME}"
        deploy_themes "git+https://${DEPLOY_TOKEN_USER}:${DEPLOY_TOKEN_PASSWORD}@gitlab.sissamedialab.it/wjs/wjs-themes@${TAGNAME}#egg=wjs-themes"
        ;;
    "deploy-test-wjs-search:"+([[:word:]]))
        set_test_variables
        TAGNAME=$(echo "$SSH_ORIGINAL_COMMAND"|sed 's/deploy-test-wjs-search://')
        echo "Installing wjs-search at ${TAGNAME}"
        deploy_search "git+https://${DEPLOY_TOKEN_USER}:${DEPLOY_TOKEN_PASSWORD}@gitlab.sissamedialab.it/wjs/wjs-user-search@${TAGNAME}#egg=wjs-user-search"
        ;;

    # ========================================
    # DEVELOPMENT INSTANCE
    # ========================================
    "deploy-dev-janeway")
        set_dev_variables
        deploy_janeway
        ;;
    "deploy-dev-wjs")
        set_dev_variables
        deploy_wjs
        ;;
    "deploy-dev-wjs-submission")
        set_dev_variables
        deploy_submission
        ;;
    "deploy-dev-wjs-themes")
        set_dev_variables
        deploy_themes
        ;;
    "deploy-dev-wjs-search")
        set_dev_variables
        deploy_search
        ;;
    # Development instance with specific tag/commit
    "deploy-dev-janeway:"+([[:word:]]))
        set_dev_variables
        TAGNAME=$(echo "$SSH_ORIGINAL_COMMAND"|sed 's/deploy-dev-janeway://')
        echo "Installing janeway at ${TAGNAME}"
        JANEWAY_BRANCH="${TAGNAME}"
        deploy_janeway
        ;;
    "deploy-dev-wjs:"+([[:word:]]))
        set_dev_variables
        TAGNAME=$(echo "$SSH_ORIGINAL_COMMAND"|sed 's/deploy-dev-wjs://')
        echo "Installing wjs.jcom_profile at ${TAGNAME}"
        deploy_wjs "git+https://${DEPLOY_TOKEN_USER}:${DEPLOY_TOKEN_PASSWORD}@gitlab.sissamedialab.it/wjs/wjs-profile-project@${TAGNAME}#egg=wjs.jcom_profile"
        ;;
    "deploy-dev-wjs-submission:"+([[:word:]]))
        set_dev_variables
        TAGNAME=$(echo "$SSH_ORIGINAL_COMMAND"|sed 's/deploy-dev-wjs-submission://')
        echo "Installing wjs-submission at ${TAGNAME}"
        deploy_submission "git+https://${DEPLOY_TOKEN_USER}:${DEPLOY_TOKEN_PASSWORD}@gitlab.sissamedialab.it/wjs/wjs-submission-project@${TAGNAME}#egg=wjs-submission"
        ;;
    "deploy-dev-wjs-themes:"+([[:word:]]))
        set_dev_variables
        TAGNAME=$(echo "$SSH_ORIGINAL_COMMAND"|sed 's/deploy-dev-wjs-themes://')
        echo "Installing wjs-themes at ${TAGNAME}"
        deploy_themes "git+https://${DEPLOY_TOKEN_USER}:${DEPLOY_TOKEN_PASSWORD}@gitlab.sissamedialab.it/wjs/wjs-themes@${TAGNAME}#egg=wjs-themes"
        ;;
    "deploy-dev-wjs-search:"+([[:word:]]))
        set_dev_variables
        TAGNAME=$(echo "$SSH_ORIGINAL_COMMAND"|sed 's/deploy-dev-wjs-search://')
        echo "Installing wjs-search at ${TAGNAME}"
        deploy_search "git+https://${DEPLOY_TOKEN_USER}:${DEPLOY_TOKEN_PASSWORD}@gitlab.sissamedialab.it/wjs/wjs-user-search@${TAGNAME}#egg=wjs-user-search"
        ;;

    *)
        echo "Unknown command $SSH_ORIGINAL_COMMAND"
        exit 1
        ;;
esac
