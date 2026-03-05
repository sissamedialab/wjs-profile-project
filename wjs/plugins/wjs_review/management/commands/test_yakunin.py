import json
import shutil
from pathlib import Path

from django.core.management.base import BaseCommand

from ...logic import YakuninClient, YakuninPDFGenerationError, YakuninRequestError


class Command(BaseCommand):
    def add_arguments(self, parser):
        parser.add_argument("folder", type=str)
        parser.add_argument("--output", type=str, default="yakunin_recap.json")
        parser.add_argument("--outdir", type=str, default="yakunin_outputs")
        parser.add_argument("--clear", type=bool, default=True, help="Clear the output directory")

    def handle(self, folder, output, outdir, clear, **options):
        folder = Path(folder)
        outdir = Path(outdir)
        if clear:
            if outdir.exists():
                shutil.rmtree(outdir)
        outdir.mkdir(parents=True, exist_ok=True)

        results = {"success": [], "errors": []}

        for filepath in sorted(folder.iterdir()):
            if not filepath.is_file():
                continue

            name = filepath.name
            try:
                data = filepath.read_bytes()
                client = YakuninClient(file=data, filename=name)
                tmpdir, log = client.call_yakunin_mkpdf()

                target = outdir / name
                target.mkdir(parents=True, exist_ok=True)

                for item in Path(tmpdir).iterdir():
                    dest = target / item.name
                    if item.is_file():
                        shutil.copy2(item, dest)
                    else:
                        shutil.copytree(item, dest, dirs_exist_ok=True)

                shutil.rmtree(tmpdir)

                results["success"].append(
                    {
                        "file": name,
                    }
                )

            except YakuninRequestError as e:
                results["errors"].append(
                    {
                        "file": name,
                        "error_type": type(e).__name__,
                    }
                )

            except YakuninPDFGenerationError as e:
                results["errors"].append(
                    {
                        "file": name,
                        "error_log": client._log,
                        "error_type": type(e).__name__,
                    }
                )

        Path(output).write_text(json.dumps(results, indent=2))
