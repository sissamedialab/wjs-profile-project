"""Test (apache) redirects."""
import re

import requests
from django.core.management.base import BaseCommand
from requests.auth import HTTPBasicAuth
from utils.logger import get_logger

logger = get_logger(__name__)

# warning: remember that the last item is a regexp: () have special meaning
TESTS = (
    # Landing page
    ("/archive/21/07/JCOM_2107_2022_A02", 301, "/article/pubid/JCOM_2107_2022_A02/"),
    ("/archive/21/07/JCOM_2107_2022_A02/", 301, "/article/pubid/JCOM_2107_2022_A02/"),
    ("/archive/21/07/JCOM_2107_2022_A02/ciao", 301, "/article/pubid/JCOM_2107_2022_A02/ciao"),
    #     - sub documents / children
    ("/archive/16/01/JCOM_1601_2017_C01/JCOM_1601_2017_C02", 301, "/article/pubid/JCOM_1601_2017_C02/"),
    ("/archive/02/04/C020401/C020402", 301, "/article/pubid/C020402/"),
    ("/archive/09/04/Jcom0904(2010)C01/Jcom0904(2010)C02", 301, r"/article/pubid/Jcom0904\(2010\)C02/"),
    #     - old-style pubid
    ("/archive/01/01/E0101", 301, "/article/pubid/E0101/"),
    ("/archive/09/04/Jcom0904(2010)E", 301, r"/article/pubid/Jcom0904\(2010\)E/"),
    #     - non standard-issue 12/3-4
    ("/archive/12/3-4/JCOM1203(2013)A04", 301, r"/article/pubid/JCOM1203\(2013\)A04/"),
    ("/archive/12/3-4/JCOM1203(2013)C01/JCOM1203(2013)C02", 301, r"/article/pubid/JCOM1203\(2013\)C02/"),
    #
    # Issue
    ("/archive/03/03", 301, r"/issue/(\d+)/info"),
    ("/archive/03/03/", 301, r"/issue/(\d+)/info/"),
    #
    # Galleys
    ("/sites/default/files/documents/JCOM_2107_2022_A02.pdf", 301, r"/article/(\d+)/galley/(\d+)/download/"),
    ("/sites/default/files/documents/Jcom0904(2010)E_it.pdf", 301, r"/article/(\d+)/galley/(\d+)/download/"),
    #     - with language
    ("/sites/default/files/documents/JCOM_2002_2021_A01_en.pdf", 301, r"/article/(\d+)/galley/(\d+)/download/"),
    ("/sites/default/files/documents/JCOM_2002_2021_A01_pt.epub", 301, r"/article/(\d+)/galley/(\d+)/download/"),
    #     - citation_pdf_url (for google scholar, must be sibling or the paper's landing page)
    #     - old citation_pdf_url bring to galley
    ("/archive/20/02/JCOM_2002_2021_A01_en.pdf", 301, r"/article/(\d+)/galley/(\d+)/download/"),
    ("/archive/22/01/JCOM_2201_2023_N01.pdf", 301, r"/article/(\d+)/galley/(\d+)/download/"),
    ("/archive/21/07/JCOM_2107_2022_C01/JCOM_2107_2022_C07.pdf", 301, r"/article/(\d+)/galley/(\d+)/download/"),
    ("/archive/09/04/Jcom0904(2010)C01/Jcom0904(2010)C02.pdf", 301, r"/article/(\d+)/galley/(\d+)/download/"),
    #     - new citation_pdf_url bring to galley
    #       WARNING: cannot write a generic test because the galleyid
    #       appears in the "src" part (and may change because of
    #       import order)
    # NON-GENERIC: ("/article/pubid/JCOM_2002_2021_A01/1234", 301, r"/article/(\d+)/galley/(\d+)/download/"),
    #
    # Archive and volumes
    ("/archive", 301, "/articles/"),
    ("/archive/01", 301, "/articles/"),
    ("/archive/01/", 301, "/articles/"),
    #
    # Supplementary material / attachments
    ("/sites/default/files/documents/supplementary_material/JCOM_2106_2022_Y01_ATTACH_1.pdf", 301, ""),
)


class Command(BaseCommand):
    help = "Test (apache) redirects."  # noqa

    def handle(self, *args, **options):
        """Command entry point."""
        for request_path, expected_http_code, expected_location_path in TESTS:
            scheme_and_domain = f'{options["proto"]}://{options["domain"]}'
            url = f"{scheme_and_domain}{request_path}"

            basic_auth = None
            if options["auth"]:
                basic_auth = HTTPBasicAuth(*(options["auth"].split(":")))

            response = requests.get(
                url=url,
                verify=options["ssl_no_verify"],
                allow_redirects=False,
                auth=basic_auth,
            )

            if response.status_code != expected_http_code:
                self.error(f'got {response.status_code} (vs {expected_http_code}) for "{url}"')

            else:
                if expected_http_code in [301, 302]:
                    location_path = response.headers["Location"].replace(scheme_and_domain, "")
                    if match_obj := re.search(expected_location_path, location_path):
                        self.notice(f'"{url}" ok')
                        logger.debug(f"Match obj: {match_obj}")
                    else:
                        self.error(f"Got {location_path} (vs {expected_location_path}) for {url}")
                else:
                    self.error("WRITEME")

    def notice(self, msg):
        """Emit a notice."""
        self.stdout.write(self.style.SUCCESS(msg))

    def error(self, msg):
        """Emit an error."""
        self.stdout.write(self.style.ERROR(msg))

    def add_arguments(self, parser):
        """Add arguments to command."""
        parser.add_argument(
            "--domain",
            default="jcom.sissa.it",
            help="The domain to test. Defaults to %(default)s.",
        )
        parser.add_argument(
            "--proto",
            default="https",
            help="Protocol / scheme of the request. Defaults to %(default)s.",
        )
        parser.add_argument(
            "--ssl-no-verify",
            action="store_false",
            help="Do not verify TLS certificate.",
        )
        parser.add_argument(
            "--auth",
            help='HTTP Basic Auth in the form "user:passwd" (should be useful only for test sites).',
        )
