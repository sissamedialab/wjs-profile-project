#!/bin/bash
# Try to mimic production env.
#
# Please run this script just after resetting the DB
#
# We have not yet installed the wjs_review plugin in production.
# - package wjs_mgmt_cmds has references to the plugin, so we uninstall it
# - we unlink the plugin (check the path: define PLUGIN_PATH to customize it)

set -e

export JANEWAY_SETTINGS_MODULE=core.settings.nodebug
# Write a module with something like the following:
#
# # src/core/settings/nodebug.py
# from core.settings import *  # noqa
# DEBUG = False
#


pip show wjs_mgmt_cmds >/dev/null 2>&1  && wjs_utils="installed" || wjs_utils="not installed"
if [[ "$wjs_utils" == "installed" ]]
then
    echo "$(tput setaf 1)Warning: wjs-utils is installed. Uninstalling....$(tput sgr0)"
    pip uninstall wjs_mgmt_cmds
fi

wjs_review_plugin=${PLUGIN_PATH:-$HOME/janeway/src/plugins/wjs_review}
if [[ -L "$wjs_review_plugin" ]]
then rm "$wjs_review_plugin"
else echo "$(tput setaf 1)Warning: $wjs_review_plugin missing or not a link. Might be ok, but please check!$(tput sgr0)"
fi

echo "$(tput setaf 8)create_custom_settings ...$(tput sgr0)" && python -mmanage create_custom_settings
echo "$(tput setaf 8)link_plugins ...$(tput sgr0)" && python -mmanage link_plugins
echo "$(tput setaf 8)install_themes ...$(tput sgr0)" && python -mmanage install_themes
echo "$(tput setaf 8)migrate ...$(tput sgr0)" && python -mmanage migrate
echo "$(tput setaf 8)sync_translation_fields ...$(tput sgr0)" && python -mmanage sync_translation_fields --noinput
echo "$(tput setaf 8)build_assets ...$(tput sgr0)" && python -mmanage build_assets
