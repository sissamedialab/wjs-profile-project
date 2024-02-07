import json
import os
import threading
import time

import jwt
import requests
from core.models import Account
from django.conf import settings
from django.db.models import F, Q, QuerySet, Subquery, Value
from django.http import QueryDict
from requests_toolbelt.multipart.encoder import MultipartEncoder
from utils.logger import get_logger
from utils.setting_handler import get_setting

from .models import Correspondence, ProphyAccount, ProphyCandidate

logger = get_logger(__name__)


class Prophy:
    """Prophy site interaction management"""

    def __init__(self, article):
        self.article = article

    def article_prophy_upload(self):
        output = ""
        if not self.prophy_upload_enabled():
            logger.debug("prophy upload for this journal is not enabled")
            return output
        else:
            prophy_url = settings.PROPHY_URL
            api_key = settings.PROPHY_API_KEY

            pdf_path = ""
            all_manuscripts = self.article.manuscript_files.all()
            for file in all_manuscripts:
                if "PDF" in file.label:
                    # TODO: verify if label PDF is correct for main pdf
                    # TODO: do not use path but function of article
                    pdf_path = f"{settings.BASE_DIR}/files/articles/{self.article.id}/{file.uuid_filename}"
                    break

            if not os.path.exists(pdf_path):
                logger.warning(f"pdf file of the manuscript does not exists: {self.article.id}")
                return output

            corr_auth = self.article.correspondence_author
            # used article id as origin: "origin_id": self.article.id,
            prophy_journal_value = get_setting(
                "wjs_prophy",
                "prophy_journal",
                self.article.journal,
            ).processed_value
            try:
                multipart_data = MultipartEncoder(
                    fields={
                        "file": ("source_file", open(str(pdf_path), "rb")),
                        # plain text fields
                        "api_key": api_key,
                        "organization": settings.PROPHY_ORGANIZATION,
                        "journal": prophy_journal_value,
                        "origin_id": f"{self.article.id}",
                        "title": self.article.title,
                        "abstract": self.article.abstract,
                        "authors_count": "1",
                        "author1_name": f"{corr_auth.first_name} {corr_auth.last_name}",
                    },
                )

                response = requests.post(
                    prophy_url,
                    data=multipart_data,
                    headers={"Content-Type": multipart_data.content_type},
                )

            except RuntimeError:
                logger.error(
                    f"error posting data to Prophy for article {self.article.id}. Trying to proceed anyway...",
                )

            if response.status_code == 200:
                self.store_json(response.text)
                logger.debug(f"response stored for article {self.article.id}")
                output = response.text
                # TODO: log_operation on success (to be shown in timeline)
            else:
                logger.error(
                    f"""response status code {response.status_code}; article: {self.article.id};
                    response: {response.text}""",
                )

        return output

    def async_article_prophy_upload(self):
        # async upload to prophy
        threading.Thread(target=self.article_prophy_upload).start()
        return

    def jwt_token(self, user):
        """Generate a token that can let the given user login into Prophy.

        The URL with the token brings the user to Prophy's list of candidates for the article.

        Logged-in users can access, for instance,:
        - the manuscript page
        - the list of candidates for the manuscript
        - the details for the candidates (that are public pages anyway)

        The users also has access to all papers uploaded to the same Prophy folder.
        NB: this will change around March '24.
        """
        # `manuscript_id` is the id or our Article on Prophy. I.e. not our Article.pk, but Prophy's.
        # In Prophy, our Article.pk is called `origin_id` (not used here, but in the upload).
        #
        # We store the correspondence between an Article.id and the relative prophy manuscript_id in all candidates for
        # the article, so we can just take the value from the first candidate.
        prophy_manuscript_id = ProphyCandidate.objects.filter(
            article=self.article.id,
        )[0].prophy_manuscript_id

        # `journal` is the name of the "team" on Prophy. This is usually also the name of
        # the folder/panel where the documents are available.
        #
        # Prophy automatically creates both the team and the folder (if necessary) during the first upload.
        prophy_journal_value = get_setting(
            "wjs_prophy",
            "prophy_journal",
            self.article.journal,
        ).processed_value

        # `iat` is the time at which the JWT token was issued (UNIX timestamp). Token expires in 1 hour.
        iat = round(time.time())

        # `name` and `email` are credentials of a journal editor/panel member, who looks at the system.
        #
        # Prophy will automatically create a user, give them access to this journal/panel and log them in.

        algorithm = "HS512"

        token = jwt.encode(
            payload={
                "sub": settings.PROPHY_JWT_SUB,
                "iat": iat,
                "organization": settings.PROPHY_ORGANIZATION,
                "journal": prophy_journal_value,
                "name": f"{user.first_name} {user.last_name}",
                "email": user.email,
                "manuscript_id": prophy_manuscript_id,
            },
            key=settings.PROPHY_JWT_KEY,
            algorithm=algorithm,
            headers={"alg": algorithm, "typ": "JWT"},
        )
        return token

    def jwt_token_url(self, user):
        return f"{settings.PROPHY_JWT_URL}{self.jwt_token(user)}"

    def store_json(self, text):
        data = ""
        try:
            data = json.loads(text)
        except ValueError as e:
            logger.error(f"Error reading json data: {e}")
            return

        # delete candidates for article if already loaded
        ProphyCandidate.objects.filter(
            article=self.article.id,
        ).delete()

        # Iterate through the json list
        for c in data["candidates"]:
            # Sometimes Prophy would send us candidates without an email (or with an invalid one).
            # This information is useless for us (we could not contact the candidate reviewer),
            # so we silently drop these entries and keep only the "good" ones.
            # TODO: use an email validator maybe?
            if c["email"] and "@" in c["email"]:
                # get or create prophy account
                prophy_account, _ = ProphyAccount.objects.get_or_create(author_id=c["author_id"])
                # Below, we want to overwrite (eventual) previous values with the values that we just got.
                # The rationale being that more recent value may be more correct.
                prophy_account.affiliation = c["affiliation"]
                prophy_account.articles_count = c["articlesCount"]
                prophy_account.authors_groups = c["authors_groups"]
                prophy_account.citations_count = c["citationsCount"]
                prophy_account.email = c["email"]
                prophy_account.h_index = c["hIndex"]
                prophy_account.name = c["name"]
                prophy_account.orcid = c["orcid"]
                prophy_account.url = c["url"]
                prophy_account.save()

                self.store_correspondence(prophy_account)

                prophy_candidate, created = ProphyCandidate.objects.get_or_create(
                    prophy_account=prophy_account,
                    article=self.article,
                    defaults={
                        "score": c["score"],
                        "prophy_manuscript_id": data["manuscript_id"],
                    },
                )

                if created:
                    # common case (probably "this is the only case, because we deleted all candidates
                    # for this article at l.143)
                    logger.debug(f'create candidate relation {c["author_id"]} {self.article.id}')
                else:
                    # can this happen?
                    logger.warning(
                        f"Prophy candidate {c['author_id']} for article {self.article.id}"
                        f"already exists with score {prophy_candidate.score} (vs. received {c['score']})"
                        f"and prophy manuscript id {prophy_candidate.prophy_manuscript_id}"
                        f"(vs. received {c['manuscript_id']}). Please check!",
                    )

        return

    def store_correspondence(self, prophy_account):
        """
        if email already in accounts or correspondence but not prophy
        add new correspondence (wjs_user_id, prophy, author_id, email)
        LOGIC:
        if email in correspondence
                  if not prophy add correspondence
                  else
                     if prophy and not same code, raise error? see (import from wjapp)
        return
        if email in account (not in correspondence otherwise already found)
                   add correspondence
        add case user cod different

        Attention: empty email in correspondence and in json
        """

        mapping_prophy = Correspondence.objects.filter(
            source="prophy",
            email=prophy_account.email,
        ).first()
        if mapping_prophy:
            logger.debug(f"mapping_prophy: {mapping_prophy}")
            prophy_account.correspondence = mapping_prophy
            prophy_account.save()
        else:
            mapping_not_prophy = Correspondence.objects.filter(
                email=prophy_account.email,
            ).first()
            if mapping_not_prophy:
                logger.debug(f"mapping_not_prophy: {mapping_not_prophy}")
                new_prophy_correspondence = Correspondence.objects.create(
                    user_cod=prophy_account.author_id,
                    source="prophy",
                    email=prophy_account.email,
                    account=mapping_not_prophy.account,
                )
                prophy_account.correspondence = new_prophy_correspondence
                prophy_account.save()
            else:
                mapping_account = Account.objects.filter(
                    email=prophy_account.email,
                ).first()
                if mapping_account:
                    logger.debug(f"mapping_account: {mapping_account}")
                    new_prophy_correspondence = Correspondence.objects.create(
                        user_cod=prophy_account.author_id,
                        source="prophy",
                        email=prophy_account.email,
                        account=mapping_account,
                    )
                    prophy_account.correspondence = new_prophy_correspondence
                    prophy_account.save()
        return

    def article_has_prophycandidates(self):
        """Tell if this article has any candidate reviewers suggested by Prophy."""
        return ProphyCandidate.objects.filter(article=self.article).exists()

    def get_not_account_article_prophycandidates(self, search_data: QueryDict) -> QuerySet[ProphyAccount]:
        """Return all candidate reviewers proposed by Prophy that do not have a matching (Janeway) Account.
        The purpose of this method is to integrate the list of possible reviewers in the "select reviewer"
        editor's page.
        The list already comprises Accounts, so we can omit Prophy candidates that have a "correspondence"
        to an Account.
        The entries in the queryset are annotated with a all annotations already used in the reviewers list.
        Most can be just set to `False` because, if the candidate was an author, or had already reviewed this article,
        he would also have been "promoted" to a full-fledged Account, and thus omitted from this list.
        The most interesting annotations are
        - `wjs_is_prophy_candidate` (self explanatory)
        - `wjs_is_only_prophy` (self explanatory)
        - `wjs_prophy_auth_url` the URL of the user's page on prophy. This can be given to the editor if he wants
        to examine the candidate.
        NB: `wjs_is_prophy_candidate` and `wjs_is_only_prophy` are both needed, because we could have an Account that
            also is a prophy candidate, and a prophy candidate that ha not yet any Account associated.
        """

        article_candidates = ProphyCandidate.objects.filter(
            article=self.article,
            prophy_account__correspondence__isnull=True,
        )
        prophy_accounts_candidates = ProphyAccount.objects.filter(
            id__in=Subquery(article_candidates.values("prophy_account")),
        ).annotate(
            full_name=F("name"),
            wjs_is_author=Value(False),
            is_active=Value(True),
            wjs_is_active_reviewer=Value(False),
            wjs_has_declined_current_review_round=Value(False),
            wjs_has_currently_completed_review=Value(False),
            wjs_is_past_reviewer=Value(False),
            wjs_has_delined_previous_review_round=Value(False),
            wjs_has_previously_completed_review=Value(False),
            wjs_is_prophy_candidate=Value(True),
            wjs_is_only_prophy=Value(True),
            wjs_prophy_auth_url=Value(settings.PROPHY_AUTH),
        )
        q_filters = None
        if search_data.get("search"):
            search_text = search_data.get("search").lower()
            q_filters = Q(
                Q(name__icontains=search_text) | Q(email__icontains=search_text),
            )
        if q_filters:
            prophy_accounts_candidates = prophy_accounts_candidates.filter(q_filters)

        return prophy_accounts_candidates.order_by("-is_active", "name")

    def prophy_upload_enabled(self):
        prophy_upload_enabled_value = get_setting(
            "wjs_prophy",
            "prophy_upload_enabled",
            self.article.journal,
        ).processed_value
        return prophy_upload_enabled_value
