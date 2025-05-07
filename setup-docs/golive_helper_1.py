"""
Read submission settings from dev and copy them to test.

When dumping the data, I'm dumping the setting.name in order to get the setting.id because I can't be sure that the ids
match across the two different DBs.

Janeway also has a SubmissionConfiguration object for each Journal. Here we transfer the records for this table too.

"""

import socket
from pathlib import Path

import psycopg2

pgpass_path = Path.home() / ".pgpass"
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
            conn_params[host] = {
                database: {
                    user: {
                        "password": password,
                    },
                },
            }

except FileNotFoundError:
    print(f"The file {pgpass_path} does not exist.")
except PermissionError:
    print(f"Permission denied when trying to read {pgpass_path}.")
except Exception as e:
    print(f"An unexpected error occurred: {e}")

dev_db = {
    "host": "wjs-test",
    "database": "janeway-dev",
    "user": "wjs_ro",
    "password": conn_params["wjs-test"]["*"]["wjs_ro"]["password"],
    "port": "5432",
}

if "wjs-prod" in socket.gethostname():
    # We are on one of the production machines
    local_db = {
        "host": "localhost",
        "database": "janeway",
        "user": "wjs",
        "password": conn_params["localhost"]["*"]["wjs"]["password"],
        "port": "5432",
    }
else:
    # We are on the test machine
    local_db = {
        "host": "localhost",
        "database": "janeway-test",
        "user": "janeway",
        "password": conn_params["localhost"]["*"]["janeway"]["password"],
        "port": "5432",
    }

# Settings
# ========

settings_names = (
    "abstract_required",
    "acceptance_criteria",
    "accepts_preprint_submissions",
    "copyright_notice",
    "copyright_submission_label",
    "data_figure_file_submission_instructions",
    "default_review_form",  # this is not submission-related, but... 🙂
    "disable_journal_submission",
    "disable_journal_submission_message",
    "editors_for_notification",
    "file_submission_guidelines",
    "focus_and_scope",
    "hide_editors_from_authors",
    "limit_access_to_submission",
    "limit_manuscript_types",
    "manuscript_file_submission_instructions",
    "peer_review_info",
    "publication_cycle",
    "publication_fees",
    "submission_access_request_contact",
    "submission_access_request_text",
    "submission_checklist",
    "submission_intro_text",
    "submission_summary",
    "user_automatically_author",
)

dev_connection = psycopg2.connect(**dev_db)
dev_connection.autocommit = True  # on dev, we only do "selects": no need for real transactions
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
    {",".join([f"'{i}'" for i in settings_names])}
)
AND
v.journal_id IS NOT NULL
""",
)

local_connection = psycopg2.connect(**local_db)
local_cursor = local_connection.cursor()

# Delete existing setting values once (for every journal):
# don't do it in the for-loop because we can touch the same setting multiple times if we
# change values for multiple journals.
query = f"""DELETE FROM core_settingvalue
WHERE
setting_id IN
(SELECT id from core_setting WHERE name IN
(
    {",".join([f"'{i}'" for i in settings_names])}
)
)
AND
journal_id IS NOT NULL
"""
# DEBUG: # print(query)
local_cursor.execute(query)
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
    local_cursor.execute(
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
local_connection.commit()


# Submission Configuration
# ========================

# Janeway stores some info in this table where each record is in 1to1 relation with a Journal.
# Here I can just update test values with all values from dev.

dev_cursor.execute("""SELECT * FROM submission_submissionconfiguration""")
for row in dev_cursor:
    # 🤔 I need to list all columns...
    # For a different approach (drop the record and insert it anew in a unique transaction)
    # see https://stackoverflow.com/a/49077243/1581629
    local_cursor.execute(
        """
        UPDATE submission_submissionconfiguration
        SET
        publication_fees = %s,
        submission_check = %s,
        copyright_notice = %s,
        competing_interests = %s,
        comments_to_the_editor = %s,
        subtitle = %s,
        abstract = %s,
        language = %s,
        license = %s,
        keywords = %s,
        figures_data = %s,
        journal_id = %s,
        default_language = %s,
        default_license_id = %s,
        default_section_id = %s,
        section = %s,
        funding = %s,
        submission_file_text = %s,
        submission_file_text_en = %s,
        submission_file_text_en_us = %s,
        submission_file_text_fr = %s,
        submission_file_text_de = %s,
        submission_file_text_nl = %s,
        submission_file_text_cy = %s,
        submission_file_text_es = %s,
        submission_file_text_pt = %s
        WHERE
        id = %s
        """,
        (*row[1:], row[0]),
    )
local_connection.commit()


# Licenses
# ========

# Should alredy be identical because of https://gitlab.sissamedialab.it/wjs/specs/-/work_items/1336#note_35443
# Here we just check.
# (also I don't think I can just assume good values on dev; see details in link above)

dev_cursor.execute("SELECT * FROM submission_licence order by id")
local_cursor.execute("SELECT * FROM submission_licence order by id")
good_rows = dev_cursor.fetchall()
bad_rows = local_cursor.fetchall()  # not really "bad", just "I don't know"...
colcount = len(good_rows[0])
for good, bad in zip(good_rows, bad_rows, strict=True):
    for col in range(colcount):
        if good[col] != bad[col]:
            msg = f"Licences inconsistent! Please check {good[0]} col {col}"
            raise ValueError(msg)


# Additional Submission Fields
# ============================

# These are fields such as "metadata coherence", "use of AI", that are manually configured from the manger → additional
# submission fields and that are visibile in the "info" step.

# Here we transfer all the fields, i.e. for both JCOM and JCOMAL

dev_cursor.execute("SELECT * FROM submission_field order by id")

# We don't have any field-answer in production, so it's safe to truncate-cascade submission_field and
# submission_fieldanswer (that is the only table that has a FK to it).
#
# To be more precise, Article 1413 has a 'Metadata coherence' answer set to True, but only because it has had its
# metadata updated. ATM the answer has no real meaning.
local_cursor.execute("TRUNCATE submission_field RESTART IDENTITY CASCADE")
rows = dev_cursor.fetchall()
colcount = len(rows[0])
for row in rows:
    local_cursor.execute(
        f"INSERT INTO submission_field VALUES({', '.join(['%s'] * colcount)})",
        (*row,),
    )
local_connection.commit()
