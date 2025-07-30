#!/bin/bash

set -e

USER="YOUR_USERNAME"
PASS="YOUR_PASSWORD"
GPG_PASSPHRASE="YOUR_GPG_PASSPHRASE"

WEBDAV_BASE="https://example.org/remote.php/webdav"
TARGET_DIR="target_dir"

TIMESTAMP=$(date +"%Y_%m_%d_%H_%M")
DUMP_NAME="${TIMESTAMP}_j_production_dump_db.sql"
DUMP_PATH="/root/${DUMP_NAME}"
ENCRYPTED_DUMP="${DUMP_PATH}.gpg"
REMOTE_DUMP="${TARGET_DIR}/${DUMP_NAME}.gpg"

DB_NAME="YOUR_DB_NAME"

# 1) Dump del db by postgres user
su - postgres -c "pg_dump $DB_NAME" > "$DUMP_PATH"

# 2) Cifratura del db via chiave GPG
gpg --batch --yes --passphrase "$GPG_PASSPHRASE" -c "$DUMP_PATH"

# 3) Rimozione del db in chiaro
rm -f "$DUMP_PATH"

# 4) Recupera la lista di file presenti nella cartella di destinazione del caricamento
files=($(curl -s -u "$USER:$PASS" -X PROPFIND -H "Depth: 1" "$WEBDAV_BASE/$TARGET_DIR/" \
  | grep "<d:href>" \
  | sed -E 's|.*<d:href>(.*)</d:href>.*|\1|' \
  | grep -v "/$" \
  | sed 's|/nextcloud/remote.php/webdav/||'))

file_count="${#files[@]}"

# 5) Controllo che ci sia un singolo file e che sia un dump eliminabile

if [ "$file_count" -eq 0 ]; then
	:
elif [ "$file_count" -eq 1 ]; then
    file="${files[0]}"

    # Verifica se eliminabile
    if [[ "$file" == *"_j_production_dump_db"* ]]; then
        curl -s -u "$USER:$PASS" -X DELETE "$WEBDAV_BASE/$file"
    else
        echo "ERRORE: il file trovato non presenta la nomenclatura corretta"
    	printf ' - %s\n' "${files[@]}"
		exit 1
    fi
else
    echo "ERRORE: trovati $file_count file, ma ne aspettavo al massimo uno:"
    printf ' - %s\n' "${files[@]}"
    exit 1
fi

# 6) Carico il dump cifrato
if curl -s -u "$USER:$PASS" -T "$ENCRYPTED_DUMP" "$WEBDAV_BASE/$REMOTE_DUMP"; then
	:
else
    echo "Errore durante l'upload"
    exit 1
fi

# 7) Rimozione del dump cifrato
rm -f "$ENCRYPTED_DUMP"
