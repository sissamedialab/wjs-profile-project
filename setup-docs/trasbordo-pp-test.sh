#!/bin/bash

# Reset jcom-test and copy everything from jcom-pp to jcom-test.

set -e

# Delete all files from test, and replace with files from pre-production
j_test=/home/wjs/janeway
j_pp=/home/wjs/janeway-pp
rsync --archive --delete $j_pp/src/files/ $j_test/src/files/
rsync --archive --delete $j_pp/src/media/ $j_test/src/media/

# Let's store postgresql pwds (temporarily) in pgpass
pgpass=$HOME/.pgpass
can_delete_pgpass=yes
if [[ -e $pgpass ]]
then
    echo "Warning: appending pwd to $pgpass"
    can_delete_pgpass=no
else
    touch $pgpass
    chmod 0600 $pgpass
fi

# Test db
j_test_settings=$j_test/src/core/settings.py
j_test_dump=/tmp/j_test.sql
j_test_db_name=$(sed -n -E 's/ +"NAME": "(.+)",/\1/p' $j_test_settings)
j_test_db_user=$(sed -n -E 's/ +"USER": "(.+)",/\1/p' $j_test_settings)
j_test_db_password=$(sed -n -E 's/ +"PASSWORD": "(.+)",/\1/p' $j_test_settings)
j_test_db_host=$(sed -n -E 's/ +"HOST": "(.+)",/\1/p' $j_test_settings)
echo "$j_test_db_host:5432:$j_test_db_name:$j_test_db_user:$j_test_db_password" >> $pgpass
# I also need a catch-all entry, or dropdb/createdb won't work
echo "$j_test_db_host:5432:*:$j_test_db_user:$j_test_db_password" >> $pgpass
# Dump also the test db (just in case...)
pg_dump -U $j_test_db_user -h $j_test_db_host $j_test_db_name --clean --create --no-password --file=$j_test_dump

# Must manually dropdb and createdb because Janeway doesn't always use
# "on delete=CASCADE" and the drop from `pg_restore --clean` won't
# work.
dropdb -U $j_test_db_user -h $j_test_db_host --no-password $j_test_db_name
createdb -U $j_test_db_user -h $j_test_db_host --no-password $j_test_db_name


# Pre-production db
j_pp_settings=$j_pp/src/core/settings.py
j_pp_dump=/tmp/j_pp.sql
j_pp_db_name=$(sed -n -E 's/ +"NAME": "(.+)",/\1/p' $j_pp_settings)
j_pp_db_user=$(sed -n -E 's/ +"USER": "(.+)",/\1/p' $j_pp_settings)
j_pp_db_password=$(sed -n -E 's/ +"PASSWORD": "(.+)",/\1/p' $j_pp_settings)
j_pp_db_host=$(sed -n -E 's/ +"HOST": "(.+)",/\1/p' $j_pp_settings)
echo "$j_pp_db_host:5432:$j_pp_db_name:$j_pp_db_user:$j_pp_db_password" >> $pgpass

# Don't use --create if you want to restore to a DB different from the
# one you dumped, because it brings the DB name into the dump, so that
# it is not possible to restore in any other DB
# e.g. pg_dump ... --create ...
# Not even with pg_restore (when using --format=custom)
pg_dump -U $j_pp_db_user -h $j_pp_db_host $j_pp_db_name --no-password --format=custom --file=$j_pp_dump

# Restore pre-production schema/data into test DB
pg_restore --exit-on-error --single-transaction -U $j_test_db_user -h $j_test_db_host --no-password --dbname $j_test_db_name $j_pp_dump

# Fix press and journal domain (or get infinite redirects)
psql --quiet -U $j_test_db_user -h $j_test_db_host --no-password --dbname $j_test_db_name <<EOF
update press_press set domain='janeway-test.sissamedialab.it';
update journal_journal set domain='jcom-test.sissamedialab.it';
EOF


if [[ $can_delete_pgpass == yes ]]
then
    rm -f $pgpass
fi
