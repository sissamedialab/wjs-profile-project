#!/usr/bin/env bash

cd ../janeway/src
while inotifywait -r -e modify ../../wjs-profile-project/wjs/themes/JCOM-theme/assets; do
  python manage.py build_assets
done
