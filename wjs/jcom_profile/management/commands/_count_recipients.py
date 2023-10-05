"Helper script to call the munin_count_recipients mgmt cmd as a munin plugin."

# Not using https://github.com/samuel/python-munin because it seems a bit old (10 years since last edit).

import argparse
import os

from django.core import management


def main():
    parser = argparse.ArgumentParser(
        description="Munin plugin to count newsletter recipients. Please install as any other munin plugin.",
    )
    # Munin wants either "config" or nothing (but not "--config"). This usage of argparse is a bit confusing...
    # Munin with "fetch", nothing or any other parameter but "config" expects the values
    parser.add_argument(
        "run_type",
        nargs="?",
        default="",
        type=str,
        help='"config" print config data other args or nothing print values. See munin docs',
    )
    # do cli option parsing here to emit a user-friendly help message
    args = parser.parse_args()

    # Taken from janeway's manage.py:
    from utils import load_janeway_settings

    os.environ.setdefault("JANEWAY_SETTINGS_MODULE", "core.settings")
    load_janeway_settings()

    arg = ""
    if args.run_type == "config":
        arg = "config"
    management.call_command("munin_count_recipients", arg)
