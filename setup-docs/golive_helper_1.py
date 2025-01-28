"""Read submission settings from dev and copy them to test.

When dumping the data, I'm dumping the setting.name in order to get the setting.id because I can't be sure that the ids
match across the two different DBs.

"""

import os

import psycopg2

pgpass_path = os.path.expanduser("~/.pgpass")
conn_params = {}

try:
    with open(pgpass_path) as file:
        for line in file:
            # Remove any trailing newline characters and whitespace
            line = line.strip()

            # Skip empty lines or comments
            if not line or line.startswith("#"):
                continue

            # Split the line into its components
            parts = line.split(":")
            if len(parts) != 5:
                print(f"Invalid line format: {line}")
                continue

            host, port, database, user, password = parts
            conn_params[database] = {
                "user": user,
                "password": password,
                "host": host,
                "port": port,
            }

except FileNotFoundError:
    print(f"The file {pgpass_path} does not exist.")
except PermissionError:
    print(f"Permission denied when trying to read {pgpass_path}.")
except Exception as e:
    print(f"An unexpected error occurred: {e}")

dev_db = {
    "database": "janeway-dev",
    "user": conn_params["*"]["user"],
    "password": conn_params["*"]["password"],
    "host": conn_params["*"]["host"],
    "port": conn_params["*"]["port"],
}

test_db = {
    "database": "janeway-test",
    "user": conn_params["*"]["user"],
    "password": conn_params["*"]["password"],
    "host": conn_params["*"]["host"],
    "port": conn_params["*"]["port"],
}

settings_names = (
    "disable_journal_submission",
    "disable_journal_submission_message",
    "limit_access_to_submission",
    "submission_access_request_text",
    "submission_access_request_contact",
    "abstract_required",
    "submission_intro_text",
    "copyright_notice",
    "submission_checklist",
    "acceptance_criteria",
    "publication_fees",
    "editors_for_notification",
    "user_automatically_author",
    "submission_summary",
    "limit_manuscript_types",
    "accepts_preprint_submissions",
    "focus_and_scope",
    "publication_cycle",
    "peer_review_info",
    "copyright_submission_label",
    "file_submission_guidelines",
    "manuscript_file_submission_instructions",
    "data_figure_file_submission_instructions",
    "hide_editors_from_authors",
    "default_review_form",  # this is not submission-related, but... 🙂
)

dev_connection = psycopg2.connect(**dev_db)
dev_cursor = dev_connection.cursor()
dev_cursor.execute(
    # Warning: watch out for the f-string!
    # (but it's safe because I'm working with data I've generated)
    f"""SELECT
s.name,
v.journal_id,
v.value, v.value_cy, v.value_de, v.value_en, v.value_fr, v.value_nl, v.value_es, v.value_pt, v.value_en_us
FROM
core_settingvalue v LEFT JOIN core_setting s ON v.setting_id = s.id
WHERE
s.name
IN
(
    {','.join([f"'{i}'" for i in settings_names])}
)
AND
v.journal_id IS NOT NULL
"""
)

test_connection = psycopg2.connect(**test_db)
test_cursor = test_connection.cursor()

# Delete existing setting once: don't do it in the for-loop because we can touch the same setting multiple times if we
# values for multiple journals.
query = f"""DELETE FROM core_settingvalue
WHERE
setting_id IN
(SELECT id from core_setting WHERE name IN
(
    {','.join([f"'{i}'" for i in settings_names])}
)
)
"""
# DEBUG: # print(query)
test_cursor.execute(query)
for row in dev_cursor:
    query = """INSERT INTO core_settingvalue
    (
    setting_id,
    journal_id,
    value, value_cy, value_de, value_en, value_fr, value_nl, value_es, value_pt, value_en_us
    )
    select
        id as setting_id,
        %s,
        %s, %s, %s, %s, %s, %s, %s, %s, %s
    from
        core_setting
    where
        name = %s
    """
    test_cursor.execute(
        query,
        (
            # setting name (row[0]) goes last
            row[1],
            row[2],
            row[3],
            row[4],
            row[5],
            row[6],
            row[7],
            row[8],
            row[9],
            row[10],
            row[0],
        ),
    )
test_connection.commit()
