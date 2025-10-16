#!/bin/bash

# Reset jcom-prod and copy everything from jcom-pp to jcom-prod.

set -e


# Delete all files from prod, and replace with files from pre-production
j_prod=wjs@jannon:/home/wjs/janeway
j_pp=/home/wjs/janeway-pp

echo
echo "INFO: you need credentials to Production DB and Pre-production DB in .pgpass"
echo "      and ssh access to $j_prod"
echo
echo $(tput setaf 3)"WARNING: THIS IS DESTRUCTIVE ON ${j_prod}!!!"$(tput sgr0)
echo

read -p 'Do you want to continue? (type "yes" to continue) ' continue
if test "$continue" != "yes"
then
    echo "Quitting."
    exit
fi

# FILL THESE BEFORE USE!
j_prod_db_user="***"
j_prod_db_host="***"
j_prod_db_name="***"

j_prod_dump="/tmp/j_prod.sql"

rsync --archive --delete $j_pp/src/files/ $j_prod/src/files/
rsync --archive --delete $j_pp/src/media/ $j_prod/src/media/

# Dump also the prod db before reset (just in case...)
pg_dump -U $j_prod_db_user -h $j_prod_db_host $j_prod_db_name --clean --create --no-password --file=$j_prod_dump

# Must manually dropdb and createdb because Janeway doesn't always use
# "on delete=CASCADE" and the drop from `pg_restore --clean` won't
# work.
dropdb -U $j_prod_db_user -h $j_prod_db_host --no-password $j_prod_db_name
createdb -U $j_prod_db_user -h $j_prod_db_host --no-password $j_prod_db_name


# Pre-production db
j_pp_settings=$j_pp/src/core/settings.py
j_pp_dump=/tmp/j_pp.sql
j_pp_db_name=$(sed -n -E 's/ +"NAME": "(.+)",/\1/p' $j_pp_settings)
j_pp_db_user=$(sed -n -E 's/ +"USER": "(.+)",/\1/p' $j_pp_settings)
j_pp_db_password=$(sed -n -E 's/ +"PASSWORD": "(.+)",/\1/p' $j_pp_settings)
j_pp_db_host=$(sed -n -E 's/ +"HOST": "(.+)",/\1/p' $j_pp_settings)
# expected to exists! echo "$j_pp_db_host:5432:$j_pp_db_name:$j_pp_db_user:$j_pp_db_password" >> $pgpass

# Don't use --create if you want to restore to a DB different from the
# one you dumped, because it brings the DB name into the dump, so that
# it is not possible to restore in any other DB
# e.g. pg_dump ... --create ...
# Not even with pg_restore (when using --format=custom)
pg_dump -U $j_pp_db_user -h $j_pp_db_host $j_pp_db_name --no-password --format=custom --file=$j_pp_dump

# Restore pre-production schema/data into prod DB
pg_restore --no-owner --exit-on-error --single-transaction -U $j_prod_db_user -h $j_prod_db_host --no-password --dbname $j_prod_db_name $j_pp_dump

# Fix press and journal domain (or get infinite redirects)
psql --quiet -U $j_prod_db_user -h $j_prod_db_host --no-password --dbname $j_prod_db_name <<EOF
update press_press set domain='journals.sissamedialab.it';
update journal_journal set domain='jcom.sissa.it';
EOF
