"""Forms for the synchronization of article metadata between TeX sources and DB."""

import difflib
import re
from dataclasses import dataclass
from urllib.parse import urlencode

import pycountry
from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db.models import Case, IntegerField, OuterRef, QuerySet, Subquery, When
from django.urls import reverse
from django.utils import translation
from django.utils.html import escape
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _
from submission import models as submission_models
from submission.models import Article
from utils.management.commands.test_fire_event import create_fake_request

from wjs.jcom_profile.models import Correspondence
from wjs.jcom_profile.utils import get_eo_user, render_template_from_setting

from .. import communication_utils
from ..logic__production import reunite_divided_kwds
from ..models import Message

Account = get_user_model()


class SyncLanguageForm(forms.ModelForm):
    """
    Form used to synchronize the language between TeX and DB.

    This is a ModelForm on Article: saving it writes the TeX language onto the article's
    ``language`` field. The ``language`` field is not editable: it just shows the language
    extracted from the TeX source. The form is validated (also at GET) so that the template can
    disable the submit button when the TeX language does not match any language known to Janeway.
    """

    action = forms.CharField(widget=forms.HiddenInput(), initial="sync_language")
    language = forms.CharField(label=_("Language"), disabled=True, required=True)

    class Meta:
        model = submission_models.Article
        fields = ["language"]

    def __init__(self, texdata, *args, **kwargs):
        """Store the TeX data and bind the language extracted from it to the field."""
        self.texdata = texdata
        super().__init__(*args, **kwargs)
        self.language_names = dict(submission_models.LANGUAGE_CHOICES)
        self.tex_language = texdata.data.get("language")
        # Capture the DB language now: once the form is validated, _post_clean() copies the
        # (TeX) cleaned value onto self.instance, so self.instance.language can no longer be
        # relied upon to hold the original DB value.
        self.db_language = self.instance.language
        # The field is disabled, so its value comes from self.initial (which a ModelForm
        # populates from the instance); override it so we display and save the TeX language.
        self.initial["language"] = self.tex_language
        self.fields["language"].help_text = (
            f"TeX: {self.language_names.get(self.tex_language, self.tex_language)} "
            f"vs DB: {self.language_names.get(self.db_language, self.db_language)}"
        )

    def clean_language(self):
        """Ensure the TeX language in one of the known ones."""
        tex_language = self.cleaned_data["language"]
        if tex_language not in self.language_names:
            raise ValidationError(f"Unknown TeX language '{tex_language}'!")
        return tex_language

    def should_sync(self) -> bool:
        """Tell if DB and TeX are out of sync."""
        return self.tex_language != self.db_language

    def sync(self):
        """
        Validate and persist the language.

        Raise:
          ValueError: if the form does not validate or if saving fails.
        """
        if not self.is_valid():
            raise ValueError(self.errors.as_text())
        try:
            return self.save()
        except Exception as e:  # noqa: BLE001 - surface any persistence failure as a ValueError
            raise ValueError(str(e)) from e


class SyncLicenseForm(forms.Form):
    """
    Form used to receive the green-light to synchronize the license between TeX and DB.

    The ``license`` field is not editable: it just holds the pk of the journal's license that
    matches the one extracted from the TeX source. The form is validated (also at GET) so that
    the template can disable the submit button when the TeX license does not match any license
    known to the journal.
    """

    action = forms.CharField(widget=forms.HiddenInput(), initial="sync_license")
    license = forms.CharField(label=_("License"), disabled=True, required=True)  # noqa: A003 (ruff and flake disagree)

    def __init__(self, texdata, *args, **kwargs):
        """Store the TeX data and bind the matching license's pk to the field."""
        self.texdata = texdata
        super().__init__(*args, **kwargs)
        # Map the slugified short_name of each of the journal's licenses to the license object.
        journal = texdata.workflow.article.journal
        self.licenses_mapping = {
            slugify(licence.short_name): licence
            for licence in submission_models.Licence.objects.filter(journal=journal)
        }
        # Note that the TeX command sequence from which the license is extracted was
        # \publicationLicence{{{ article.license.short_name|slugify }}}
        self.tex_license_slugified = texdata.data.get("licence")
        self.tex_license = self.licenses_mapping.get(self.tex_license_slugified)
        if self.tex_license:
            self.fields["license"].initial = self.tex_license.pk
        self.db_license = texdata.workflow.article.license
        self.fields["license"].help_text = (
            f"TeX: {self.tex_license.short_name if self.tex_license else self.tex_license_slugified} "
            f"vs DB: {self.db_license.short_name if self.db_license else None}"
        )

    def clean_license(self):
        """Ensure the TeX license matches one of the journal's licenses."""
        if self.tex_license_slugified not in self.licenses_mapping:
            raise ValidationError(f"Unknown TeX license '{self.tex_license_slugified}'!")
        return self.cleaned_data["license"]

    def should_sync(self) -> bool:
        """Tell if DB and TeX are out of sync."""
        return self.tex_license != self.db_license

    def sync(self):
        """
        Validate and persist the license.

        Raise:
          ValueError: if the form does not validate or if saving fails.
        """
        if not self.is_valid():
            raise ValueError(self.errors.as_text())
        article = self.texdata.workflow.article
        article.license = self.tex_license
        try:
            article.save()
        except Exception as e:  # noqa: BLE001 - surface any persistence failure as a ValueError
            raise ValueError(str(e)) from e


class SyncTitleAbstractForm(forms.Form):
    """
    Form used to synchronize title and abstract between TeX and DB.

    The ``title`` and ``abstract`` fields are not editable: they just show the values extracted
    from the TeX source. The comparison with the DB values is computed in __init__;
    get_form_context_data() returns the data the view should merge into its template context
    (the form itself included as ``form_titleabstract``).
    """

    action = forms.CharField(widget=forms.HiddenInput(), initial="sync_titleabstract")
    title = forms.CharField(label=_("Title"), disabled=True, required=True)
    # NB: errata and such might not have an abstract
    abstract = forms.CharField(label=_("Abstract"), widget=forms.Textarea, disabled=True, required=False)

    def __init__(self, texdata, *args, **kwargs):
        """Store the TeX data and bind the title/abstract extracted from it to the fields."""
        self.texdata = texdata
        super().__init__(*args, **kwargs)
        self.tex_title = texdata.data.get("title")
        # The tex abstract can have newlines here and there, so we adapt it (see also wjs/specs#1773):
        tex_abstract = re.sub(r"\n", " ", texdata.data.get("abstract") or "")
        self.tex_abstract = re.sub(r"  +", " ", tex_abstract)

        # Read the DB values in the article's language.
        article = texdata.workflow.article
        lang = pycountry.languages.get(alpha_3=article.language).alpha_2
        with translation.override(lang):
            self.db_title = article.title
            # Note that the DB abstract is wrapped with <p> by the TinyMCE widget.
            db_abstract = re.sub(r"^<p>", "", article.abstract or "")
        self.db_abstract = re.sub(r"</p>$", "", db_abstract)

        # The fields are disabled, so their values come from self.initial.
        self.initial["title"] = self.tex_title
        self.initial["abstract"] = self.tex_abstract
        self.fields["title"].help_text = f"TeX: {self.tex_title} vs DB: {self.db_title}"

    def should_sync(self) -> bool:
        """Tell if DB and TeX are out of sync."""
        return self.tex_title != self.db_title or self.tex_abstract != self.db_abstract

    def sync(self):
        """
        Validate and persist title and abstract.

        Raise:
          ValueError: if the form does not validate or if saving fails.
        """
        if not self.is_valid():
            raise ValueError(self.errors.as_text())
        article = self.texdata.workflow.article
        # The title and abstract of the tex are saved in the correspondent translation of the article.
        lang = pycountry.languages.get(alpha_3=article.language).alpha_2
        try:
            with translation.override(lang):
                article.title = self.cleaned_data["title"]
                article.abstract = self.cleaned_data["abstract"]
                article.save()
        except Exception as e:  # noqa: BLE001 - surface any persistence failure as a ValueError
            raise ValueError(str(e)) from e

    def get_form_context_data(self) -> dict:
        """Return title/abstract-related context to be merged into the view's context."""
        context = {"form_titleabstract": self}
        # Include a diff-like display of the abstract
        if self.tex_abstract != self.db_abstract:
            context["abstract_diff_html"] = self._abstract_diff_html()
        return context

    def _abstract_diff_html(self) -> str:
        """
        Return an HTML fragment marking word-level differences between the DB and TeX abstracts.

        DB-only text is wrapped in <del> (it will be replaced by the sync), TeX-only text in
        <ins> (it will be written to the DB). Every word is escaped, so the result is safe to
        render with the |safe template filter.
        """
        db_words = self.db_abstract.split()
        tex_words = self.tex_abstract.split()
        matcher = difflib.SequenceMatcher(a=db_words, b=tex_words, autojunk=False)
        parts = []
        for op, db1, db2, tex1, tex2 in matcher.get_opcodes():
            if op in ("replace", "delete"):
                parts.append(f"<del>{escape(' '.join(db_words[db1:db2]))}</del>")
            if op in ("replace", "insert"):
                parts.append(f"<ins>{escape(' '.join(tex_words[tex1:tex2]))}</ins>")
            if op == "equal":
                parts.append(escape(" ".join(db_words[db1:db2])))
        return " ".join(parts)


class SyncKeywordsForm(forms.Form):
    """
    Form used to receive the green-light to synchronize the keywords between TeX and DB.

    This form is perculiar because keywords comparison is (was?) complicated by the possibility of
    badly-split kwds. Please refer to the template sync_texdb/keywords.html for a clearer picture.
    """

    action = forms.CharField(widget=forms.HiddenInput(), initial="sync_keywords")

    def __init__(self, texdata, *args, **kwargs):
        """Store the TeX data and compute the TeX/DB keywords comparison."""
        self.texdata = texdata
        super().__init__(*args, **kwargs)
        # Remember that kwds_db and kwds_tex are QuerySets!
        self.kwds_db = self._get_db_kwds()
        try:
            self.kwds_tex = self._match_tex_db_kwds(
                kwds_strings=self._get_tex_kwds(),
                article=self.texdata.workflow.article,
            )
        except ValueError as e:
            # Register the problem as a (non-field) form error, so that the form does not
            # validate and the template can show the message; use empty QuerySets so that
            # should_sync() & co. keep working.
            self.add_error(None, str(e))
            self.kwds_tex = submission_models.Keyword.objects.none()
        self.kwds_db_raw = texdata.workflow.article.keywords.all()

    def _get_db_kwds(self) -> QuerySet:
        """
        Get the "real" article kwds.

        Deal with the case when the kwds on the DB have been erroneously split by Janeway manger UI (it splits
        a kwd on the ",", so that a single kwd "aaa, bbb" becomes two kwds: "aaa" and "bbb").

        """
        # Note that kwds are implicitly ordered because of core.models_utils.M2MOrderedThroughField, however, when we
        # "reunite" them, we get back a list of ids of kwds that might not even be linked to the article. So we must
        # ensure that the order is "maintained".
        good, __ = reunite_divided_kwds(self.texdata.workflow.article.keywords.all())
        order_of_ids = Case(*[When(pk=pk, then=pos) for pos, pk in enumerate(good)])
        return submission_models.Keyword.objects.filter(id__in=good).order_by(order_of_ids)

    def _get_tex_kwds(self) -> list[str]:
        """Get the kwds strings that exist in the TeX file."""
        return self.texdata.data.get("keywords")

    @staticmethod
    def _match_tex_db_kwds(kwds_strings: list[str], article: Article) -> QuerySet:
        """
        Match a list of strings to DB keywords.

        Use the article to get language and journal.

        Raise:
          ValueError: if any kwd from the TeX does not exists in the DB.

        """
        # Expect kwds to be in the article's language, so we need to compare the received string with the
        # appropriate translation.
        lang = pycountry.languages.get(alpha_3=article.language).alpha_2
        # Apparently `annotate` is not patched by django-modeltranslation
        # https://django-modeltranslation.readthedocs.io/en/latest/usage.html#multilingual-manager-1
        # so we have to manually select the correct field to use:
        lang_field = f"word_{lang}"
        with translation.override(lang):
            tex_kwds = (
                submission_models.Keyword.objects.filter(
                    journal=article.journal,
                    **{f"{lang_field}__in": kwds_strings},
                )
                # We need to manully order the queryset to maintain the order we found in the TeX
                .annotate(
                    manual_order=Case(
                        *[When(**{lang_field: word}, then=pos) for pos, word in enumerate(kwds_strings)],
                        output_field=IntegerField(),
                    ),
                ).order_by("manual_order")
            )

        if len(kwds_strings) != tex_kwds.count():
            tex_kwds_indb = set(tex_kwds.values_list("word", flat=True))
            msg = ""
            if only_tex := set(kwds_strings) - tex_kwds_indb:
                msg += f" Kwds from TeX that do not exist in the DB: {'; '.join(only_tex)}."
            if only_db := tex_kwds_indb - set(kwds_strings):
                msg += f" 😱 This cannot be! Only in DB: {'; '.join(only_db)}."
            msg += " Please contact assistance!"
            raise ValueError(msg)
        return tex_kwds

    def should_sync(self) -> bool:
        """Tell if DB and TeX keywords are out of sync."""
        return (
            list(self.kwds_db.values_list("id", flat=True)) != list(self.kwds_tex.values_list("id", flat=True))
            or self.kwds_db_raw.count() != self.kwds_tex.count()
        )

    def sync(self):
        """
        Validate and persist the keywords.

        Raise:
          ValueError: if the form does not validate or if saving fails.
        """
        if not self.is_valid():
            raise ValueError(self.errors.as_text())
        article = self.texdata.workflow.article
        try:
            # Using article.keywords.set(self.kwds_tex) gives
            # create_m2m_ordered_through_manager.<locals>.M2MOrderedThroughManager.add() got
            #   an unexpected keyword argument 'through_defaults'
            # So I fallback to the following one-by-one approach:
            article.keywords.clear()
            for order, kwd in enumerate(self.kwds_tex):
                submission_models.KeywordArticle.objects.update_or_create(
                    article=article,
                    keyword=kwd,
                    defaults={"order": order},
                )
        except Exception as e:  # noqa: BLE001 - surface any persistence failure as a ValueError
            raise ValueError(str(e)) from e

    def get_form_context_data(self) -> dict:
        """Return keyword-related context to be merged into the view's context."""
        # Remember that tex_data holds kwds as QuerySets!
        context = {
            "form_keywords": self,
            "kwds_tex": self.kwds_tex,
            "kwds_db": self.kwds_db,
        }
        if self.kwds_db_raw.count() != self.kwds_tex.count():
            context["kwds_db_raw"] = self.kwds_db_raw
        return context


class SyncAuthorsForm(forms.Form):
    """
    Form used to receive the green-light to synchronize the authors between TeX and DB.

    The mapping of the TeX authors onto DB accounts (and the related errors) is computed in
    __init__; get_form_context_data() returns the data the view should merge into its template
    context (the form itself included as ``form_authors``).
    """

    action = forms.CharField(widget=forms.HiddenInput(), initial="sync_authors")

    # TODO: refactor with data_extractor.Author (or drop?)
    @dataclass
    class SimilarAccount:
        """Convenience."""

        pk: int
        last_name: str
        first_name: str
        email: str
        orcid: str | None
        country: str | None
        institution: str | None
        biography: str | None
        link_to_mapping: str | None

    @dataclass
    class AuthorStruct:
        """Convenience."""

        last_name: str
        first_name: str
        email: str
        orcid: str | None
        extra_email: str | None
        account_id: int | None
        warning: str | None
        similar_accounts: QuerySet[Account] | None = None
        must_be_created: bool = False

    def __init__(self, texdata, *args, **kwargs):
        """Store the TeX data and compute the TeX/DB authors mapping."""
        self.texdata = texdata
        super().__init__(*args, **kwargs)
        self.authors_tex = texdata.data.get("authors_data")
        self.authors_db = self._get_db_authors()
        self.authors_map = self._map_authors()
        self.authors_errors = self._find_authors_errors()
        # Register the problems as (non-field) form errors, so that the form does not validate.
        for error in self.authors_errors:
            self.add_error(None, error)

    def _get_db_authors(self) -> QuerySet:
        """
        Get the article's authors.

        Do not just use article.authors.all() because the order is not guaranteed.
        """
        article = self.texdata.workflow.article
        subq = Subquery(
            submission_models.FrozenAuthor.objects.filter(
                article=article,
                author__id=OuterRef("id"),
            ).values_list("order"),
        )
        return article.author_accounts.all().annotate(order=subq).order_by("order")

    def _map_authors(self) -> list["SyncAuthorsForm.AuthorStruct"]:
        """
        Use TeX data to retrieve Accounts from DB.

        When no suitable account can be found, data suitable to create a new account is also prepared.
        """
        authors_map = []
        for tex_author in self.authors_tex:
            accountstruct = self._find_corresponding_account(tex_author)
            authors_map.append(accountstruct)
        return authors_map

    def _find_corresponding_account(self, tex_author: dict) -> "SyncAuthorsForm.AuthorStruct":
        """
        Find an Account in the DB, given some author data.

        Try to find the account in many ways:
        - by email
        - by orcid (if available)
        - by old wjapp correspondence / mapping using the email (aka via extra_email)
        - by first + last on article.authors
        - by first-initial + last on article.authors
        - by first + last on all DB (select first and add a "warning" if >1)
        - by first-initial + last on all DB (select first and add a "warning" if >1)
        - give up and propose to add a new Account
          - notify the author of the new account

        If no account in the DB can be found, an Account() object populated with data from the TeX is returned.
        """
        article = self.texdata.workflow.article
        try:
            account = Account.objects.get(email=tex_author["email"])
        except Account.DoesNotExist:
            pass
        else:
            return self.AuthorStruct(
                last_name=account.last_name,
                first_name=account.first_name,
                email=account.email,
                orcid=account.orcid,
                extra_email=None,
                account_id=account.id,
                must_be_created=False,
                warning=None,
            )

        if tex_author["orcid"]:
            try:
                account = Account.objects.get(orcid=tex_author["orcid"])
            except Account.DoesNotExist:
                pass
            else:
                return self.AuthorStruct(
                    last_name=account.last_name,
                    first_name=account.first_name,
                    email=account.email,
                    orcid=account.orcid,
                    extra_email=None,  # FIXME!
                    account_id=account.id,
                    must_be_created=False,
                    warning=None,
                )

        try:
            wjapp_mapping = Correspondence.objects.get(email=tex_author["email"])
        except Correspondence.DoesNotExist:
            pass
        else:
            return self.AuthorStruct(
                last_name=wjapp_mapping.account.last_name,
                first_name=wjapp_mapping.account.first_name,
                email=wjapp_mapping.account.email,
                orcid=wjapp_mapping.account.orcid,
                extra_email=None,  # FIXME!
                account_id=wjapp_mapping.account.id,
                must_be_created=False,
                warning=None,
            )

        similaraccounts_warning = """Similar accounts: either set the orcid on the existing account (if the TeX has
        it), or create/edit a "correspondence" (a mapping) with the TeX email."""
        try:
            article.author_accounts.get(
                first_name=tex_author["first_name"],
                last_name=tex_author["surname"],
            )
        except Account.DoesNotExist:
            pass
        except Account.MultipleObjectsReturned:
            # Any other euristics after this would contain these accounts also.
            # So we can stop here and ask for help.
            similar_accounts = article.author_accounts.filter(
                first_name=tex_author["first_name"],
                last_name=tex_author["surname"],
            )
            return self.AuthorStruct(
                last_name="NA",
                first_name="NA",
                email="NA",
                orcid="NA",
                extra_email=None,
                account_id=None,
                must_be_created=False,
                warning=similaraccounts_warning,
                similar_accounts=self._enrich_similar_accounts(tex_author, similar_accounts),
            )

        try:
            article.author_accounts.get(
                last_name=tex_author["surname"],
            )
        except Account.DoesNotExist:
            pass
        except Account.MultipleObjectsReturned:
            # Any other euristics after this would contain these accounts also.
            # So we can stop here and ask for help.
            similar_accounts = article.author_accounts.filter(
                last_name=tex_author["surname"],
            )
            return self.AuthorStruct(
                last_name="NA",
                first_name="NA",
                email="NA",
                orcid="NA",
                extra_email=None,
                account_id=None,
                must_be_created=False,
                warning=similaraccounts_warning,
                similar_accounts=self._enrich_similar_accounts(tex_author, similar_accounts),
            )

        try:
            Account.objects.get(
                first_name=tex_author["first_name"],
                last_name=tex_author["surname"],
            )
        except Account.DoesNotExist:
            pass
        except Account.MultipleObjectsReturned:
            # Any other euristics after this would contain these accounts also.
            # So we can stop here and ask for help.
            similar_accounts = Account.objects.filter(
                first_name=tex_author["first_name"],
                last_name=tex_author["surname"],
            )
            return self.AuthorStruct(
                last_name="NA",
                first_name="NA",
                email="NA",
                orcid="NA",
                extra_email=None,
                account_id=None,
                must_be_created=False,
                warning=similaraccounts_warning,
                similar_accounts=self._enrich_similar_accounts(tex_author, similar_accounts),
            )

        try:
            Account.objects.get(
                first_name__startswith=tex_author["first_name"][0],
                last_name__endswith=tex_author["surname"].split(" ")[-1],
            )
        except Account.DoesNotExist:
            pass
        except Account.MultipleObjectsReturned:
            # Any other euristics after this would contain these accounts also.
            # So we can stop here and ask for help.
            similar_accounts = Account.objects.filter(
                first_name__startswith=tex_author["first_name"][0],
                last_name__endswith=tex_author["surname"].split(" ")[-1],
            )
            return self.AuthorStruct(
                last_name="NA",
                first_name="NA",
                email="NA",
                orcid="NA",
                extra_email=None,
                account_id=None,
                must_be_created=False,
                warning=similaraccounts_warning,
                similar_accounts=self._enrich_similar_accounts(tex_author, similar_accounts),
            )

        return self.AuthorStruct(
            last_name=tex_author["surname"],
            first_name=tex_author["first_name"],
            email=tex_author["email"],
            orcid=tex_author["orcid"],
            extra_email=None,
            account_id=None,
            must_be_created=True,
            warning=None,
        )

    def _find_authors_errors(self) -> list[str]:
        """
        Tell if there is some unresolvable error in the authors list.

        ATM only check that owner and corresondence author are in the list of mapped authors.
        """
        article = self.texdata.workflow.article
        errors = []
        proposed_authors_ids = [a.account_id for a in self.authors_map if a.account_id]
        if article.owner.id not in proposed_authors_ids:
            errors.append(_("No owner in the new authors list!"))
        if article.correspondence_author.id not in proposed_authors_ids:
            errors.append(_("No correspondence author in the new authors list!"))
        if any(am.similar_accounts for am in self.authors_map):
            errors.append(_("Similar accounts exist!"))
        return errors

    def _enrich_similar_accounts(self, tex_author: dict, similar_accounts: QuerySet) -> list[SimilarAccount]:
        """
        Enrich the list of similar accounts by computing the appropriate correspondence URL.

        Since it's possible to have existing mappings/correspondences with empty email, the idea here is to give the
        operator a direct link to the most appropriate action: edit the exising correspondence if the email is missing
        or add a new correspondence.

        """
        result = []
        for i, account in enumerate(similar_accounts):
            if not Correspondence.objects.filter(account=account, email__isnull=True).exists():
                link_to_mapping = reverse("admin:jcom_profile_correspondence_add")
                querystring = urlencode(
                    {
                        "account": account.pk,
                        "email": tex_author["email"],
                        # "source" and "user_cod" are mandatory for a Correspondence
                        # below we make-up suitable values:
                        "source": "tex",
                        "user_cod": self.texdata.workflow.article.pk * 100 + i,
                    },
                )
            else:
                correspondence = Correspondence.objects.filter(account=account, email__isnull=True).first()

                link_to_mapping = reverse("admin:jcom_profile_correspondence_change", args=[correspondence.pk])
                querystring = urlencode(
                    {
                        "email": tex_author["email"],
                        "source": "tex",
                        "user_cod": self.texdata.workflow.article.pk * 100 + i,
                    },
                )
            link_to_mapping = f"{link_to_mapping}?{querystring}"

            result.append(
                self.SimilarAccount(
                    pk=account.pk,
                    last_name=account.last_name,
                    first_name=account.first_name,
                    email=account.email,
                    orcid=account.orcid,
                    country=account.country.name if account.country else "",
                    institution=account.institution,
                    biography=account.biography,
                    link_to_mapping=link_to_mapping,
                ),
            )
        return result

    def _log_new_coauthor_created(self, newaccount: Account) -> Message:
        """Send a notification to the newly-created account."""
        article = self.texdata.workflow.article
        fake_request = create_fake_request(
            user=get_eo_user(article.journal),
            journal=article.journal,
        )
        message_subject = render_template_from_setting(
            setting_group_name="wjs_review",
            setting_name="add_coauthor_manually_subject",
            journal=article.journal,
            request=fake_request,
            context={"article": article},
            template_is_setting=True,
        )
        message_body = render_template_from_setting(
            setting_group_name="wjs_review",
            setting_name="add_coauthor_manually_body",
            journal=article.journal,
            request=fake_request,
            context={"article": article, "newaccount": newaccount},
            template_is_setting=True,
        )
        return communication_utils.log_operation(
            article=article,
            message_subject=message_subject,
            message_body=message_body,
            actor=None,
            recipients=[newaccount],
            verbosity=Message.MessageVerbosity.EMAIL,
            flag_as_read=True,
            flag_as_read_by_eo=True,
        )

    def should_sync(self) -> bool:
        """Tell if DB and TeX authors are out of sync."""
        return list(self.authors_db.values_list("id", flat=True)) != [a.account_id for a in self.authors_map]

    def sync(self):
        """
        Validate and persist the authors; also freeze authors.

        Raise:
          ValueError: if the form does not validate or if saving fails.
        """
        if not self.is_valid():
            raise ValueError(self.errors.as_text())
        article = self.texdata.workflow.article
        try:
            submission_models.FrozenAuthor.objects.filter(article=article).delete()
            for am in self.authors_map:
                if am.must_be_created:
                    author = Account.objects.create(
                        first_name=am.first_name,
                        last_name=am.last_name,
                        email=am.email,
                        orcid=am.orcid,
                    )
                    self._log_new_coauthor_created(author)
                else:
                    author = Account.objects.get(
                        id=am.account_id,
                    )
                    # FIXME: update with data from DB:
                    # - first/last name (only if longer than DB)
                    # - email (only add to jcom_profile.Correspondence)
                    # - orcid
                    # https://gitlab.sissamedialab.it/wjs/specs/-/issues/1804

                author.snapshot_as_author(article)
        except Exception as e:  # noqa: BLE001 - surface any persistence failure as a ValueError
            raise ValueError(str(e)) from e

    def get_form_context_data(self) -> dict:
        """Return authors-related context to be merged into the view's context."""
        return {
            "form_authors": self,
            "authors_tex": self.authors_tex,
            "authors_db": self.authors_db,
            "authors_map": self.authors_map,
            "authors_errors": self.authors_errors,
        }
