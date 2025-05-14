#!/bin/bash

# This script prepares a Janeway instance for JCOM go-live.

# It behaves differently if it detects that we are on a test machine or the production machine.
# PROD:
# - install WJS latest
# - verify and/or tidy-up a bunch of settings (django settings and journal settings)
# - port cms content from dev instance
# - port submission configuration from dev instance
# - run import of pending papers
# - send imported papers to Prophy
# - see also specs#855
#
# TEST:
# - reset DB: overwrites the test-db with the production db
# - reset files: rsync the prod files onto the test files
# - do the setup-procedure as for PROD


# Run as
# DEBUG=1 \
#   ./setup-golive-test-env.sh

# Advanced
# DEBUG=1 \
#   PYTHON=/home/wjs/.virtualenvs/janeway/bin/python \
#   JANEWAY=/home/wjs/janeway \
#   QUICK=0 \
#   WJS_FROM_GIT=0 \
#   WJS_TAG="feature/issue-1261__fixes" \
#   ./setup-golive-test-env.sh
#
# WJS_FROM_GIT is the most delicate: it tells if we should install from the package-registry (0) or from the git repo (1)
#              when this variable is set to "1", the variable WJS_TAG tells which tag/branch/commit to use
#              NB: it also controls which values are set in the Prophy-related settings (see code).
# QUICK is useful only on TEST machine.
# PYTHON and JANEWAY are generally not needed.

set -e

# output something only if variable "$DEBUG" is "1"
# echo in gray color
function debug () {
    if test "$DEBUG" == "1"
    then
        >&2 echo $(tput setaf 8)"$@"$(tput sgr0)
    fi
}
export -f debug

# echo in red color
function error () {
    >&2 echo $(tput setaf 1)"$@"$(tput sgr0)
}
export -f error


if [[ "$(hostname)" == *"wjs-prod" ]]
then
    debug "Working on PROD"
    PROD=1
    TEST=0
    : ${PYTHON:="/home/wjs/.virtualenvs/janeway/bin/python"}
    : ${JANEWAY:="/home/wjs/janeway"}
    apache_redirects_file=$HOME/.virtualenvs/janeway/lib/python3.11/site-packages/wjs/conf/jcom-apache-redirects.inc
    local_db_user=wjs
    local_db_name=janeway
    UWSGI_INI="janeway.ini"
    QCLUSTER="qcluster.service"
else
    PROD=0
    TEST=1
    debug "Working on TEST"
    : ${PYTHON:="/home/wjs/.virtualenvs/janeway-test/bin/python"}
    : ${JANEWAY:="/home/wjs/janeway-test"}
    apache_redirects_file=$HOME/.virtualenvs/janeway-test/lib/python3.11/site-packages/wjs/conf/jcom-apache-redirects.inc
    local_db_user=janeway
    local_db_name=janeway-test
    UWSGI_INI="janeway-test.ini"
    QCLUSTER="qcluster-test.service"
fi

# Setup
p="$PYTHON"
m="$p $JANEWAY/src/manage.py"
: ${DEBUG:=1}
: ${QUICK:=1}
: ${WJS_FROM_GIT:=1}
: ${WJS_TAG:="feature/issue-1261__fixes"}


# Stop the services
mv ~/uwsgi/{,stopped-}"$UWSGI_INI" 2> /dev/null || debug "Test instance already stopped."
systemctl --user stop "$QCLUSTER" || debug "Please check that test qcluster is stopped."


if test "$TEST" == "1"
then
    # This code is necessary to reset the "test" instance

    prod_files=wjs@wjs-prod:/home/wjs/janeway
    test_files=/home/wjs/janeway-test

    echo
    echo "INFO: you need credentials to Production DB and test DB in .pgpass"
    echo "      and ssh access to $prod_files"
    echo
    echo $(tput setaf 3)"WARNING: THIS IS DESTRUCTIVE ON ${test_files}!!!"$(tput sgr0)
    echo

    read -p 'Do you want to continue? (type "yes" to continue) ' continue
    if test "$continue" != "yes"
    then
        echo "Quitting."
        exit
    fi


    if test "$QUICK" == "1"
    then
        debug "Skipping rsync. Check env variable QUICK."
    else
        # Sync files
        # Warning: "--delete" deletes extraneous files from dest dirs
        rsync --archive --delete "${prod_files}/src/files/" "${test_files}/src/files/"
        rsync --archive --delete "${prod_files}/src/media/" "${test_files}/src/media/"
        debug "rsync from $prod_files done"
    fi

    # Sync DBs
    # Dump also the test db (just in case...)
    test_db_user=janeway
    test_db_name=janeway-test
    test_dump=/tmp/test_dump.sql

    # only dump if no "recent" dump is present
    # (useful during development)
    ONEDAY=1440
    # Please note that "find" will give no-error status even if no file meets the
    # specs, so we check if the returned string is empty (which means that "find"
    # did not find the file)
    if [[ -n "$(find $test_dump -mmin -$ONEDAY 2> /dev/null)" ]] ;
    then
        debug "Recent test DB dump already exists ($test_dump); skipping dump."
    else
        pg_dump -U "$test_db_user" -h localhost \
                --clean --create --no-password \
                "$test_db_name" \
                --file="$test_dump"
        debug "$test_dump dumped"
    fi

    # Drop and create empty test DB
    dropdb   -U "$test_db_user" -h localhost --no-password "$test_db_name"
    createdb -U "$test_db_user" -h localhost --no-password "$test_db_name"


    # Get the production db
    prod_dump=/tmp/prod.sql
    prod_db_name=janeway
    prod_db_user=wjs_ro
    prod_db_host=wjs-prod
    if [[ -n "$(find $prod_dump -mmin -$ONEDAY 2> /dev/null)" ]] ;
    then
        debug "Recent prod DB dump already exists ($prod_dump); skipping dump."
    else
        # Don't use --create if you want to restore to a DB different from the
        # one you dumped, because it brings the DB name into the dump, so that
        # it is not possible to restore in any other DB
        # e.g. pg_dump ... --create ...
        # Not even with pg_restore (when using --format=custom)
        pg_dump --no-password --format=custom \
                -U "$prod_db_user" -h "$prod_db_host" "$prod_db_name" \
                --file="$prod_dump"
        debug "Prod DB dumped ($prod_dump)"
    fi

    # Restore the production schema/data into the test DB
    pg_restore --no-owner --no-password --exit-on-error --single-transaction \
               -U "$test_db_user" -h localhost --dbname "$test_db_name" \
               "$prod_dump"
    debug "Prod DB restored into local $test_db_name"

    # Fix press and journal domains (to avoid infinite redirects)
    psql --quiet -U "$test_db_user" -h localhost --no-password --dbname "$test_db_name" <<EOF
update press_press set domain='wjs-test-test-journals.wjapp.it';
update journal_journal set domain='wjs-test-test-jcom.wjapp.it' where code='JCOM';
update journal_journal set domain='wjs-test-test-jcomal.wjapp.it' where code='JCOMAL';

UPDATE core_settingvalue
SET
  value = '',
  value_en = ''
FROM core_setting s
WHERE
  s.name = 'use_crossref'
  AND core_settingvalue.setting_id = s.id
  AND core_settingvalue.journal_id IS NOT NULL
;

-- useless once we set use_crossref to off/false, but...
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

EOF
    debug "Test DB ready ($test_db_name)"

fi
# -- END of resetting of the test-instance



# Uninstall wjs-mgmt-cmds if necessary
# (it has a reference to wjs_review and would block the rest of the process)
$p -m pip show wjs_mgmt_cmds >/dev/null 2>&1  && wjs_utils="installed" || wjs_utils="not installed"
if [[ "$wjs_utils" == "installed" ]]
then
    $p -m pip uninstall --yes --quiet wjs_mgmt_cmds >/dev/null
    debug "Uninstalled wjs-utils."
else
    debug "wjs-utils is not installed (ok)"
fi


# Check that DEBUG is False
# (manage.py will not work before we link wjs_review plugin)
$m shell -c 'from django.conf import settings;import sys;exit(1) if settings.DEBUG is True else exit(0)' 2>/dev/null 1>&2 && debug "Django not in DEBUG mode (ok)" || ( error "Django in DEBUG mode (😠). Quitting!"; exit 1; )


# Upgrade Janeway
pushd $JANEWAY > /dev/null
git pull > /dev/null
$p -m pip install -r requirements.txt -c constraints.txt > /dev/null
popd > /dev/null
debug "Janeway updated"

# Install wjs_review
# When WJS_FROM_GIT
if [[ "${WJS_FROM_GIT}" != "1" ]]
then
    # Install / update wjs-jcom-profile from the registry
    $p -m pip install -U wjs-jcom-profile
    debug "Installed wjs-jcom-profile from registry"
else
    # Install / update wjs-jcom-profile from a given tag
    # (requires ssh -A)
    tag="${WJS_TAG:-v0.6.14}"
    $p -m pip uninstall --yes --quiet wjs-jcom-profile > /dev/null && \
        $p -m pip install --no-input --quiet "git+ssh://git@gitlab.sissamedialab.it/wjs/wjs-profile-project@${tag}#egg=wjs.jcom_profile"
    debug "Installed wjs-jcom-profile at ${tag}"
fi

$m link_plugins >/dev/null ; debug "All plugins linked and installed"
$m migrate jcom_profile >/dev/null ; debug "jcom_profile migrations applied"
$m migrate wjs_review >/dev/null ; debug "wjs_review migrations applied"
$m migrate >/dev/null ; debug "All migrations applied"
$m build_assets >/dev/null ; debug "Assets built (and collected)"
$m load_default_settings >/dev/null ; debug "Janeway default settings (re)loaded"
$m patch_submission_settings >/dev/null ; debug "Submission settings patched"
$m apply_wjs_settings --no-input >/dev/null ; debug "Existing settings corrected"
$m setup_review_settings --force >/dev/null ; debug "wjs_review settings created"
$m create_custom_settings --force >/dev/null ; debug "jcom_profile settings created"

$m populate_wjs_section >/dev/null ; debug "WJS Sections populated"


# Uncomment apache redirect rule to point wjapp URLs to the dedicated help page
rg -q '/site/help-new-system/' "$apache_redirects_file" || ( error "No redirect to help page in apache conf (😠). Quitting!"; exit 1; )
sed -i 's|^# \(RewriteRule .*/site/help-new-system/.*\)|\1|' "$apache_redirects_file"
debug "Redirect to help page in apache conf (ok)"


# Dump cms pages
cms_pages=/tmp/cms-$(date -I).json
m_dev="/home/wjs/.virtualenvs/janeway-dev/bin/python /home/wjs/janeway-dev/src/manage.py"
if test "$TEST" == "1"
then
    # Easy, because we are on the same machien as the "dev" instance
    $m_dev dumpdata cms --indent=2 -o "$cms_pages" >/dev/null
    sed -i 's!//wjs-test-dev-!//wjs-test-test-!g' "$cms_pages"
else
    # A bit more difficult: we must collect the cms pages from another machine
    ssh wjs@wjs-test $m_dev dumpdata cms --indent=2 -o "$cms_pages" >/dev/null
    scp wjs@wjs-test:"$cms_pages" /tmp/
    sed -i 's!//wjs-test-dev-jcom.wjapp.it!//jcom.sissa.it!g' "$cms_pages"
    sed -i 's!//wjs-test-dev-jcomal.wjapp.it!//jcomal.sissa.it!g' "$cms_pages"
fi
debug "CMS pages dumped from dev ($cms_pages)"

# Clean-up CMS stuff before load (because load only "adds")
psql --echo-errors --quiet -U "$local_db_user" -h localhost --no-password --dbname "$local_db_name" <<EOF
TRUNCATE TABLE cms_historicalpage, cms_mediafile, cms_navigationitem, cms_page, cms_submissionitem;
EOF

$m loaddata "$cms_pages" >/dev/null ; debug "CMS pages loaded locally"


# LaTeX preambles
# Warning: does nothing if they are already set!
jcom_preamble='\documentclass[a4paper,11pt]{article}
\usepackage[journal=jcom]{jcom2}[=2024-12-01]
{% load wjs_tex %}
{% with article.title as title %}
{% with article.date_accepted|date:"Y-m-d" as date_accepted %}
{% with journal.code as journal %}
{% with article.section.wjssection.pubid_and_tex_sectioncode as type_code %}
{% with article.articleworkflow.latex_desc as latex_desc %}
{% with article.ancestors.first.parent.articleworkflow.latex_desc as latex_desc_parent %}
{% with article.primary_issue.issueparameters.latex_fragment as latex_desc_issue %}
{% angular_variables %}
\article{<title>}
\accepted{<date_accepted>}
\journal{<journal>}
\doc_type{<type_code>}
\latex_desc{<latex_desc>}
\latex_desc_parent{<latex_desc_parent>}
\subheader{<latex_desc_issue>}
{% endangular_variables %}
{% endwith %}
{% endwith %}
{% endwith %}
{% endwith %}
{% endwith %}
{% endwith %}
{% endwith %}
%% Filled-in during publication:
\published{???}
\publicationyear{xxxx}
\publicationvolume{xx}
\publicationissue{xx}
\publicationnum{xx}
\doiInfo{doi}{xxxxxxx}
'
jcomal_preamble=${jcom_preamble//journal=jcom/journal=jcomal}
psql --echo-errors --quiet -U "$local_db_user" -h localhost --no-password --dbname "$local_db_name" <<EOF ; debug "LaTeX preambles set"
insert into wjs_review_latexpreamble values
(1, '$jcom_preamble', 1)
,
(2, '$jcomal_preamble', 2)
ON CONFLICT DO NOTHING
;
EOF

# Check that q-cluster is async
$m shell -c 'from django.conf import settings;import sys;exit(1) if settings.Q_CLUSTER["sync"] is True else exit(0)' 2>/dev/null 1>&2 && debug "Q-cluster is async (ok)" || error "Q-cluster is sync (😠). Please correct settings!"

# Check that GDPR middleware is active
$m shell -c 'from django.conf import settings;import sys;exit(1) if "wjs.jcom_profile.middleware.PrivacyAcknowledgedMiddleware" not in settings.MIDDLEWARE_CLASSES else exit(0)' 2>/dev/null 1>&2 && debug "GDPR middleware is active (ok)" || error "GDPR middleware is not active (😠). Please correct settings!"

# Ensure DOIs are note registered at acceptance
# Warning: does nothing if journal overrides exist!
psql --echo-errors --quiet -U "$local_db_user" -h localhost --no-password --dbname "$local_db_name" <<EOF ; debug "DOI should not be registered at acceptance"
WITH dummy_tbl AS (select id as rdaa from core_setting where name='register_doi_at_acceptance')
INSERT INTO core_settingvalue
  (setting_id, journal_id, value, value_en)
VALUES
  ((select rdaa from dummy_tbl), 1, 'off', 'off'),
  ((select rdaa from dummy_tbl), 2, 'off', 'off')
ON CONFLICT DO NOTHING
;
EOF

# Setup assignment parameters for editors and EO
$m reset_assignment_parameters --noinput >/dev/null ; debug "Assignment parameters reset for EO and editors"

# Ensure submission settings are valid
# Also ensure that the default_review_form is set
# (take them from dev)
# Take the list of submission settings from core.logic.get_settings_to_edit()
#
# Attempts:
# - pg_dump / pg_restore -> failed: could not find a way to restore only part of a table
#
# - \copy -> failed: was able to collect only part of a table, but could not
#            "import" data using setting.name to get the setting.id (I can't be
#            sure that the ids match on two different DBs)
#   E.g.:
#   \copy
#     (
#       select s.name, v.journal_id, v.value, v.value_cy...
#       from core_settingvalue v left join core_setting s on v.setting_id = s.id
#       where
#       s.name in ('disable_journal_submission', ...)
#       and v.journal_id is not null
#     )
#     to '/tmp/aaa';
#
# - cannot use dumpdata / loaddata because many setting from prod should not be
#   touched (the last "trasbordo" to dev has been done so long ago that I can't
#   be sure that non-submission settings are valid).

$p golive_helper_1.py && debug "Submission-configuration copied from dev" || ( error "Submission-configuration failed"; exit 1; )

# We should also look a Journal.enable_correspondence_authors (because of
# core.views::957) but it's set default=True and we never touch it, is no need.

# Ensure redis is active
redis-cli ping > /dev/null 2>&1 && debug "Redis running (ok)" || error "Redis server not running on default port (😠). Please check!"

# List production and pending papers
import_log=/tmp/import-$(date -I).log
papers_list=/tmp/jcom-pending-$(date -I).list

$p golive_helper_2.py > $papers_list && debug "Collected $(wc -l $papers_list) papers." || ( error "Paper collection failed"; exit 1; )

# Import production and pending papers
rm -f "$import_log"
papers_count=0
for paper in $(cat $papers_list)
do
    papers_count=$((papers_count+1))
    JANEWAY_SETTINGS_MODULE=core.settings.nonotifications  \
        $m import_articles_from_wjapp \
        --importfiles \
        --preprintid "$paper" >> "$import_log" 2>&1 && echo -ne "$(tput setaf 8)Imported $paper\033[0K\r$(tput sgr0)" || echo -e "$(tput setaf 1)Errors importing $paper$(tput sgr0)"

    # Useful in development
    if [[ "$QUICK" == "1" ]] && [[ $papers_count -eq 3 ]]
    then
        break
    fi

done
echo -e "$(tput setaf 8)Imported $(wc -l $papers_list) papers.\033[0K\r$(tput sgr0)"

# Activate all users
psql --echo-errors --quiet -U "$local_db_user" -h localhost --no-password --dbname "$local_db_name" <<EOF ; debug "All users activated"
update core_account set is_active='t';
EOF


# Import all kwds
$m import_keywords >/dev/null ; debug "All kwds imported from wjapp"


# Ensure all wjapp editor have role "section-editor" in wjs
$m import_users_from_wjapp_with_role --editors > /dev/null ; debug "All editor imported and role set"


# Ensure articles are sent to prophy
$m shell -c 'from django.conf import settings;import sys;exit(1) if not settings.PROPHY_API_KEY else exit(0)' 2>/dev/null 1>&2 && debug "PROPHY_API_KEY is set (ok)" || error "PROPHY_API_KEY is not set (😠). Please correct settings!"
$m shell -c 'from django.conf import settings;import sys;exit(1) if not settings.PROPHY_JWT_KEY else exit(0)' 2>/dev/null 1>&2 && debug "PROPHY_JWT_KEY is set (ok)" || error "PROPHY_JWT_KEY is not set (😠). Please correct settings!"

# I'll check that values of the settings,
# if the value is not the expected one (it could never be in a pristine prod env),
# I'll set it to the expected one (or to the fallback one if in QUICK mode).
# This should allow us to test the script and to setup and test environment,
# while allowing for clear feedback on the go-live situation.
function verify_setting_value () {
    setting_name="$1"
    journal_id="$2"
    expected_value="$3"
    fallback_value="$4"
    # SQL-injection paradise, but they are all my values 😉

    setting_value=$(psql \
                        --echo-errors --quiet \
                        -U "$local_db_user" -h localhost --no-password --dbname "$local_db_name" \
                        -t -c "select v.value from core_settingvalue v
left join core_setting s on v.setting_id=s.id
where
s.name='$setting_name'
and v.journal_id=$journal_id
;" |\
                   sed 's/ *//')
    if [[ "$setting_value" != "$expected_value" ]]
    then
        error "Setting value for journal $journal_id for $setting_name is \"$setting_value\" (😠). Was expecting \"$expected_value\"."
        if [[ "$WJS_FROM_GIT" == "1" ]]
        then
            # insert fallback-value
            psql --echo-errors --quiet -U "$local_db_user" -h localhost --no-password --dbname "$local_db_name" <<EOF ; debug "Set \"$fallback_value\" for journal $journal_id for setting $setting_name"
WITH dummy_tbl AS (select id as settingid from core_setting where name='$setting_name')
INSERT INTO core_settingvalue
  (setting_id, journal_id, value, value_en)
VALUES
  ((select settingid from dummy_tbl), $journal_id, '$fallback_value', '$fallback_value')
ON CONFLICT DO NOTHING
;
EOF
        else
            # insert expected value
            psql --echo-errors --quiet -U "$local_db_user" -h localhost --no-password --dbname "$local_db_name" <<EOF ; debug "Set \"$expected_value\" for journal $journal_id for setting $setting_name"
WITH dummy_tbl AS (select id as settingid from core_setting where name='$setting_name')
INSERT INTO core_settingvalue
  (setting_id, journal_id, value, value_en)
VALUES
  ((select settingid from dummy_tbl), $journal_id, '$expected_value', '$expected_value')
ON CONFLICT DO NOTHING
;
EOF
        fi
    else
        debug "Setting value for journal $journal_id $setting_name is \"$setting_value\" (ok)."
    fi
}

verify_setting_value 'prophy_journal' 1 'JCOM' 'JCOM prova matteo bis'
verify_setting_value 'prophy_journal' 2 'JCOMAL' 'JCOM prova matteo bis'
verify_setting_value 'prophy_upload_enabled' 1 'on' 'on'
verify_setting_value 'prophy_upload_enabled' 2 'on' 'on'

read -p 'Do you want to send all imported articles to Prophy? (type "yes" to continue) ' continue
if test "$continue" == "yes"
then
    $p -m pip install --no-input --quiet git+ssh://git@gitlab.sissamedialab.it/wjs/wjs-utils-project.git@440938db1afd1a73d9a74f350fb3a4fbbd3f703b#egg=wjs_mgmt_cmds >/dev/null
    debug "Installed wjs-utils."

    papers_count=0
    while read -r preid artid
    do
        papers_count=$((papers_count+1))
        $m prophy_helper --action sendprophy $artid 2>&1 |rg -q -e CRITICAL -e ERROR -e WARNING && \
            error "Error sending $preid / $artid to Prophy. Please check." \
                || echo -ne "$(tput setaf 8)Sent $preid / $artid to Prophy\033[0K\r$(tput sgr0)"

        # Useful in development
        if [[ "$QUICK" == "1" ]] && [[ $papers_count -eq 3 ]]
        then
            break
        fi

    done <<<$(sed -E -n 's/.*[Ii]mporting (new|existing) article (.*) at ([0-9]*).*/\2 \3/p' "$import_log")
    echo -e "$(tput setaf 8)Papers sent to Prophy.\033[0K\r$(tput sgr0)"

    $p -m pip uninstall --yes --quiet wjs_mgmt_cmds >/dev/null
    debug "Uninstalled wjs-utils."

fi


echo "Please start test instance with start-test"
echo "Please start test qcluster with systemctl --user start qcluster-test.service"
