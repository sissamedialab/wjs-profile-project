"""Return a list of preprints to import."""

import argparse
import socket
import sys
from pathlib import Path

import mariadb

if "wjs-prod" in socket.gethostname():
    # We are on one of the production machines
    testenv_path = Path.home() / "janeway/src"
else:
    # We are on the test machine
    testenv_path = Path.home() / "janeway-test/src"

sys.path.append(testenv_path.as_posix())

# curiosity: only flake8 complains E402 here; ruff does not (ruff is correct)
from core.settings import WJAPP_JCOM_IMPORT_CONNECTION_PARAMS  # noqa: E402

parser = argparse.ArgumentParser(description="Your script description here.")

# Add 'journal' argument
parser.add_argument(
    "--journal",
    choices=["JCOM", "JCOMAL", "ALL"],
    help="Specify the journal. Choices: JCOM, JCOMAL, ALL",
    default="JCOM",
)

# Add 'section' argument
parser.add_argument(
    "--state",
    choices=["PROD", "REW", "ALL"],
    help="Specify PROD to collect only papers in production, REW for only papers in review, and ALL for all papers",
    default="ALL",
)

args = parser.parse_args()
if args.journal != "JCOM":
    raise NotImplementedError("Sorry, only JCOM for now.")


connection = mariadb.connect(**WJAPP_JCOM_IMPORT_CONNECTION_PARAMS)
cursor = connection.cursor()

prod_states = [6, 14, 15, 16, 17, 18, 19, 20, 26]
rew_states = [1, 2, 3, 4, 7, 8, 10]
if args.state == "PROD":
    states = prod_states
elif args.state == "REW":
    states = rew_states
else:
    states = rew_states + prod_states

cursor.execute(
    f"""SELECT d.preprintId
    FROM
      Document d
      LEFT JOIN Version v ON d.documentCod=v.documentCod
    WHERE
      v.iscurrentversion=1
      AND v.stateCod in ( {",".join(map(str, states))} )
;
""",  # noqa: S608
)
print("\n".join([row[0] for row in cursor.fetchall()]))  # noqa: T201
