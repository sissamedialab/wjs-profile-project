#!/bin/bash
#
# Wrapper script to be linked in /etc/munin/plugins
#
# It will call the management command that counts the number of newsletter
# recipients and exposes it for consumption by munin.
#
# The following steps might be needed
#
# - link the wrapper script: `ln -s .../munin_count_recipients.sh /etc/munin/plugins/munin_count_recipients`
#
# - add an override to munin-node.service:
#   - systemctl edit munin-node.service
#     ```
#     [Service]
#     ProtectHome=false
#     ```
#   - systemctl daemon-reload
#
# - add configuration for munin-node (e.g. in /etc/munin/plugin-conf.d/zzz-medialab.conf):
#   ```
#   [count_recipients]
#   user wjs
#   group www-data
#   ```
#
# - restart munin-nodesystemctl restart  munin-node.service
#

set -e
/home/wjs/.virtualenvs/janeway/bin/python /home/wjs/janeway/src/manage.py munin_count_recipients "$@"
