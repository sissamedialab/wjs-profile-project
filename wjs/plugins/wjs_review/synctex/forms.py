"""Forms for the synchronization of article metadata between TeX sources and DB."""

import difflib
import re
from dataclasses import dataclass, field
from functools import cached_property
from typing import TYPE_CHECKING

import pycountry
import requests
from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Case, IntegerField, QuerySet, When
from django.utils import translation
from django.utils.html import escape
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _
from identifiers.models import Identifier
from plugins.wjs_submission.models import (
    ArticleCollaboration,
    ArticleSubmission,
    Collaboration,
    CollaborationRelation,
)
from plugins.wjs_submission.unique_check import check_article_unique
from submission import models as submission_models
from submission.models import Article
from utils.management.commands.test_fire_event import create_fake_request

from wjs.jcom_profile.models import Correspondence
from wjs.jcom_profile.utils import get_eo_user, render_template_from_setting

from .. import communication_utils
from ..logic__production import reunite_divided_kwds
from ..models import ArticleWorkflow, Message, MessageRecipients

if TYPE_CHECKING:
    from .logic import MetadataFromTeX

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
            self.fields["license"].initial = self.tex_license.name
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


class SyncRightsForm(forms.Form):
    """
    Form used to receive the green-light to synchronize the copyright between TeX and DB.

    The ``rights`` field is not editable: it just holds the string of the paper's rights indicated by the TeX
    source. The form is validated (also at GET) so that the template can disable the submit button when the TeX
    copyright does not match any "known" one.
    """

    action = forms.CharField(widget=forms.HiddenInput(), initial="sync_rights")
    rights = forms.CharField(label=_("Rights"), disabled=True, required=True)  # noqa: A003 (ruff and flake disagree)

    def __init__(self, texdata, *args, **kwargs):
        """Store the TeX data and bind the rights string to the field."""
        self.texdata = texdata
        super().__init__(*args, **kwargs)
        # Map the known copyrights.
        # Apart from the "authors", ATM the latex preamble says (roughly):
        # access_mode.code +
        # if article has collaborations: [for the CMS and ATLAS and LHCb collaboration]
        self.rights_mapping = {
            "authors": "© Authors",
        }
        self.tex_rights_raw = texdata.data.get("copyright")  # NB: "copyright" ≈ "rights"
        self.tex_rights = self.rights_mapping.get(self.tex_rights_raw, self.tex_rights_raw)
        if self.tex_rights:
            self.fields["rights"].initial = self.tex_rights
        self.db_rights = self.texdata.workflow.article.rights
        self.fields["rights"].help_text = f"TeX: {self.tex_rights_raw} vs DB: {self.db_rights}"

    def clean_rights(self):
        """Any value vould do."""
        if not self.tex_rights:
            raise ValidationError("The copyright must have some value!")
        return self.cleaned_data["rights"]

    def should_sync(self) -> bool:
        """Tell if DB and TeX are out of sync."""
        return self.tex_rights != self.db_rights

    def sync(self):
        """
        Validate and persist the rights.

        Raise:
          ValueError: if the form does not validate or if saving fails.
        """
        if not self.is_valid():
            raise ValueError(self.errors.as_text())
        article = self.texdata.workflow.article
        article.rights = self.tex_rights
        try:
            article.save()
        except Exception as e:  # noqa: BLE001 - surface any persistence failure as a ValueError
            raise ValueError(str(e)) from e


class SyncArxivForm(forms.Form):
    """
    Form used to receive the green-light to synchronize the arXiv id between TeX and DB.

    The ``arxiv`` field is not editable: it just shows the arXiv id extracted from the TeX source.
    The form is validated (also at GET) so that the template can disable the submit button when
    the new arXiv id would clash with another article (uniqueness check).
    """

    action = forms.CharField(widget=forms.HiddenInput(), initial="sync_arxiv")
    # Not required: an article may legitimately have no arXiv id (neither in TeX nor in the DB).
    arxiv = forms.CharField(label=_("arXiv id"), disabled=True, required=False)

    def __init__(self, texdata, *args, **kwargs):
        """Store the TeX data and bind the arXiv id extracted from it to the field."""
        self.texdata = texdata
        super().__init__(*args, **kwargs)
        self.tex_arxiv = texdata.data.get("arxiv_num")
        # article.arxiv_id already drops the version suffix (everything from the "v" on), so
        # normalize the TeX value the same way to compare like-with-like.
        self.tex_arxiv_normalized = (self.tex_arxiv or "").partition("v")[0]
        self.db_arxiv = self.texdata.workflow.article.arxiv_id
        # The field is disabled, so its value comes from self.initial.
        self.initial["arxiv"] = self.tex_arxiv
        self.fields["arxiv"].help_text = f"TeX: {self.tex_arxiv} vs DB: {self.db_arxiv}"

    def clean_arxiv(self):
        """
        Perform some sanity checks.

        - the "new" the arXiv id from the TeX does not clash with another article
        - we are not trying to remove an arXiv id
        """
        # If neither the DB nor the TeX set the arXiv id, this is ok
        if not self.tex_arxiv_normalized and not self.db_arxiv:
            return self.cleaned_data["arxiv"]

        if not self.tex_arxiv_normalized and self.db_arxiv:
            raise ValidationError("Unexpected: arXiv id in the DB, but nothing in the TeX. Please check!")

        if Identifier.objects.filter(article=self.texdata.workflow.article, id_type="arxiv").count() != 1:
            raise ValidationError("Unexpected number of arXiv ids. Check the identifiers from the manager!")

        article = self.texdata.workflow.article
        response_content = {
            "arxiv_id": self.tex_arxiv,
            "title": article.title,
            "abstract": article.abstract,
        }
        if not check_article_unique(
            response_content=response_content,
            journal=article.journal,
            article_id=article.pk,
        ):
            raise ValidationError(
                f"An article with arXiv id '{self.tex_arxiv}' (or the same title and abstract) already exists!"
            )
        return self.cleaned_data["arxiv"]

    def should_sync(self) -> bool:
        """Tell if DB and TeX are out of sync (ignoring the version suffix)."""
        # article.arxiv_id is None when no arXiv Identifier exists; treat that as the empty
        # string so that "no arXiv id in TeX nor DB" does not look like an out-of-sync state.
        return self.tex_arxiv_normalized != (self.db_arxiv or "")

    def sync(self):
        """
        Validate and persist the arXiv id.

        The arXiv id lives in an Identifier of type "arxiv": drop the existing one (if any) and
        create a fresh one with the TeX value.

        Raise:
          ValueError: if the form does not validate or if saving fails.
        """
        if not self.is_valid():
            raise ValueError(self.errors.as_text())
        article = self.texdata.workflow.article
        try:
            Identifier.objects.filter(article=article, id_type="arxiv").delete()
            Identifier.objects.create(article=article, id_type="arxiv", identifier=self.tex_arxiv)
        except Exception as e:  # noqa: BLE001 - surface any persistence failure as a ValueError
            raise ValueError(str(e)) from e


class SyncCasDasForm(forms.Form):
    """
    Form used to synchronize the Code/Data Availability Statements between TeX and DB.

    "cas" is the Code Availability Statement, "das" the Data Availability Statement. Both fields
    are not editable: they just show the declarations extracted from the TeX source. The form is
    validated (also at GET) so that the template can disable the submit button when a TeX value is
    not a known CAS/DAS declaration. The declarations live on the article's ``submission_data``
    (a OneToOne ArticleSubmission).
    """

    action = forms.CharField(widget=forms.HiddenInput(), initial="sync_casdas")
    cas = forms.CharField(label=_("Code Availability Statement"), disabled=True, required=False)
    cas_url = forms.CharField(label=_("CAS URL"), disabled=True, required=False)
    das = forms.CharField(label=_("Data Availability Statement"), disabled=True, required=False)
    das_url = forms.CharField(label=_("DAS URL"), disabled=True, required=False)

    def __init__(self, texdata, *args, **kwargs):
        """Store the TeX data and bind the cas/das declarations extracted from it to the fields."""
        self.texdata = texdata
        super().__init__(*args, **kwargs)
        self.tex_cas = texdata.data.get("cas", "")
        self.tex_cas_url = texdata.data.get("cas_url", "")
        self.tex_das = texdata.data.get("das", "")
        self.tex_das_url = texdata.data.get("das_url", "")
        submission_data = self._get_submission_data()
        self.db_cas = submission_data.cas if submission_data else None
        self.db_cas_url = submission_data.cas_url if submission_data else None
        self.db_das = submission_data.das if submission_data else None
        self.db_das_url = submission_data.das_url if submission_data else None
        # The fields are disabled, so their values come from self.initial.
        self.initial["cas"] = self.tex_cas
        self.initial["cas_url"] = self.tex_cas_url
        self.initial["das"] = self.tex_das
        self.initial["das_url"] = self.tex_das_url
        cas_labels = dict(ArticleSubmission.CasDeclaration.choices)
        das_labels = dict(ArticleSubmission.DasDeclaration.choices)
        self.fields["cas"].help_text = (
            f"TeX: {cas_labels.get(self.tex_cas, self.tex_cas)} " f"vs DB: {cas_labels.get(self.db_cas, self.db_cas)}"
        )
        self.fields["cas_url"].help_text = f"TeX: {self.tex_cas_url} vs DB: {self.db_cas_url}"
        self.fields["das"].help_text = (
            f"TeX: {das_labels.get(self.tex_das, self.tex_das)} " f"vs DB: {das_labels.get(self.db_das, self.db_das)}"
        )
        self.fields["das_url"].help_text = f"TeX: {self.tex_das_url} vs DB: {self.db_das_url}"
        # Flag the case where syncing would drop an existing DB URL (the TeX has none).
        self.cas_url_will_be_removed = bool(self.db_cas_url) and not self.tex_cas_url
        self.das_url_will_be_removed = bool(self.db_das_url) and not self.tex_das_url

    def _get_submission_data(self) -> ArticleSubmission | None:
        """Return the article's ArticleSubmission, or None if it does not exist yet."""
        try:
            return self.texdata.workflow.article.submission_data
        except ArticleSubmission.DoesNotExist:
            return None

    def clean_cas(self):
        """Ensure the TeX CAS is a known Code Availability Statement declaration."""
        # CAS is not mandatory: if neither TeX nor DB has it set, there is nothing to validate.
        if not self.tex_cas and not self.db_cas:
            return self.cleaned_data["cas"]
        if self.tex_cas not in dict(ArticleSubmission.CasDeclaration.choices):
            raise ValidationError(f"Unknown CAS declaration '{self.tex_cas}'!")
        return self.cleaned_data["cas"]

    def clean_das(self):
        """Ensure the TeX DAS is a known Data Availability Statement declaration."""
        # DAS is not mandatory: if neither TeX nor DB has it set, there is nothing to validate.
        if not self.tex_das and not self.db_das:
            return self.cleaned_data["das"]
        if self.tex_das not in dict(ArticleSubmission.DasDeclaration.choices):
            raise ValidationError(f"Unknown DAS declaration '{self.tex_das}'!")
        return self.cleaned_data["das"]

    def should_sync(self) -> bool:
        """Tell if DB and TeX are out of sync (treating "unset in both" as in-sync)."""
        return (
            (self.tex_cas or "") != (self.db_cas or "")
            or (self.tex_das or "") != (self.db_das or "")
            # cas_url/das_url are always taken from the TeX (syncing clears the DB value when the
            # TeX has none), so any difference — including a removal — counts as out of sync.
            or (self.tex_cas_url or "") != (self.db_cas_url or "")
            or (self.tex_das_url or "") != (self.db_das_url or "")
        )

    def sync(self):
        """
        Validate and persist the cas/das declarations.

        Raise:
          ValueError: if the form does not validate or if saving fails.
        """
        if not self.is_valid():
            raise ValueError(self.errors.as_text())
        submission_data = self._get_submission_data()
        if submission_data is None:
            raise ValueError("This article has no submission data!")
        submission_data.cas = self.tex_cas
        submission_data.das = self.tex_das
        # cas_url/das_url are always taken from the TeX, so syncing clears the DB value when the
        # TeX has none.
        submission_data.cas_url = self.tex_cas_url
        submission_data.das_url = self.tex_das_url
        try:
            submission_data.save()
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


@dataclass
class TexAuthor:
    """An author as extracted from the TeX source."""

    order: int
    first_name: str
    last_name: str
    suffix: str
    email: str
    orcid: str
    biography: str
    socials_handle: str

    @classmethod
    def from_payload(cls, order: int, data: dict) -> "TexAuthor":
        """Build an instance from one item of jcomassistant's ``authors_data`` payload."""
        return cls(
            order=order,
            first_name=data.get("first_name") or "",
            last_name=data.get("surname") or "",
            # jcomassistant recognizes the known suffixes (Jr., III, ...) of the author's fullname
            suffix=data.get("suffix") or "",
            email=data.get("email") or "",
            orcid=data.get("orcid") or "",
            # bibliography and socials are not used across all journals
            biography=data.get("biography") or "",
            socials_handle=data.get("socials_handle") or "",
        )

    @property
    def full_name(self) -> str:
        """Return the name of this author (used to label the choices of a form)."""
        return " ".join(filter(None, [self.first_name, self.last_name, self.suffix]))

    def orcid_differs_from(self, db_author: "DBAuthor") -> bool:
        """
        Tell if this author and the given account declare different orcids.

        A missing orcid (on either side) is not a difference: there is nothing to contradict.
        """
        tex_orcid = self.orcid.strip()
        db_orcid = (db_author.orcid or "").strip()
        return bool(tex_orcid) and bool(db_orcid) and tex_orcid != db_orcid


@dataclass
class DBAuthor:
    """The DB counterpart of a TeX author: an Account, either mapped onto it or just "similar"."""

    pk: int
    first_name: str
    last_name: str
    email: str
    orcid: str
    biography: str
    socials_handle: str
    country: str
    institution: str

    @classmethod
    def from_account(cls, account: Account) -> "DBAuthor":
        """Build an instance from an Account."""
        return cls(
            pk=account.pk,
            first_name=account.first_name or "",
            last_name=account.last_name or "",
            email=account.email or "",
            orcid=account.orcid or "",
            biography=account.biography or "",
            # Janeway's Account has no "socials handle" field: the twitter one is used instead.
            socials_handle=account.twitter or "",
            country=account.country.name if account.country else "",
            institution=account.institution or "",
        )


@dataclass
class AuthorRecord:
    """
    One author record (FrozenAuthor) of the paper.

    A record does not necessarily have an account, so it is identified by its own pk: this is what
    the forms of the first section of the authors block act upon.
    """

    pk: int
    account_id: int | None
    first_name: str
    last_name: str
    email: str
    orcid: str
    biography: str
    socials_handle: str
    country: str
    institution: str

    @classmethod
    def from_frozen_author(cls, frozen_author: submission_models.FrozenAuthor) -> "AuthorRecord":
        """Build an instance from an author record of the paper."""
        account = frozen_author.author
        return cls(
            pk=frozen_author.pk,
            account_id=frozen_author.author_id,
            first_name=frozen_author.first_name or "",
            last_name=frozen_author.last_name or "",
            # These three fall back onto the account, when the record has one.
            email=frozen_author.email or "",
            orcid=frozen_author.orcid or "",
            biography=frozen_author.biography or "",
            # Janeway's Account has no "socials handle" field: the twitter one is used instead.
            socials_handle=account.twitter if account else "",
            country=str(frozen_author.country) if frozen_author.country else "",
            institution=frozen_author.institution or "",
        )


@dataclass
class AuthorMapping:
    """The result of the mapping of one TeX author onto the DB."""

    tex_author: TexAuthor
    account_id: int | None = None
    mapped_account: DBAuthor | None = None
    similar_accounts: list[DBAuthor] = field(default_factory=list)
    must_be_created: bool = False
    warning: str | None = None

    @property
    def candidates(self) -> list[DBAuthor]:
        """Return the DB accounts that this TeX author might correspond to."""
        if self.mapped_account:
            return [self.mapped_account]
        return list(self.similar_accounts)

    @property
    def is_sure_match(self) -> bool:
        """
        Tell if this TeX author has been surely mapped onto a DB account.

        Only the mapping by email, by orcid and by "correspondence" are considered sure: the
        heuristics on the name only propose candidates (see AuthorsMapper).
        """
        return self.mapped_account is not None


@dataclass
class AuthorsMapper:
    """
    Map the authors declared by the TeX source onto the authors of the article.

    This is the read-model shared by the forms of the authors block: it knows the authors of the
    TeX source, the authors of the DB, how they map onto each other and what does not add up.
    """

    texdata: "MetadataFromTeX"

    @property
    def workflow(self) -> ArticleWorkflow:
        """Return the workflow of the paper whose authors are being synchronized."""
        return self.texdata.workflow

    @property
    def article(self) -> Article:
        """Return the paper whose authors are being synchronized."""
        return self.workflow.article

    @cached_property
    def authors_tex(self) -> list[TexAuthor]:
        """Return the authors declared by the TeX source."""
        return [
            TexAuthor.from_payload(order, data)
            for order, data in enumerate(self.texdata.data.get("authors_data") or [])
        ]

    @cached_property
    def authors_db(self) -> QuerySet:
        """
        Get the author records (FrozenAuthor) of the paper, in order.

        Work on the author records and not on article.author_accounts: a record does not necessarily
        have an account, and such a record would be invisible here (and the sync would silently drop
        it). The records are ordered by FrozenAuthor's own Meta.ordering.
        """
        return submission_models.FrozenAuthor.objects.filter(article=self.article).select_related("author")

    @cached_property
    def authors_map(self) -> list[AuthorMapping]:
        """Use TeX data to retrieve Accounts from DB."""
        return [self._find_corresponding_account(tex_author) for tex_author in self.authors_tex]

    @cached_property
    def sure_match_ids(self) -> set[int]:
        """Return the ids of the accounts that have been surely mapped onto a TeX author."""
        return {mapping.account_id for mapping in self.authors_map if mapping.is_sure_match}

    @cached_property
    def tex_emails(self) -> set[str]:
        """Return the email addresses declared by the TeX source."""
        return {tex_author.email.lower() for tex_author in self.authors_tex if tex_author.email}

    @cached_property
    def orphan_authors(self) -> list[AuthorRecord]:
        """
        Return the author records of the paper that the TeX source does not (surely) know.

        These are the records that the operator must take care of before the author records can be
        rebuilt from the TeX source, or the rebuild would silently drop them: either they are not
        authors of this paper (and their record can be deleted), or they are known to the TeX with
        a different email (and a "correspondence" can say so).
        """
        return [
            AuthorRecord.from_frozen_author(record)
            for record in self.authors_db
            if not self._record_is_known_to_tex(record)
        ]

    def _record_is_known_to_tex(self, record: submission_models.FrozenAuthor) -> bool:
        """Tell if one author record of the paper corresponds to one of the TeX authors."""
        if record.author_id:
            return record.author_id in self.sure_match_ids
        # A record with no account can only be recognized by its email address.
        return bool(record.email) and record.email.lower() in self.tex_emails

    @cached_property
    def unmatched_tex_authors(self) -> list[TexAuthor]:
        """Return the TeX authors that are not surely mapped onto an account of the paper."""
        return [mapping.tex_author for mapping in self.authors_map if not mapping.is_sure_match]

    @cached_property
    def errors_db(self) -> list[str]:
        """
        Return the problems of the DB authors list (displayed by the first section).

        ATM only check that the authors of the paper are the same (and in the same order) as the
        authors of the TeX source.
        """
        db_last_names = [record.last_name.strip() for record in self.authors_db]
        tex_last_names = [tex_author.last_name.strip() for tex_author in self.authors_tex]
        if db_last_names == tex_last_names:
            return []
        return [
            _("TeX and DB authors list differ (DB: %(db)s vs TeX: %(tex)s)")
            % {"db": "; ".join(db_last_names), "tex": "; ".join(tex_last_names)},
        ]

    @cached_property
    def errors_tex(self) -> list[str]:
        """
        Return the problems of the TeX authors list (displayed by the second section).

        ATM only check that owner and corresondence author are in the list of mapped authors.
        """
        article = self.article
        errors = []
        mapped_authors_ids = [mapping.account_id for mapping in self.authors_map if mapping.account_id]
        if article.owner.id not in mapped_authors_ids:
            errors.append(_("No owner in the new authors list!"))
        if article.correspondence_author.id not in mapped_authors_ids:
            errors.append(_("No correspondence author in the new authors list!"))
        if any(mapping.similar_accounts for mapping in self.authors_map):
            errors.append(_("Similar accounts exist!"))
        return errors

    def create_correspondence(self, account: Account, tex_author: TexAuthor) -> Correspondence | None:
        """
        Map an account onto the email of a TeX author, i.e. create a "correspondence".

        This is what makes the mapping of that TeX author sure (see _find_corresponding_account).
        Return None when the TeX author has no email (there would be nothing to map onto).
        """
        if not tex_author.email:
            return None
        correspondence, __ = Correspondence.objects.get_or_create(
            account=account,
            # There is no wjapp userCod here: use the order of the author in the TeX source.
            user_cod=tex_author.order,
            source="tex",
            email=tex_author.email,
            defaults={"notes": self.workflow.preprint_id},
        )
        return correspondence

    def _find_corresponding_account(self, tex_author: TexAuthor) -> AuthorMapping:
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
        - give up and flag the author as "to be created"

        Only the first three ways give a "sure" match: the others just propose some candidates.
        """
        article = self.article
        try:
            account = Account.objects.get(email=tex_author.email)
        except Account.DoesNotExist:
            pass
        else:
            return AuthorMapping(
                tex_author=tex_author,
                account_id=account.id,
                mapped_account=DBAuthor.from_account(account),
            )

        if tex_author.orcid:
            try:
                account = Account.objects.get(orcid=tex_author.orcid)
            except Account.DoesNotExist:
                pass
            else:
                return AuthorMapping(
                    tex_author=tex_author,
                    account_id=account.id,
                    mapped_account=DBAuthor.from_account(account),
                )

        try:
            wjapp_mapping = Correspondence.objects.get(email=tex_author.email)
        except Correspondence.DoesNotExist:
            pass
        else:
            return AuthorMapping(
                tex_author=tex_author,
                account_id=wjapp_mapping.account.id,
                mapped_account=DBAuthor.from_account(wjapp_mapping.account),
            )

        similaraccounts_warning = """Unsure match!
If this DB account matches the TeX one,
either set the orcid (click on the name and come back),
or "use" it: NB a "correspondence" will be created!.
"""
        # Each of the following heuristics is more generic than the previous one, so the first one
        # that finds more than one account can stop the search: any other heuristic would contain
        # these accounts also.
        similar_accounts_filters = (
            (article.author_accounts, {"first_name": tex_author.first_name, "last_name": tex_author.last_name}),
            (article.author_accounts, {"last_name": tex_author.last_name}),
            (Account.objects, {"first_name": tex_author.first_name, "last_name": tex_author.last_name}),
            (
                Account.objects,
                {
                    "first_name__startswith": tex_author.first_name[0] if tex_author.first_name else "",
                    "last_name__endswith": tex_author.last_name.split(" ")[-1],
                },
            ),
        )
        for manager, filters in similar_accounts_filters:
            try:
                manager.get(**filters)
            except Account.DoesNotExist:
                continue
            except Account.MultipleObjectsReturned:
                return AuthorMapping(
                    tex_author=tex_author,
                    warning=similaraccounts_warning,
                    similar_accounts=[DBAuthor.from_account(account) for account in manager.filter(**filters)],
                )

        return AuthorMapping(tex_author=tex_author, must_be_created=True)


class BaseOrphanAuthorForm(forms.Form):
    """
    Base class of the forms that act on one "orphan" author record of the paper.

    An orphan record is an author record of the paper that the TeX source does not (surely) know (see
    ``AuthorsMapper.orphan_authors``); the record that this form acts upon is carried by the hidden
    ``author_record_id``. It is the record (and not its account) that identifies the author here,
    because a record does not necessarily have an account.
    """

    author_record_id = forms.IntegerField(widget=forms.HiddenInput())

    def __init__(self, mapper: AuthorsMapper, *args, author_record: AuthorRecord | None = None, **kwargs):
        """Store the mapper and (when rendering) the orphan record that this form acts upon."""
        self.mapper = mapper
        self.author_record = author_record
        super().__init__(*args, **kwargs)
        if author_record:
            self.initial["author_record_id"] = author_record.pk

    def clean_author_record_id(self):
        """Ensure that the record is one of the orphan records of the paper."""
        author_record_id = self.cleaned_data["author_record_id"]
        if author_record_id not in [orphan.pk for orphan in self.mapper.orphan_authors]:
            raise ValidationError(
                _("Author record %(pk)s is not an author to take care of!") % {"pk": author_record_id},
            )
        return author_record_id

    def get_frozen_author(self) -> submission_models.FrozenAuthor:
        """Return the author record that this form acts upon."""
        return submission_models.FrozenAuthor.objects.get(
            pk=self.cleaned_data["author_record_id"],
            article=self.mapper.article,
        )


class DeleteAuthorRecordForm(BaseOrphanAuthorForm):
    """Form used to remove one author record (FrozenAuthor) from the paper."""

    action = forms.CharField(widget=forms.HiddenInput(), initial="delete_author_record")

    def sync(self) -> submission_models.FrozenAuthor:
        """
        Validate and delete the author record.

        Return the (deleted) record: it is not in the DB anymore, but it still knows the name of the
        author that is not an author of the paper anymore.

        Raise:
          ValidationError: if the form does not validate.
        """
        if not self.is_valid():
            raise ValidationError(self.errors.as_text())
        frozen_author = self.get_frozen_author()
        frozen_author.delete()
        return frozen_author


class CreateCorrespondenceForm(BaseOrphanAuthorForm):
    """
    Form used to map one author of the paper onto the email of one TeX author.

    The choices are the emails of the TeX authors that are not surely mapped onto an author of the
    paper: creating the "correspondence" makes the mapping sure (see
    ``AuthorsMapper._find_corresponding_account``).
    """

    action = forms.CharField(widget=forms.HiddenInput(), initial="create_correspondence")
    email = forms.ChoiceField(label=_("TeX email"), widget=forms.RadioSelect(), required=True)

    def __init__(self, mapper: AuthorsMapper, *args, **kwargs):
        """Offer the emails of the TeX authors that are not surely mapped onto the DB."""
        super().__init__(mapper, *args, **kwargs)
        self.tex_authors = {tex_author.email: tex_author for tex_author in mapper.unmatched_tex_authors}
        self.fields["email"].choices = [
            (tex_author.email, f"{tex_author.full_name} <{tex_author.email}>")
            for tex_author in mapper.unmatched_tex_authors
        ]

    @property
    def is_usable(self) -> bool:
        """
        Tell if this form can do anything for the record that it is rendered for.

        A "correspondence" maps an account onto an email address, so it needs both an account on the
        DB side and an unmatched email on the TeX side.
        """
        return bool(self.author_record and self.author_record.account_id and self.fields["email"].choices)

    def sync(self) -> Correspondence:
        """
        Validate and create the correspondence.

        Raise:
          ValidationError: if the form does not validate or if the correspondence cannot be created.
        """
        if not self.is_valid():
            raise ValidationError(self.errors.as_text())
        email = self.cleaned_data["email"]
        account = self.get_frozen_author().author
        if not account:
            raise ValidationError(_("This author record has no account to map onto a TeX email!"))
        try:
            correspondence = self.mapper.create_correspondence(
                account=account,
                tex_author=self.tex_authors[email],
            )
        except Exception as e:  # noqa: BLE001 - surface any persistence failure as a ValidationError
            raise ValidationError(str(e)) from e
        return correspondence


@dataclass
class OrphanAuthorRow:
    """One author record of the paper that the TeX source does not (surely) know, and its two forms."""

    author_record: AuthorRecord
    delete_form: "DeleteAuthorRecordForm"
    correspondence_form: "CreateCorrespondenceForm"


@dataclass
class AuthorChoiceRow:
    """One line of the choices offered for one TeX author: an account of the DB, or a new record."""

    radio: forms.BoundField
    db_author: DBAuthor | None = None
    # True when the account of this line declares an orcid different from the TeX one: such an
    # account cannot be the author, so this line cannot be selected (see SyncAuthorsForm).
    orcid_conflict: bool = False


@dataclass
class AuthorSection:
    """One TeX author, the accounts that it might be linked to and the "new record" line."""

    mapping: AuthorMapping
    rows: list[AuthorChoiceRow]
    # True when this TeX author is not an author of the paper yet: adding them to the paper notifies
    # them (see SyncAuthorsForm._log_new_coauthor_created).
    is_new_coauthor: bool = False

    @property
    def tex_author(self) -> TexAuthor:
        """Return the TeX author of this section (shortcut for the template)."""
        return self.mapping.tex_author


@dataclass(frozen=True)
class PreviousAuthors:
    """
    The authors of a paper, as they were before their author records are rebuilt.

    Rebuilding the author records (see SyncAuthorsForm.sync) drops and recreates all of them, so the
    records themselves cannot tell who is a *new* co-author of the paper: this is the snapshot taken
    before the rebuild. A person is recognized by their account and by their email address, because an
    author record does not necessarily have an account.
    """

    account_ids: frozenset[int]
    emails: frozenset[str]

    @classmethod
    def snapshot(cls, article: Article) -> "PreviousAuthors":
        """Take note of the authors of the paper, before their records are dropped."""
        account_ids = set()
        emails = set()
        for record in submission_models.FrozenAuthor.objects.filter(article=article).select_related("author"):
            if record.author_id:
                account_ids.add(record.author_id)
            if record.email:
                emails.add(record.email.lower())
        return cls(account_ids=frozenset(account_ids), emails=frozenset(emails))

    def includes(self, frozen_author: submission_models.FrozenAuthor) -> bool:
        """Tell if the given author record refers to a person that was already an author of the paper."""
        return self.includes_person(frozen_author.author_id, frozen_author.email)

    def includes_person(self, account_id: int | None, email: str | None) -> bool:
        """Tell if the given account / email address refers to a person that was already an author."""
        if account_id and account_id in self.account_ids:
            return True
        email = (email or "").lower()
        return bool(email) and email in self.emails


class RadioSelectWithDisabled(forms.RadioSelect):
    """A radio group where some of the options cannot be selected."""

    def __init__(self, *args, disabled_values: frozenset[str] = frozenset(), **kwargs):
        """Store the values of the options that cannot be selected."""
        self.disabled_values = disabled_values
        super().__init__(*args, **kwargs)

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        """Render as disabled the options whose value cannot be selected."""
        option = super().create_option(name, value, label, selected, index, subindex=subindex, attrs=attrs)
        if str(value) in self.disabled_values:
            option["attrs"]["disabled"] = True
        return option


class SyncAuthorsForm(forms.Form):
    """
    Form used to rebuild the author records of the paper from the TeX source.

    The TeX source is the source of truth: syncing drops every author record (FrozenAuthor) of the
    paper and creates a new one per TeX author, in the order of the TeX source and with the data of
    the TeX source (there is nothing to edit here).

    The form has one radio group per TeX author, that tells which account the new author record
    must be linked to: the (single) account that the TeX author has been surely mapped onto, one of
    the "similar" accounts found in the DB, or none at all (the "new" choice).

    Syncing is refused as long as some author of the paper is not surely mapped onto a TeX author
    (see ``AuthorsMapper.orphan_authors``): such an author would silently disappear from the paper.
    """

    NEW_RECORD = "new"

    action = forms.CharField(widget=forms.HiddenInput(), initial="sync_authors")

    def __init__(self, mapper: AuthorsMapper, *args, **kwargs):
        """Store the mapper and add one radio group per TeX author."""
        self.mapper = mapper
        super().__init__(*args, **kwargs)
        for mapping in mapper.authors_map:
            self.fields[self.field_name(mapping.tex_author)] = self._build_choice_field(mapping)

    @staticmethod
    def field_name(tex_author: TexAuthor) -> str:
        """Return the name of the field that holds the choice made for the given TeX author."""
        return f"author_{tex_author.order}"

    def _build_choice_field(self, mapping: AuthorMapping) -> forms.ChoiceField:
        """Return the radio group of one TeX author: its candidate accounts and/or a new record."""
        choices = [(str(candidate.pk), _("use")) for candidate in mapping.candidates]
        if not mapping.is_sure_match:
            # A candidate is just a guess, so the operator must be able to refuse all of them.
            choices.append((self.NEW_RECORD, _("new")))
        return forms.ChoiceField(
            label=mapping.tex_author.full_name,
            choices=choices,
            # An account that declares another orcid cannot be this author: do not let it be chosen
            # (_check_orcid guards the update, for a request that ignores this).
            widget=RadioSelectWithDisabled(disabled_values=self._orcid_conflicts(mapping)),
            required=True,
            initial=choices[0][0] if mapping.is_sure_match else self.NEW_RECORD,
        )

    @staticmethod
    def _orcid_conflicts(mapping: AuthorMapping) -> frozenset[str]:
        """Return the choices of one TeX author whose account declares a different orcid."""
        return frozenset(
            str(candidate.pk) for candidate in mapping.candidates if mapping.tex_author.orcid_differs_from(candidate)
        )

    @property
    def sections(self) -> list[AuthorSection]:
        """Group the radio buttons by TeX author: the template displays one section per author."""
        sections = []
        for mapping in self.mapper.authors_map:
            tex_author = mapping.tex_author
            radios = list(self[self.field_name(tex_author)])
            rows = [
                AuthorChoiceRow(
                    radio=radio,
                    db_author=candidate,
                    orcid_conflict=tex_author.orcid_differs_from(candidate),
                )
                for candidate, radio in zip(mapping.candidates, radios)
            ]
            if not mapping.is_sure_match:
                # The "new record" choice is always the last one (see _build_choice_field).
                rows.append(AuthorChoiceRow(radio=radios[-1]))
            sections.append(
                AuthorSection(
                    mapping=mapping,
                    rows=rows,
                    # Judge the choice that is selected: the account that the TeX author has been
                    # surely mapped onto, or (when it is just a guess) a brand new author record.
                    is_new_coauthor=not self.previous_authors.includes_person(
                        mapping.account_id if mapping.is_sure_match else None,
                        tex_author.email,
                    ),
                ),
            )
        return sections

    @cached_property
    def previous_authors(self) -> PreviousAuthors:
        """Return the authors that the paper has before its author records are rebuilt."""
        return PreviousAuthors.snapshot(self.mapper.article)

    def can_sync(self) -> bool:
        """Tell if the author records can be rebuilt from the TeX source."""
        return not self.mapper.orphan_authors

    def should_sync(self) -> bool:
        """
        Tell if the author records of the paper are out of sync with the TeX source.

        Return False when there is no author to take care of and every author record has the values
        and the position of the corresponding TeX author; True otherwise.

        NB: the socials handle is not part of the author record (it lives on the account), so it
        does not take part in the comparison.
        """
        if self.mapper.orphan_authors:
            return True
        frozen_authors = list(self.mapper.article.frozen_authors())
        if len(frozen_authors) != len(self.mapper.authors_tex):
            return True
        return not all(
            self._matches_tex_author(frozen_author, tex_author)
            for frozen_author, tex_author in zip(frozen_authors, self.mapper.authors_tex)
        )

    @staticmethod
    def _matches_tex_author(frozen_author: submission_models.FrozenAuthor, tex_author: TexAuthor) -> bool:
        """Tell if an author record has the values that the given TeX author would write on it."""
        return all(
            str(getattr(frozen_author, record_field) or "").strip() == str(getattr(tex_author, tex_field)).strip()
            for record_field, tex_field in (
                ("first_name", "first_name"),
                ("last_name", "last_name"),
                ("name_suffix", "suffix"),
                ("frozen_email", "email"),
                ("frozen_orcid", "orcid"),
                ("frozen_biography", "biography"),
            )
        )

    def clean(self):
        """Refuse to sync as long as some author of the paper is not surely mapped."""
        cleaned_data = super().clean()
        if not self.can_sync():
            raise ValidationError(
                _("Please take care of the authors of the paper that the TeX source does not know!"),
            )
        # Two TeX authors can be mapped onto the same account (e.g. by a "correspondence" on a
        # second email), but a single account cannot be the author of the same paper twice.
        chosen_accounts = [
            choice
            for mapping in self.mapper.authors_map
            if (choice := cleaned_data.get(self.field_name(mapping.tex_author))) and choice != self.NEW_RECORD
        ]
        if len(chosen_accounts) != len(set(chosen_accounts)):
            raise ValidationError(_("The same account cannot be linked to two different authors!"))
        self._check_orcid(cleaned_data)
        # The socials handle is stored as a handle (see _create_author_record) but displayed as a
        # bluesky URL (see the bluesky_url template filter), so the TeX must declare it as such.
        bad_handles = [
            tex_author.socials_handle
            for tex_author in self.mapper.authors_tex
            if tex_author.socials_handle and not tex_author.socials_handle.startswith("@")
        ]
        if bad_handles:
            raise ValidationError(
                _('The socials handle must start with "@": please fix %(handles)s in the TeX source!')
                % {"handles": "; ".join(bad_handles)},
            )
        return cleaned_data

    def _check_orcid(self, cleaned_data: dict):
        """
        Ensure that the account linked to a TeX author does not declare another orcid.

        The account of a candidate is just a guess (it has been found by name), and even a sure match
        can contradict the TeX source (a match by email or by "correspondence" says nothing about the
        orcid): a different orcid on both sides means that this is the wrong account. A sure match is
        judged even when nothing has been selected for its TeX author, because it is its only choice
        (and the choice is not selectable, see _build_choice_field): such a contradiction can only be
        resolved by fixing the TeX source or the account.

        Raise:
          ValidationError: if the orcid of a linked account differs from the TeX one.
        """
        conflicts = []
        for mapping in self.mapper.authors_map:
            if mapping.is_sure_match:
                candidate = mapping.mapped_account
            else:
                candidate = self._get_selected_candidate(mapping, cleaned_data)
            if candidate and mapping.tex_author.orcid_differs_from(candidate):
                conflicts.append(
                    _("%(name)s: the TeX orcid (%(tex)s) is not the orcid of the selected account (%(db)s)!")
                    % {
                        "name": mapping.tex_author.full_name,
                        "tex": mapping.tex_author.orcid,
                        "db": candidate.orcid,
                    },
                )
        if conflicts:
            raise ValidationError(conflicts)

    def _get_selected_candidate(self, mapping: AuthorMapping, cleaned_data: dict) -> DBAuthor | None:
        """Return the account selected for the given TeX author (None for a brand new record)."""
        choice = cleaned_data.get(self.field_name(mapping.tex_author))
        if not choice or choice == self.NEW_RECORD:
            return None
        return next((candidate for candidate in mapping.candidates if str(candidate.pk) == choice), None)

    def get_selected_account(self, mapping: AuthorMapping) -> Account | None:
        """Return the account chosen for the given TeX author, or None for a new record."""
        choice = self.cleaned_data[self.field_name(mapping.tex_author)]
        if choice == self.NEW_RECORD:
            return None
        return Account.objects.get(id=int(choice))

    def sync(self):
        """
        Validate and rebuild the author records of the paper.

        Raise:
          ValidationError: if the form does not validate or if saving fails.
        """
        if not self.is_valid():
            raise ValidationError(self.errors.as_text())
        article = self.mapper.article
        try:
            with transaction.atomic():
                # Take note of who the authors of the paper are before dropping the author records:
                # anybody else is a new co-author, and must be notified.
                previous_authors = self.previous_authors
                submission_models.FrozenAuthor.objects.filter(article=article).delete()
                for mapping in self.mapper.authors_map:
                    self._create_author_record(
                        mapping,
                        self.get_selected_account(mapping),
                        previous_authors,
                    )
        except Exception as e:  # noqa: BLE001 - surface any persistence failure as a ValidationError
            raise ValidationError(str(e)) from e

    def _create_author_record(
        self,
        mapping: AuthorMapping,
        account: Account | None,
        previous_authors: PreviousAuthors,
    ) -> submission_models.FrozenAuthor:
        """
        Create the author record of one TeX author, linked to the given account (if any).

        Notify the person when they were not already an author of the paper (every author record is
        created anew by every sync, so the notification is driven by the snapshot of the authors that
        the paper had before the rebuild, not by the records themselves).
        """
        article = self.mapper.article
        tex_author = mapping.tex_author
        frozen_author = submission_models.FrozenAuthor.objects.create(
            article=article,
            author=account,
            order=tex_author.order,
            first_name=tex_author.first_name,
            last_name=tex_author.last_name,
            name_suffix=tex_author.suffix,
            frozen_email=tex_author.email,
            frozen_orcid=tex_author.orcid,
            frozen_biography=tex_author.biography,
            display_email=account is not None and account == article.correspondence_author,
        )
        if account:
            if not mapping.is_sure_match:
                # The account has been chosen among some candidates: remember that it corresponds
                # to this TeX author, so that the next sync does not have to guess again.
                self.mapper.create_correspondence(account=account, tex_author=tex_author)
            account.add_account_role("author", article.journal)
            account.snapshot_affiliations(frozen_author)
            # Janeway's Account has no "socials handle" field: the twitter one is used instead.
            if tex_author.socials_handle and tex_author.socials_handle != account.twitter:
                account.twitter = tex_author.socials_handle
                account.save()
        if not previous_authors.includes(frozen_author):
            self._log_new_coauthor_created(frozen_author)
        return frozen_author

    def _log_new_coauthor_created(self, frozen_author: submission_models.FrozenAuthor) -> Message | None:
        """
        Notify a person that has just been linked to the paper as co-author.

        When the author record has an account, that account is the recipient of the message, as usual.
        When it has none, the notification is sent to the email address declared by the TeX source: the
        message is logged (so that the EO can see it) and the email is sent, but no account is created
        (see communication_utils.UnregisteredRecipient).

        Return None when there is nobody to notify, i.e. when the author record has neither an account
        nor an email address.
        """
        article = self.mapper.article
        account = frozen_author.author
        if not account and not frozen_author.email:
            return None
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
            context={"article": article, "newaccount": account or frozen_author},
            template_is_setting=True,
        )
        recipients = None
        unregistered_recipients = None
        if account:
            recipients = [account]
        else:
            unregistered_recipients = [
                communication_utils.UnregisteredRecipient(
                    email=frozen_author.email,
                    full_name=frozen_author.full_name(),
                    # A co-author may see the authors of the paper.
                    may_see_authors=True,
                ),
            ]
        message = communication_utils.log_operation(
            article=article,
            message_subject=message_subject,
            message_body=message_body,
            actor=None,
            recipients=recipients,
            unregistered_recipients=unregistered_recipients,
            verbosity=Message.MessageVerbosity.EMAIL,
            flag_as_read=True,
            flag_as_read_by_eo=True,
        )
        if not recipients:
            # The notification has been emailed to a recipient that has no account, so the message has
            # no recipient at all, and a message with no recipients is "generic", i.e. visible to
            # anybody who can see the messages of the paper (see get_messages_related_to_me): now that
            # the email has been sent, make it a (read) message to the EO.
            message.recipients.set([get_eo_user(article.journal)])
            MessageRecipients.objects.filter(message=message).update(read=True)
        return message


class SyncFundingsForm(forms.Form):
    """
    Form used to receive the green-light to synchronize the fundings between TeX and DB.

    The TeX fundings come from the ``fundings`` key: a (possibly empty) list of dicts with keys
    ``name``, ``fundref_id``, ``funding_id`` and ``funding_statement``. The DB fundings are the
    article's ArticleFunding entries. get_form_context_data() returns both lists so the template
    can show them side by side; sync() replaces the DB fundings with the TeX ones.
    """

    action = forms.CharField(widget=forms.HiddenInput(), initial="sync_fundings")

    # Fundref ids are stored as URIs like https://dx.doi.org/10.13039/501100021082; the funder
    # registry id (used to query Crossref) is the part after the "10.13039/" DOI prefix.
    FUNDREF_ID_RE = re.compile(r"10\.13039/(\S+)")
    CROSSREF_FUNDERS_URL = "https://api.crossref.org/funders/{funder_id}"

    FUNDING_KEYS = ("name", "fundref_id", "funding_id", "funding_statement")

    def __init__(self, texdata, *args, **kwargs):
        """Store the TeX data and collect the TeX/DB fundings."""
        self.texdata = texdata
        super().__init__(*args, **kwargs)
        self.fundings_tex = texdata.data.get("fundings") or []
        self.fundings_db = self._get_db_fundings()

    def _get_db_fundings(self) -> list[dict]:
        """Return the article's fundings as a list of dicts (comparable with the TeX ones)."""
        return [
            {key: getattr(funding, key) or "" for key in self.FUNDING_KEYS}
            for funding in self.texdata.workflow.article.funders
        ]

    @classmethod
    def _normalize(cls, fundings: list[dict]) -> list[tuple]:
        """Turn a list of funding dicts into a sorted list of tuples for order-independent comparison."""
        return sorted(tuple(funding.get(key) or "" for key in cls.FUNDING_KEYS) for funding in fundings)

    def _get_crossref_funder_name(self, fundref_id: str) -> str | None:
        """Query Crossref for the funder name of ``fundref_id``, or None if it cannot be resolved."""
        match = self.FUNDREF_ID_RE.search(fundref_id)
        if not match:
            return None
        url = self.CROSSREF_FUNDERS_URL.format(funder_id=match.group(1))
        try:
            response = requests.get(url, timeout=10)
            response.raise_for_status()
        except requests.exceptions.RequestException:
            return None
        return response.json().get("message", {}).get("name")

    def clean(self):
        """Verify each TeX funding's name against Crossref when a fundref id is present."""
        cleaned_data = super().clean()
        # The Crossref check hits the network, so skip it when there is nothing to sync.
        if not self.should_sync():
            return cleaned_data
        for funding in self.fundings_tex:
            fundref_id = funding.get("fundref_id")
            if not fundref_id:
                continue
            crossref_name = self._get_crossref_funder_name(fundref_id)
            if crossref_name is None:
                raise ValidationError(f"Could not verify fundref id '{fundref_id}' on Crossref!")
            if crossref_name != funding.get("name"):
                raise ValidationError(
                    f"Funder name mismatch for fundref id '{fundref_id}': "
                    f"TeX says '{funding.get('name')}' but Crossref says '{crossref_name}'!"
                )
        return cleaned_data

    def should_sync(self) -> bool:
        """Tell if DB and TeX fundings are out of sync."""
        return self._normalize(self.fundings_tex) != self._normalize(self.fundings_db)

    def sync(self):
        """
        Validate and persist the fundings (replacing the DB ones with the TeX ones).

        Raise:
          ValueError: if the form does not validate or if saving fails.
        """
        if not self.is_valid():
            raise ValueError(self.errors.as_text())
        article = self.texdata.workflow.article
        try:
            article.funders.delete()
            for funding in self.fundings_tex:
                submission_models.ArticleFunding.objects.create(
                    article=article,
                    **{key: funding.get(key) or "" for key in self.FUNDING_KEYS},
                )
        except Exception as e:  # noqa: BLE001 - surface any persistence failure as a ValueError
            raise ValueError(str(e)) from e

    def get_form_context_data(self) -> dict:
        """Return fundings-related context to be merged into the view's context."""
        return {
            "form_fundings": self,
            "fundings_tex": self.fundings_tex,
            "fundings_db": self.fundings_db,
        }


class SyncCollaborationsForm(forms.Form):
    """
    Form used to receive the green-light to synchronize the collaborations between TeX and DB.

    The TeX describes the collaborations with two keys: ``collaborations`` (a list of collaboration
    names) and ``collaborations_type`` (how the article relates to *all* of them). Each name is
    matched against the DB ``Collaboration`` records (ignoring case and surrounding spaces) and the
    type is mapped onto a ``CollaborationRelation``; syncing attaches the matched collaborations to
    the article (in the TeX order, all with the mapped relation) and drops the other links.

    The ``Collaboration`` records themselves are never created nor modified here: they are shared
    between articles, so a collaboration that exists only in the TeX must be created by hand (and
    the form does not validate until every TeX collaboration has a match).
    """

    action = forms.CharField(widget=forms.HiddenInput(), initial="sync_collaborations")

    NO_MATCH_ERROR = _("No matching collaboration; please manually create one and retry!")

    NAMES_KEY = "collaborations"
    TYPE_KEY = "collaborations_type"
    #: TeX collaboration types that mean "the article is written on behalf of the collaborations".
    ON_BEHALF_OF_TYPES = ("forthe", "behalf")

    @dataclass
    class CollaborationStruct:
        """A collaboration name extracted from the TeX and the DB Collaboration matching it (if any)."""

        name: str
        collaboration: Collaboration | None

    def __init__(self, texdata, *args, **kwargs):
        """Store the TeX data and match the TeX collaboration names onto the DB Collaborations."""
        self.texdata = texdata
        super().__init__(*args, **kwargs)
        self.names_tex = texdata.data.get(self.NAMES_KEY) or []
        self.type_tex = texdata.data.get(self.TYPE_KEY) or ""
        self.relation_tex = self._map_relation(self.type_tex)
        # NB: these are ArticleCollaboration (the through model), already ordered by "order".
        self.collaborations_db = self.texdata.workflow.article.collaborations.all()
        self.collaborations_tex = [
            self.CollaborationStruct(name=name, collaboration=Collaboration.objects.by_name(name).first())
            for name in self.names_tex
        ]

    @classmethod
    def _map_relation(cls, type_tex: str) -> str:
        """Map the TeX collaborations type onto the relation between the article and a collaboration."""
        if type_tex in cls.ON_BEHALF_OF_TYPES:
            return CollaborationRelation.ON_BEHALF_OF
        return CollaborationRelation.BY

    def clean(self):
        """Ensure that the TeX describes the collaborations completely and that they all exist in the DB."""
        cleaned_data = super().clean()
        errors = []
        # Both keys must be there: a missing one means that the TeX (or its parsing) is incomplete,
        # and we would not know which relation to use (or for which collaborations).
        errors.extend(
            ValidationError(f"The TeX has no '{key}'. Please check!")
            for key in (self.NAMES_KEY, self.TYPE_KEY)
            if key not in self.texdata.data
        )
        errors.extend(
            ValidationError(f"{self.NO_MATCH_ERROR} ({item.name})")
            for item in self.collaborations_tex
            if not item.collaboration
        )
        if errors:
            raise ValidationError(errors)
        return cleaned_data

    def should_sync(self) -> bool:
        """Tell if DB and TeX collaborations (or their relation with the article) are out of sync."""
        return list(self.collaborations_db.values_list("relation", "collaboration_id")) != [
            (self.relation_tex, item.collaboration.pk) for item in self.collaborations_tex if item.collaboration
        ]

    def sync(self):
        """
        Validate and persist the collaborations attached to the article.

        Only the article-to-collaboration links are touched: the Collaboration records are left alone.

        Raise:
          ValueError: if the form does not validate or if saving fails.
        """
        if not self.is_valid():
            raise ValueError(self.errors.as_text())
        article = self.texdata.workflow.article
        collaborations_tex = [item.collaboration for item in self.collaborations_tex]
        try:
            for order, collaboration in enumerate(collaborations_tex):
                ArticleCollaboration.objects.update_or_create(
                    article=article,
                    collaboration=collaboration,
                    defaults={"order": order, "relation": self.relation_tex},
                )
            ArticleCollaboration.objects.filter(article=article).exclude(
                collaboration__in=collaborations_tex,
            ).delete()
        except Exception as e:  # noqa: BLE001 - surface any persistence failure as a ValueError
            raise ValueError(str(e)) from e

    def get_form_context_data(self) -> dict:
        """Return collaborations-related context to be merged into the view's context."""
        return {
            "form_collaborations": self,
            "collaborations_db": self.collaborations_db,
            "collaborations_tex": self.collaborations_tex,
            "relation_tex": self.relation_tex,
        }
