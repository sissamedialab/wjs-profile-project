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

dropdb -U ${user} ${host_connection} ${database}
createdb -U ${user} ${host_connection} ${database}
psql -U ${user} ${host_connection} ${database} <<<"CREATE EXTENSION IF NOT EXISTS citext;" >/dev/null
psql -U ${user} ${host_connection} ${database} <<<"CREATE EXTENSION IF NOT EXISTS btree_gin;" >/dev/null

TMPFILE=$(mktemp)
rm -f ${TMPFILE}
gpg --decrypt -o ${TMPFILE} ${dump} 2>/dev/null

failed=0
if [[ -f ${TMPFILE} ]]; then
    echo "Restoring dump ${dump} to ${database} ..."
    pg_restore -U ${user} ${host_connection} -x -O -d ${database} ${TMPFILE} || psql -U ${user} ${host_connection} -f ${TMPFILE} -x ${database} || failed=1

    rm -f ${TMPFILE}
    if [[ $failed == 0 ]]; then
        psql -U ${user} ${host_connection} ${database} >/dev/null <<EOF
update press_press set domain='press.local:8000', is_secure='f';
update journal_journal set domain='jcom.local:8000', is_secure='f' where code='JCOM';
update journal_journal set domain='jcomal.local:8000', is_secure='f' where code='JCOMAL';
update journal_journal set domain='jquant.local:8000', is_secure='f' where code='JQUANT';
update journal_journal set domain='jcap.local:8000', is_secure='f' where code='JCAP';
update journal_journal set domain='jhep.local:8000', is_secure='f' where code='JHEP';
update journal_journal set domain='jsts.local:8000', is_secure='f' where code='JSTAT';
update journal_journal set domain='jinst.local:8000', is_secure='f' where code='JINST';
EOF

        psql -U ${user} ${host_connection} ${database} >/dev/null <<EOF
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
        echo "Restore completed"
    else
        echo "Restore of dump ${dump} failed"
        exit 2
    fi
else
    echo "File decrypt failed, check input file ${dump}"
    exit 1
fi
