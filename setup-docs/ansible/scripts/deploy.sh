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
# The script decides what to do by matching the SSH_ORIGINAL_COMMAND
# against the following grammar:
#
#     deploy-<INSTANCE>-<PACKAGE>[:<SHA>]
#
# where
# - <INSTANCE> is one of: prod pp dev t1 t2 t3 t4 t5
# - <PACKAGE>  is one of: janeway hydra profile submission themes search
# - <SHA> is a commit SHA (or tag), required for t1-t5, forbidden elsewhere
#
# The deploy matrix is:
# - prod / pp: janeway is `git pull`ed from the wjs-production branch (only);
#   the other packages are `pip install -U`ed from the package registry
# - dev: janeway is `git pull`ed from the wjs-develop branch (only);
#   the other packages are pip-installed from git at wjs-develop
# - t1-t5: like dev, but the caller must provide the commit SHA to deploy
#
# `hydra` is special: it is a Janeway plugin that is not distributed as a python
# package, so it is `git pull`ed into src/plugins like janeway itself.
#
# Examples:
# - deploy-pp-janeway --> deploy Janeway on the pre-production instance
# - deploy-prod-profile --> deploy wjs.jcom_profile on production
# - deploy-t3-themes:0123abc --> deploy wjs-themes at commit 0123abc on t3

set -e

# -- CONFIGURATION DEFAULTS START --
# The token name and value used to "pull" git repos.
# When it expires, create a new token at the WJS group level, with scope "read-repo"
# (and role "Reporter", probably useless...)
DEPLOY_TOKEN_USER=***
DEPLOY_TOKEN_PASSWORD=***

# The gitlab host serving the wjs group's repos and package registry
GITLAB_HOST=gitlab.sissamedialab.it
# -- CONFIGURATION DEFAULTS END --

function parse_command() {
    # Match the given command against the deploy grammar
    # and set INSTANCE, PACKAGE and REF (possibly empty).
    #
    # Don't be too generous with the pattern here: watch out for sh injections!
    # Remember Bobby Tables https://xkcd.com/327/
    # ([[:alnum:]_] is enough for a SHA or a tag, and REF is only ever used quoted)
    if [[ "$1" =~ ^deploy-(prod|pp|dev|t[1-5])-(janeway|hydra|profile|submission|themes|search)(:([[:alnum:]_]+))?$ ]]; then
        INSTANCE="${BASH_REMATCH[1]}"
        PACKAGE="${BASH_REMATCH[2]}"
        REF="${BASH_REMATCH[4]}"
    else
        echo "Unknown command $1"
        exit 1
    fi
}

function set_instance_variables() {
    # Derive all instance-specific variables from $INSTANCE
    # and validate the presence/absence of $REF.
    case "$INSTANCE" in
        t[1-5])
            if [[ -z "$REF" ]]; then
                echo "Instance $INSTANCE requires a commit SHA: deploy-${INSTANCE}-${PACKAGE}:<SHA>"
                exit 1
            fi
            ;;
        *)
            if [[ -n "$REF" ]]; then
                echo "Instance $INSTANCE does not accept a ref (got \"$REF\")"
                exit 1
            fi
            ;;
    esac

    # MODE says how the non-janeway packages are installed:
    # - "release": pip install from the package registry
    # - "git": pip install from a git checkout at GIT_REF
    # GIT_REF is also the ref used to `git pull` janeway.
    case "$INSTANCE" in
        prod)
            SUFFIX=""
            MODE=release
            GIT_REF=wjs-production
            ;;
        pp)
            SUFFIX="-pp"
            MODE=release
            GIT_REF=wjs-production
            ;;
        dev)
            SUFFIX="-dev"
            MODE=git
            GIT_REF=wjs-develop
            ;;
        t[1-5])
            SUFFIX="-${INSTANCE}"
            MODE=git
            GIT_REF="$REF"
            ;;
    esac

    # The path to the clone of the Janeway repo. This contains the `src` folder.
    JANEWAY_ROOT="/home/wjs/janeway${SUFFIX}"
    # The path to the `bin` folder of the virtual env. This contains `python` and `pip`
    VENV_BIN="/home/wjs/.virtualenvs/janeway${SUFFIX}/bin"
    # The systemd units to restart
    WJS_SERVICE="gunicorn${SUFFIX}.service"
    QCLUSTER_SERVICE="qcluster${SUFFIX}.service"

    PIP="${VENV_BIN}/pip"
    PYTHON="${VENV_BIN}/python"
    MANAGE_DIR="${JANEWAY_ROOT}/src"
    # Janeway plugins that are not python packages live inside the Janeway checkout
    HYDRA_ROOT="${MANAGE_DIR}/plugins/hydra"
}

function set_package_variables() {
    # Per-package data (janeway is special, see deploy_janeway):
    # - PIP_NAME: the name used to pip install/uninstall the package
    # - REPO: the repo under ${GITLAB_HOST}/wjs/
    # - EGG: the egg name used when pip-installing from git
    # - POST_MANAGE: package-specific manage command, run before manage_setup
    case "$PACKAGE" in
        profile)
            PIP_NAME=wjs.jcom_profile
            REPO=wjs-profile-project
            EGG=wjs.jcom_profile
            POST_MANAGE=run_customizations
            ;;
        submission)
            PIP_NAME=wjs_submission
            REPO=wjs-submission-project
            EGG=wjs-submission
            POST_MANAGE=""
            ;;
        themes)
            PIP_NAME=wjs-themes
            REPO=wjs-themes
            EGG=wjs-themes
            POST_MANAGE=install_themes
            ;;
        search)
            # 😢 why?! why did I set the package name different from the repo name???
            PIP_NAME=wjs-user-search
            REPO=wjs-search-user
            EGG=wjs-user-search
            POST_MANAGE=""
            ;;
    esac
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
    echo "Deploying janeway at ${GIT_REF} into ${JANEWAY_ROOT}"
    cd "$JANEWAY_ROOT"
    git pull --ff-only "https://${DEPLOY_TOKEN_USER}:${DEPLOY_TOKEN_PASSWORD}@${GITLAB_HOST}/wjs/janeway.git" "$GIT_REF"
    "$PIP" install -r requirements.txt -c constraints.txt

    cd "$MANAGE_DIR"
    "$PYTHON" -mmanage load_default_settings
    manage_setup
}

function deploy_hydra() {
    # Hydra is a Janeway plugin, not a python package: it is cloned into
    # ${MANAGE_DIR}/plugins, where Janeway finds it (see core.plugin_installed_apps).
    # For prod/pp, GIT_REF is wjs-production; for dev it is wjs-develop; for t1-t5 it
    # is the SHA given by the caller.
    echo "Deploying hydra at ${GIT_REF} into ${HYDRA_ROOT}"
    cd "$HYDRA_ROOT"
    git pull --ff-only "https://${DEPLOY_TOKEN_USER}:${DEPLOY_TOKEN_PASSWORD}@${GITLAB_HOST}/wjs/hydra.git" "$GIT_REF"

    cd "$MANAGE_DIR"
    "$PYTHON" -mmanage install_plugins hydra
    manage_setup
}

function deploy_package() {
    if [[ "$MODE" == "git" ]]; then
        echo "Installing ${PIP_NAME} from ${REPO} at ${GIT_REF}"
        "$PIP" uninstall --yes "$PIP_NAME"
        "$PIP" install --no-cache-dir "git+https://${DEPLOY_TOKEN_USER}:${DEPLOY_TOKEN_PASSWORD}@${GITLAB_HOST}/wjs/${REPO}@${GIT_REF}#egg=${EGG}"
    else
        echo "Installing latest release of ${PIP_NAME}"
        "$PIP" install -U "$PIP_NAME"
    fi

    if [[ -n "$POST_MANAGE" ]]; then
        cd "$MANAGE_DIR"
        "$PYTHON" -mmanage "$POST_MANAGE"
    fi
    manage_setup
}

function main() {
    parse_command "$SSH_ORIGINAL_COMMAND"
    set_instance_variables
    if [[ "$PACKAGE" == "janeway" ]]; then
        deploy_janeway
    elif [[ "$PACKAGE" == "hydra" ]]; then
        deploy_hydra
    else
        set_package_variables
        deploy_package
    fi
}

# Run main only when executed, not when sourced
# (sourcing allows testing the parse/validate logic without deploying anything)
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main
fi
