"""Install WJS cron jobs.

Jobs are defined as:
  - name: identify the cron line
  - task: mgmt command to be used (but see also raw and raw-python)
  - raw: flag to run raw command ("task" will be run as is)
  - raw-python: flag to run raw python code inside venv ("task" will be prepended with the venv python)
  - time
  - type
"""

# Adapted from src/cron/management/commands/install_cron.py
import os

from crontab import CronTab
from django.conf import settings
from django.core.management.base import BaseCommand


def find_job(tab, comment):
    """WRITEME."""
    for job in tab:
        if job.comment == comment:
            return job
    return None


class Command(BaseCommand):
    """Installs cron jobs."""

    help = "Install WJS cron jobs."  # noqa

    def add_arguments(self, parser):
        """Add arguments."""
        parser.add_argument("--action", choices=["test", "dry-run"], default="")

    def handle(self, *args, **options):
        """Install cron jobs."""
        action = options.get("action")
        tab = CronTab(user=True)
        virtualenv = os.environ.get("VIRTUAL_ENV", None)

        cwd = settings.PROJECT_DIR.replace("/", "_")

        jobs = [
            {
                "name": f"{cwd}_send_newsletter_notifications",
                "time": 23,
                "task": "send_newsletter_notifications",
                "type": "daily",
            },
            {
                "name": f"{cwd}_check_wjapp_new_registrations",
                "time": 9,
                "task": "check_wjapp_new_registrations",
                "type": "daily",
            },
            {
                "name": f"{cwd}_pip_audit_check",
                "time": 6,
                "task": "pip-audit | sed '/Dependency not found/d;/Skip Reason/d;/------/d;'",
                "type": "daily",
                "raw-python": True,
            },
        ]

        for job in jobs:
            current_job = find_job(tab, job["name"])

            if current_job:
                print(f"{job['name']} cron job already exists.")
                continue

            if "raw-python" in job:
                if virtualenv:
                    command = f"{virtualenv}/bin/{job['task']}"
            elif "raw" in job:
                command = job["task"]
            else:
                django_command = f"{settings.BASE_DIR}/manage.py {job['task']}"
                if virtualenv:
                    command = f"{virtualenv}/bin/python3 {django_command}"
                else:
                    command = django_command

            cron_job = tab.new(command, comment=job["name"])

            if job.get("type") == "daily":
                cron_job.setall(f"0 {job['time']} * * *")
            elif job.get("type") == "hourly":
                cron_job.setall(f"0 */{job['time']} * * *")
            else:
                cron_job.minute.every(job["time"])

        if action == "test":
            print(tab.render())
        elif action == "dry-run":
            pass
        else:
            tab.write()
