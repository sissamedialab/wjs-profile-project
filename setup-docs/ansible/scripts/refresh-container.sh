#!/bin/bash

# Refresh a container by pulling the relative image and restarting it.
#
# To be saved in
# $HOME/.local/bin
#
# To be added to the PATH
# echo 'export PATH=$HOME/.local/bin:$PATH' >> $HOME/.bashrc
#
# To be run via user's cron:
# 26   6  *  *  * $HOME/.local/bin/refresh-container.sh registry.gitlab.sissamedialab.it/wjs/jcomassistant-project/jcomassistant:production jcomassistant 1234:8888 2>&1 > /dev/null  || echo "jcomassistant image refresh failed. Please check!"
# 36   6  *  *  * $HOME/.local/bin/refresh-container.sh registry.gitlab.sissamedialab.it/wjs/yakunin-project/yakunin:production yakunin 1235:8889 2>&1 > /dev/null  || echo "yakunin image refresh failed. Please check!"
#
# Prune dangling images
# 46   6  *  *  *  docker system prune -a -f > /dev/null || echo "docker system prune failed. Please check!"
#
# Email alias for user wjs must go to wjs-sysadmin
# as root:
# echo "wjs: wjs-sysadmin@medialab.sissa.it" >> /etc/aliases && newaliases

set -e

if [ $# -ne 3 ]; then
    echo "Error: Exactly 3 arguments are required."
    echo "Use as:"
    echo "  $0 registry.gitlab.sissamedialab.it/wjs/yakunin-project/yakunin:production yakunin 1235:8889"
    echo "or"
    echo "  $0 registry.gitlab.sissamedialab.it/wjs/jcomassistant-project/jcomassistant:production jcomassistant 1234:8888"
  exit 1
fi

tag="$1"
container_name="$2"
ports="$3"

docker pull --quiet "$tag" && \
    (docker stop "$container_name" || true) && \
    (docker container rm "$container_name" || true) && \
    docker run --quiet -p "$ports" --name $container_name -d --restart always  $tag
