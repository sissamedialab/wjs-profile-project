#!/bin/bash

# Reset dev instance and copy everything from production to dev.

set -e

# output something only if variable "$DEBUG" is "1"
# echo in gray color
debug () {
    if test "$DEBUG" == "1"
    then
        >&2 echo $(tput setaf 8)"$@"$(tput sgr0)
    fi
}

# Delete all files from dev, and replace with files from production
j_dev=/home/wjs/janeway-dev
j_production=wjs@wjs-prod:janeway

# Warning: "--delete"...
rsync --archive --delete "$j_production/src/files/" "$j_dev/src/files/"
rsync --archive --delete "$j_production/src/media/" "$j_dev/src/media/"
debug "rsync from $j_production done"

# Assume postgresql credentials are in .pgpass!
# Also the catch-all entry for dropdb/createdb must be there :)

dev_db_user=janeway
dev_db_name=janeway-dev

# Dump also the dev db (just in case...)
dev_dump=/tmp/j_dev.sql
pg_dump -U "$dev_db_user" -h localhost --no-password "$dev_db_name" --clean --create --no-password --file="$dev_dump"
debug "$dev_dump dumped"

# Must manually dropdb and createdb because Janeway doesn't always use
# "on delete=CASCADE" and the drop from `pg_restore --clean` won't
# work.
dropdb -U "$dev_db_user" -h localhost --no-password "$dev_db_name"
createdb -U "$dev_db_user" -h localhost --no-password "$dev_db_name"


# Get the production db
production_dump=/tmp/j_production.sql
production_db_name=janeway
production_db_user=wjs_ro
production_db_host=wjs-prod

# only dump if no "recent" dump is present
# (useful during development)
ONEDAY=1440
# Please note that "find" will give no-error status even if no file meets the
# specs, so we check if the returned string is empty (which means that "find"
# did not find the file)
if [[ -n "$(find $production_dump -mmin -$ONEDAY 2> /dev/null)" ]] ;
then
    debug "Recent production DB dump already exists ($production_dump); skipping dump."
else
    # Don't use --create if you want to restore to a DB different from the
    # one you dumped, because it brings the DB name into the dump, so that
    # it is not possible to restore in any other DB
    # e.g. pg_dump ... --create ...
    # Not even with pg_restore (when using --format=custom)
    pg_dump --no-password --format=custom \
            -U "$production_db_user" -h "$production_db_host" "$production_db_name" \
            --file="$production_dump"
    debug "$production_dump dumped"
fi

# Restore the production schema/data into the dev DB
pg_restore --no-owner --no-password --exit-on-error --single-transaction \
           -U "$dev_db_user" -h localhost --dbname "$dev_db_name" \
           "$production_dump"
debug "$production_dump restored to local $dev_db_name"


# Fix press and journal domains (or get infinite redirects)
psql --quiet -U "$dev_db_user" -h localhost --no-password --dbname "$dev_db_name" <<EOF
update press_press set domain='wjs-test-dev-journals.wjapp.it';
update journal_journal set domain='wjs-test-dev-jcom.wjapp.it' where code='JCOM';
update journal_journal set domain='wjs-test-dev-jcomal.wjapp.it' where code='JCOMAL';
update journal_journal set domain='wjs-test-dev-jquant.wjapp.it' where code='JQUANT';
update journal_journal set domain='wjs-test-dev-jhep.wjapp.it' where code='JHEP';
update journal_journal set domain='wjs-test-dev-jcap.wjapp.it' where code='JCAP';
update journal_journal set domain='wjs-test-dev-jstat.wjapp.it' where code='JSTAT';
update journal_journal set domain='wjs-test-dev-jinst.wjapp.it' where code='JINST';

UPDATE core_settingvalue
SET
  value = 'on',
  value_en = 'on'
FROM core_setting s
WHERE
  s.name = 'crossref_test'
  AND core_settingvalue.setting_id = s.id
  AND core_settingvalue.journal_id IS NOT NULL
;

-- Ensure that we use "test" folders for Prophy - Obsolete!
UPDATE core_settingvalue
SET
  value = 'JCOM prova matteo bis',
  value_en = 'JCOM prova matteo bis'
FROM core_setting s
WHERE
  s.name = 'prophy_journal'
  AND core_settingvalue.setting_id = s.id
  AND core_settingvalue.journal_id = 1
;
UPDATE core_settingvalue
SET
  value = 'JCOMAL prova matteo bis',
  value_en = 'JCOMAL prova matteo bis'
FROM core_setting s
WHERE
  s.name = 'prophy_journal'
  AND core_settingvalue.setting_id = s.id
  AND core_settingvalue.journal_id = 2
;

EOF

VENV_PATH="/home/wjs/.virtualenvs/janeway-dev"
WJS_SUBMISSION_PATH="${VENV_PATH}/lib/python3.11/site-packages/wjs/plugins/wjs_submission"
PYTHON_BIN="${VENV_PATH}/bin/python"
# Load jquant fixtures overriding any values from production
${PYTHON_BIN} manage.py import_keywords_json ${WJS_SUBMISSION_PATH}/install/jhep-kwds.json --journal=JQUANT
${PYTHON_BIN} manage.py import_keywords_json ${WJS_SUBMISSION_PATH}/install/jhep-kwds.json --journal=JHEP
${PYTHON_BIN} manage.py install_plugins
${PYTHON_BIN} manage.py set_setting --journal=JCOM --group-name=wjs_submission --setting-name=arxiv_field_status --setting-value=disabled
${PYTHON_BIN} manage.py set_setting --journal=JCOM --group-name=submissionconfiguration --setting-name=autocomplete_keywords --setting-value=True
${PYTHON_BIN} manage.py set_setting --journal=JCOMAL --group-name=wjs_submission --setting-name=arxiv_field_status --setting-value=disabled
${PYTHON_BIN} manage.py set_setting --journal=JCOMAL --group-name=submissionconfiguration --setting-name=autocomplete_keywords --setting-value=True
${PYTHON_BIN} manage.py set_setting --journal=JQUANT --group-name=submissionconfiguration --setting-name=hierarchical_keywords --setting-value=True
${PYTHON_BIN} manage.py set_setting --journal=JQUANT --group-name=submissionconfiguration --setting-name=autocomplete_keywords --setting-value=True
${PYTHON_BIN} manage.py set_setting --journal=JQUANT --group-name=wjs_submission --setting-name=arxiv_field_status --setting-value=required
${PYTHON_BIN} manage.py set_setting --journal=JQUANT --group-name=submissionconfiguration --setting-name=hierarchical_keywords --setting-value=True
${PYTHON_BIN} manage.py set_setting --journal=JQUANT --group-name=submissionconfiguration --setting-name=autocomplete_keywords --setting-value=True
${PYTHON_BIN} manage.py set_setting --journal=JHEP --group-name=wjs_submission --setting-name=arxiv_field_status --setting-value=required
${PYTHON_BIN} manage.py set_setting --journal=JHEP --group-name=submissionconfiguration --setting-name=hierarchical_keywords --setting-value=True
${PYTHON_BIN} manage.py set_setting --journal=JHEP --group-name=submissionconfiguration --setting-name=autocomplete_keywords --setting-value=True

debug "$dev_db_name ready"


echo "Remember to $(tput setaf 1)anonymize your data$(tput sgr0) if you don't need to keep it for some specific reason."
