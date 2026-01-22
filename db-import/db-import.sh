#!/bin/bash

curdir=`dirname $0`
user=$1
database=$2
dump=$3
host=$4
host_connection=""

if [ -z "${user}" -o -z "${database}" -o -z "${dump}" ]; then
    echo "Usage: $0 user database encrypted-database [host]"
    exit 1
fi

if [ -n "${host}" ]; then
    host_connection="-H $host"
fi

dropdb -U $user $host_connection $database
createdb -U $user $host_connection $database
psql -U $user $host_connection $database <<<"CREATE EXTENSION IF NOT EXISTS citext;"
psql -U $user $host_connection $database <<<"CREATE EXTENSION IF NOT EXISTS btree_gin;"
gpg --decrypt $dump | psql -U $user $host_connection $database
psql -U $user $host_connection $database <<EOF
update press_press set domain='press.local:8000', is_secure='f';
update journal_journal set domain='jcom.local:8000', is_secure='f' where code='JCOM';
update journal_journal set domain='jcomal.local:8000', is_secure='f' where code='JCOMAL';
update journal_journal set domain='jquant.local:8000', is_secure='f' where code='JQUANT';
update journal_journal set domain='jcap.local:8000', is_secure='f' where code='JCAP';
update journal_journal set domain='jhep.local:8000', is_secure='f' where code='JHEP';
update journal_journal set domain='jsts.local:8000', is_secure='f' where code='JSTAT';
update journal_journal set domain='jinst.local:8000', is_secure='f' where code='JINST';
EOF

psql -U $user $host_connection $database <<EOF
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
EOF
