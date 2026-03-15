"""My views. Looking for a way to "enrich" Janeway's `edit_profile`."""

from collections import namedtuple
from dataclasses import dataclass
from typing import Iterable

import pandas as pd
from core import logic
from core import models as core_models
from core.models import Account
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import user_passes_test
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.core.mail import send_mail
from django.db.models import Q
from django.db.models.query import RawQuerySet
from django.forms import modelformset_factory
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.timezone import now
from django.utils.translation import gettext as _
from django.views.generic import ListView, TemplateView, UpdateView
from django.views.generic.edit import FormMixin
from journal import decorators as journal_decorators
from journal.models import Issue, PinnedArticle
from security.decorators import has_journal
from submission import models as submission_models
from submission.models import FrozenAuthor, Keyword, Section
from utils.logger import get_logger
from utils.setting_handler import get_setting

from . import forms
from . import permissions
from . import permissions as base_permissions
from .drupal_redirect_views import (  # noqa F401
    DrupalAuthorsRedirect,
    DrupalKeywordsRedirect,
    FaviconRedirect,
    JcomFileRedirect,
    JcomIssueRedirect,
)
from .mixins import HtmxMixin, PaginatedViewMixin
from .models import JCOMProfile, StaffWorkloadParameters
from .permissions import get_hijacker
from .profile.views import ProfilePersonalEditView

logger = get_logger(__name__)


class ProfileAffiliationsEditView(ProfilePersonalEditView):
    model = Account
    template_name = "wjs/profile/personal_affiliations_edit.html"
    fields = None


# from src/core/views.py::register
def register(request):
    """
    Display a form for users to register with the journal.

    If the user is registering on a journal we give them
    the Author role.
    :param request: HttpRequest object
    :return: HttpResponse object
    """
    token, token_obj = request.GET.get("token", None), None
    if token:
        token_obj = get_object_or_404(core_models.OrcidToken, token=token)

    form = forms.JCOMRegistrationForm(journal=request.journal)

    if request.POST:
        form = forms.JCOMRegistrationForm(request.POST, journal=request.journal)

        password_policy_check = logic.password_policy_check(request)

        if password_policy_check:
            for policy_fail in password_policy_check:
                form.add_error("password_1", policy_fail)

        if form.is_valid():
            if token_obj:
                new_user = form.save(commit=False)
                new_user.orcid = token_obj.orcid
                new_user.save()
                token_obj.delete()
            else:
                new_user = form.save()

            if request.journal:
                new_user.add_account_role("author", request.journal)
            logic.send_confirmation_link(request, new_user)

            messages.add_message(
                request,
                messages.SUCCESS,
                "Your account has been created, please follow the"
                "instructions in the email that has been sent to you.",
            )
            return redirect(reverse("registration_success"))

    template = "admin/core/accounts/register.html"
    context = {
        "form": form,
    }

    return render(request, template, context)


def registration_success(request):
    from_email = get_setting("general", "from_address", request.journal).processed_value
    context = {
        "no_reply_email": from_email,
    }
    return render(request, "admin/core/accounts/registration_success.html", context)


def confirm_gdpr_acceptance(request, token):
    """Explicitly confirm GDPR acceptance for invited users.

    The token encodes base user information (name, surname and email)
    """
    template = "admin/core/accounts/gdpr_acceptance.html"

    # verify the account existence
    try:
        account = JCOMProfile.objects.get(invitation_token=token)
    except JCOMProfile.DoesNotExist:
        context = {"error": True}
        return render(request, template, context, status=404)

    context = {
        "first_name": account.first_name,
        "last_name": account.last_name,
        "form": forms.GDPRAcceptanceForm(),
    }
    if request.POST:
        form = forms.GDPRAcceptanceForm(request.POST)
        if form.is_valid():
            template = "admin/core/accounts/thankyou.html"
            # if the form is valid and the existing account does not have the GDPR policy accepted, it is updated
            if not account.gdpr_checkbox:
                account.is_active = True
                account.gdpr_checkbox = True
                account.gdpr_acceptance = now()
                account.invitation_token = ""
                account.save()
                context["activated"] = True
                # Generate a temporary token to set a brand-new password
                core_models.PasswordResetToken.objects.filter(account=account).update(expired=True)
                reset_token = core_models.PasswordResetToken.objects.create(account=account)
                reset_psw_url = request.build_absolute_uri(
                    reverse(
                        "core_reset_password",
                        kwargs={"token": reset_token.token},
                    ),
                )
                # Send email.
                # FIXME: Email setting should be handled using the janeway settings framework.
                # See https://gitlab.sissamedialab.it/wjs/wjs-profile-project/-/issues/4
                send_mail(
                    settings.RESET_PASSWORD_SUBJECT,
                    settings.RESET_PASSWORD_BODY.format(account.first_name, account.last_name, reset_psw_url),
                    settings.DEFAULT_FROM_EMAIL,
                    [account.email],
                )
        else:
            context["form"] = form

    return render(request, template, context)


class StaffWorkloadParametersUpdate(UserPassesTestMixin, UpdateView):
    """Change editor's own submission parameters."""

    model = StaffWorkloadParameters
    form_class = forms.UpdateAssignmentParametersForm
    template_name = "submission/update_editor_parameters.html"
    raise_exception = True

    def test_func(self):
        user, journal = self.request.user, self.request.journal
        return (
            # Not adding user.is_staff, because assignment parameters have meaning only for EO or editor
            user.check_role(journal, "section-editor", staff_override=False)
            or permissions.has_eo_role(user)
        )

    def get_object(self, queryset=None):
        editor, journal = self.request.user, self.request.journal
        parameters, _ = StaffWorkloadParameters.objects.get_or_create(user=editor, journal=journal)
        return parameters

    def get_success_url(self):
        messages.add_message(
            self.request,
            messages.SUCCESS,
            "Parameters updated successfully",
        )
        return reverse("assignment_parameters")


class DirectorStaffWorkloadParametersUpdate(UserPassesTestMixin, UpdateView):
    """Change editors parameters as journal director.

    Use formsets to update EditorKeyword instances weights.

    """

    model = StaffWorkloadParameters
    form_class = forms.DirectorStaffWorkloadParametersForm
    template_name = "submission/director_update_editor_parameters.html"
    raise_exception = True

    def test_func(self):
        """Give access to EO and directors."""
        user, journal = self.request.user, self.request.journal
        return user.is_staff or base_permissions.has_eo_or_director_role(journal=journal, user=user)

    def get_object(self, queryset=None):
        editor_pk, journal = self.kwargs.get("editor_pk"), self.request.journal
        editor = JCOMProfile.objects.get(pk=editor_pk)
        # Assignment parameters have meaning only for editor and EO
        if not (
            base_permissions.has_eo_role(editor)
            or editor.check_role(journal, "editor", staff_override=False)
            or editor.check_role(journal, "section-editor", staff_override=False)
        ):
            raise Http404()
        parameters, _ = StaffWorkloadParameters.objects.get_or_create(user=editor, journal=journal)
        return parameters

    def get_context_data(self, **kwargs):  # noqa
        context = super().get_context_data()
        if self.request.POST:
            formset = forms.EditorKeywordFormset(data=self.request.POST, instance=self.object)
            formset.is_valid()
        else:
            formset = forms.EditorKeywordFormset(instance=self.object)
        context["formset"] = formset
        return context

    def form_valid(self, form):  # noqa
        context = self.get_context_data()
        formset = context.get("formset")
        if formset.is_valid():
            formset.save()
        else:
            return self.render_to_response(self.get_context_data())
        return super().form_valid(form)

    def get_success_url(self):  # noqa
        messages.add_message(
            self.request,
            messages.SUCCESS,
            "Parameters updated successfully",
        )
        return reverse("assignment_parameters", args=(self.kwargs.get("editor_pk"),))


ODSLine = namedtuple("ODSLine", ["first_name", "middle_name", "last_name", "email"])


@dataclass
class PartitionLine:
    """A line representing a collection partition.

    Or the section of a conference.
    """

    index: int
    name: str
    # something to ease discriminating between PartitionLinse and
    # ContributionLines in templates
    is_just_a_name = True


@dataclass
class ErrorLine:
    """An error in the input."""

    index: int
    first_name: str
    middle_name: str
    last_name: str
    email: str
    institution: str
    title: str
    error: str


@dataclass
class SuggestionLine:
    """A merge-with-db / merge+edit suggestion."""

    first_name: str
    middle_name: str
    last_name: str
    email: str
    institution: str
    pk: int
    is_best_suggestion: bool = False

    def __init__(self, core_account, line: "ContributionLine"):
        """Build a SuggestionLine from an item of a queryset."""
        self.first_name = core_account.first_name
        self.middle_name = core_account.middle_name or ""
        self.last_name = core_account.last_name
        self.email = core_account.email
        self.institution = core_account.institution or ""
        self.pk = core_account.id
        # We compare emails case-insensitively
        if line.email.upper() == core_account.email.upper():
            self.is_best_suggestion = True
            line.disable_new = True


@dataclass
class ContributionLine:
    """A line representing a contribution.

    Here we also keep "suggestions" of similar authors from the database.
    """

    first_name: str
    middle_name: str
    last_name: str
    email: str
    institution: str
    title: str
    suggestions: Iterable[SuggestionLine]

    def __init__(self, line: dict):
        """Build a ContributionLine."""
        self.first_name = line["first_name"]
        self.middle_name = line["middle_name"]
        self.last_name = line["last_name"]
        self.email = line["email"]
        self.title = line["title"]
        self.index = line["index"]
        self.suggestions = []
        self.disable_new = False

    def __eq__(self, other):
        """Two lines are equal if the name and title are the same."""
        # This "equality" is useful when testing for repeated lines
        # (e.g. spurious copy-paste), but there is also a different
        # scenario: when two lines with the same email have different
        # first/middle/last name or institution. This is taken care of
        # elsewhere.
        return (
            self.first_name == other.first_name
            and self.middle_name == other.middle_name
            and self.last_name == other.last_name
            and self.title == other.title
        )

    def __hash__(self):
        """Let's say these suffice..."""
        return hash(f"{self.first_name}{self.middle_name}{self.last_name}{self.title}")

    def author_eq(self, other):
        """Two authors are "equal" if first/middle/last name or institution match.

        Here I don't check the email because it is used as a
        dictionary key to keep track of who we already saw. If we see
        the same email more than once, we expect that the authors of
        the two lines are equal.

        """
        return (
            self.first_name == other.first_name
            and self.middle_name == other.middle_name
            and self.last_name == other.last_name
            # and self.email == other.email  # just add also the email, it does not hurt
            and self.institution == other.institution
        )

    def to_error_line(self, error_message):
        """Use this line to build an ErrorLine with the given error message and return it."""
        return ErrorLine(
            index=self.index,
            first_name=self.first_name,
            middle_name=self.middle_name,
            last_name=self.last_name,
            email=self.email,
            institution=self.institution,
            title=self.title,
            error=error_message,
        )


class IMUStep1(TemplateView):
    """Insert Many Users - first step.

    Manage the data file upload form.
    """

    form_class = forms.IMUForm

    def get(self, *args, **kwargs):
        """Show a form to start the IMU process - upload the data file."""
        form = self.form_class(special_issue_id=kwargs["pk"])
        return render(
            self.request,
            template_name=self.template_name,
            context={"form": form},
        )

    def post(self, *args, **kwargs):
        """Receive the data file, process it and redirect along to the next step."""
        form = self.form_class(
            special_issue_id=kwargs["pk"],
            data=self.request.POST,
            files=self.request.FILES,
        )
        if not form.is_valid():
            return render(
                self.request,
                template_name=self.template_name,
                context={"form": form},
            )
        data_file = form.files["data_file"]
        context = {
            "lines": self.process_data_file(data_file),
            "special_issue_id": kwargs["pk"],
            "create_articles_on_import": form.data.get("create_articles_on_import", ""),
            "type_of_new_articles": form.data.get("type_of_new_articles", ""),
        }
        return render(
            self.request,
            template_name="admin/core/si_imu_check.html",
            context=context,
        )

    def process_data_file(self, data_file) -> Iterable[ContributionLine]:
        """Prepare data file to be presented in the input/merge form."""
        result_lines = []

        columns_names = ("first_name", "middle_name", "last_name", "email", "title")
        sheet_index = 0
        df = pd.read_excel(
            data_file.read(),
            sheet_name=sheet_index,
            header=None,
            names=columns_names,
            dtype="string",
            na_filter=False,
            engine="odf",
        )
        # Check for extra copy paste: two lines with same author and same title.
        seen_titles = {}
        # Check for uncleare data: two lines with same email, but different author metadata.
        seen_authors = {}
        for row in df.itertuples(index=True):
            line = self.examine_row(row)
            if not isinstance(line, ContributionLine):
                result_lines.append(line)
                continue

            if line in seen_titles:
                line = line.to_error_line(
                    f"Line {line.index} is the same as {seen_titles[line]}",
                )
            elif line.email in seen_authors and not line.author_eq(seen_authors[line.email]):
                line = line.to_error_line(
                    f"Line {line.index} has same email but different data than {seen_authors[line.email].index}",
                )
            else:
                seen_titles[line] = line.index
                seen_authors[line.email] = line
            result_lines.append(line)
        return result_lines

    def examine_row(self, row: namedtuple) -> ContributionLine:
        """Parse a odt row (pandas namedtuple) into a Line.

        Line can be a PartitionLine or a ContributionLine with its suggestions.
        """
        # Allow for dirty data: if I'm missing lastname and email,
        # I'll consider this a PartitionLine and just use the
        # firstname column as the partition name.
        if not row.last_name and not row.email:
            return PartitionLine(index=row.Index, name=row.first_name)

        # But filter untreatable errors: if the title is missing and
        # the flag `create_articles_on_import` is True, treat the line
        # as an error
        if self.request.POST["create_articles_on_import"] and not row.title:
            return ErrorLine(*[*row], error="Missing title!")

        # Validate the rest
        validation_form = forms.IMUHelperForm(
            data={
                "first_name": row.first_name,
                "middle_name": row.middle_name,
                "last_name": row.last_name,
                "email": row.email,
                "title": row.title,
            },
        )
        if not validation_form.is_valid():
            return ErrorLine(validation_form.cleaned_data, error=validation_form.errors)

        validation_form.cleaned_data["index"] = row.Index  # watch out for "Index" uppercase "I"
        line = ContributionLine(validation_form.cleaned_data)
        line.suggestions = self.make_suggestion(line)
        return line

    def make_suggestion(self, line: ContributionLine) -> Iterable[SuggestionLine]:
        """Take a contribution line and find similar users in the DB."""
        suggestions = []
        try:
            # Find similar users in the DB by email
            # expect at most one and when one is found that is sufficient
            user_with_same_email = core_models.Account.objects.get(email=line.email)
        except core_models.Account.DoesNotExist:
            suggestions = self.make_more_suggestions(line)
        else:
            suggestions.append(SuggestionLine(user_with_same_email, line))

        return suggestions

    def make_more_suggestions(self, line: ContributionLine) -> Iterable[SuggestionLine]:
        """Take a contribution line and find similar users in the DB by euristics."""
        # TODO: use self.form.cleaned_data.match_euristic
        return [
            SuggestionLine(suggestion, line)
            for suggestion in core_models.Account.objects.filter(
                last_name__iexact=line.last_name,
                first_name__istartswith=line.first_name[0],
            )
        ]


imu_edit_formset_factory = modelformset_factory(
    model=core_models.Account,
    form=forms.IMUEditExistingAccounts,
    extra=0,
)


# TODO: protect me!
class IMUStep2(TemplateView):
    """Insert Many Users - second step.

    We should receive a "list" of users/contributions to process.
    """

    def post(self, *args, **kwargs):
        """Process things an necessary.

        We will:
        - create users accounts
        - create articles (linked to the given special issue) if necessary
        - prepare existing accounts for editing if necessary
        """
        # Procedure
        # - while scanning received lines
        #   - accumulate instances of core.Accounts to edit
        #   - also accumulate ODT data, paired with the Accounts
        # - after scanning all lines
        #   - build a queryset ...filter(pk__in( [pk for pk in line] ) )
        #   - use the suggestion pk as key in a dictionary of ODT lines
        # - in the template
        #   - layout all lines (i.e. give a feedback on how the import went)
        #   - cycle for form in formset
        #     - layout the DB data
        #     - layout the ODT data
        #     - layout the form

        # fetch the special issue object; it will be used by all
        # methods that create an article
        self.special_issue = Issue.objects.get(pk=kwargs["pk"])

        # collect accounts we should present for editing and the
        # relative new possible data
        self.accounts_to_edit = []
        self.accounts_new_data = {}

        # TODO: validate... single fields? somthing else???
        self.extra_context = {"lines": [], "edit_suggestions": {}}
        for i in range(int(self.request.POST["tot_lines"])):
            if f"just_the_name_{i}" in self.request.POST:
                # this is just a partition, nothing to do
                self.extra_context["lines"].append(f"{i} - PARTITION")
                continue
            self.process(i)

        # save the special issue because invitees have probably been added
        self.special_issue.save()
        formset = imu_edit_formset_factory(queryset=core_models.Account.objects.filter(pk__in=self.accounts_to_edit))
        return self.render_to_response(
            context=self.get_context_data(
                formset=formset,
                accounts_new_data=self.accounts_new_data,
                special_issue_id=kwargs["pk"],
            ),
        )

    def process(self, index: int):
        """Process line "index"."""
        # Actions come in these forms:
        # - action-1 → skip
        # - action-1 → new
        # - action-1 → db_123
        # - action-1 → edit_123
        # Here we just find to where we should dispatch the processing to.
        action_suggestion = self.request.POST.get(f"action-{index}", "unspecified")
        action, *suggestion = action_suggestion.split("_")
        action = action.lower()
        func = getattr(self, f"action_{action}")
        try:
            if suggestion:
                func(index, int(suggestion[0]))
            else:
                func(index)
        except Exception as e:
            self.add_line(index, msg=f"ERROR - {action.upper()} - {e}", css_class="error")

    def action_new(self, index):
        """Create a contribution and a new core.Account."""
        # It is possible that a new author has multiple entries in the
        # spreadsheet. The first time that we encounter him, it's easy
        # and we create him, but, subsequent encounters should trigger
        # an IntegrityError because the email is constrained as
        # unique. If this happens, to be safe, we must assume that
        # there might be some differences between the two lines of
        # this contributor (misspelled name, different
        # affiliation,...), and so we check.
        form = forms.IMUHelperForm(
            data={
                "first_name": self.request.POST[f"first_name_{index}"],
                "middle_name": self.request.POST[f"middle_name_{index}"],
                "last_name": self.request.POST[f"last_name_{index}"],
                "email": self.request.POST[f"email_{index}"],
            },
        )
        if not form.is_valid():
            self.add_line(
                index,
                msg="ERROR - some error in the data. Doing nothing.",
                css_class="error",
            )
            return

        author, created = core_models.Account.objects.get_or_create(email=form.cleaned_data["email"])
        if created:
            author.first_name = form.cleaned_data["first_name"]
            author.middle_name = form.cleaned_data["middle_name"]
            author.last_name = form.cleaned_data["last_name"]
            author.save()
        else:
            if (
                author.first_name != form.cleaned_data["first_name"]
                or author.middle_name != form.cleaned_data["middle_name"]
                or author.last_name != form.cleaned_data["last_name"]
            ):
                self.add_line(
                    index,
                    msg=f'ERROR - different data for existing user with email "{form.cleaned_data["email"]}".',
                    css_class="error",
                )
                return
        # No need to check if `author` is already in
        # `special_issue.invitees` (django takes care 🎉)
        self.special_issue.invitees.add(author)

        article = self.create_article(index, author)
        self.add_line(index, msg=f"NEW - {article}")

    def action_skip(self, index):
        """Skip."""
        self.add_line(index, msg="SKIP")

    def action_db(self, index, pk):
        """Create a contribution and using the suggested author (core.Account) as-is."""
        author = core_models.Account.objects.get(pk=pk)
        self.special_issue.invitees.add(author)
        article = self.create_article(index, author)
        self.add_line(index, msg=f"DB - {article} by {author}")

    def action_edit(self, index, pk):
        """Create a contribution and prepare the suggested author (core.Account) for editing."""
        author = core_models.Account.objects.get(pk=pk)
        self.special_issue.invitees.add(author)
        article = self.create_article(index, author)

        # I'd prefer to use the author directly, but the formset wants
        # a queryset, not a list...
        # ...accounts_to_edit.append(author)
        self.accounts_to_edit.append(pk)

        odsline = ODSLine(
            first_name=self.request.POST[f"first_name_{index}"],
            middle_name=self.request.POST[f"middle_name_{index}"],
            last_name=self.request.POST[f"last_name_{index}"],
            email=self.request.POST[f"email_{index}"],
        )
        self.accounts_new_data[pk] = odsline
        self.add_line(index, msg=f"EDIT - {article} by {author}", must_edit=True)

    def action_unspecified(self, index):
        """Report 💩."""
        self.add_line(index, msg="UNSPECIFIED - 💩", css_class="error")

    def add_line(self, index, **kwargs):
        """Add a line of data in extra_context."""
        kwargs["index"] = index
        self.extra_context["lines"].append(kwargs)

    def create_article(self, index, author):
        """Create an article with data from the given index and author."""
        if not self.request.POST.get("create_articles_on_import", False):
            return
        article = submission_models.Article(
            # do I need this? last_modified=now()
            journal=self.request.journal,
            # TODO: use only cleaned data (don't use POST directly)
            title=self.request.POST[f"title_{index}"],
            owner=author,
            # TODO: use only cleaned data (don't use POST directly)
            section=submission_models.Section.objects.get(
                pk=self.request.POST["type_of_new_articles"],
                journal=self.request.journal,
            ),
            # TODO: enable choosing a license in the first step
            license=submission_models.Licence.objects.filter(journal=self.request.journal).first(),
            date_started=timezone.now(),
            # date_submitted=... NOPE! this indicates when the submission has been "finished"
            # TODO: find out which "steps" we can choose from and their relation with "stages"
            current_step=1,
            stage=submission_models.STAGE_UNSUBMITTED,
        )
        article.save()  # why doesn't it get saved using `create`?!?
        FrozenAuthor.objects.all().delete()
        FrozenAuthor.objects.create(article=article, author=author)
        self.special_issue.articles.add(article)
        article.refresh_from_db()
        return article


# TODO: protect me!
class IMUStep3(TemplateView):
    """Insert Many Users - last step.

    Edit existing accounts and redirect to special issue ? update / detail ?.
    """

    def post(self, *args, **kwargs):
        """Edit existing accounts."""
        formset = imu_edit_formset_factory(self.request.POST)
        formset.save()
        return redirect(to=reverse("manage_issues_id", kwargs={"issue_id": kwargs["pk"]}))


@has_journal
@journal_decorators.frontend_enabled
def issues(request):
    """Render the list of issues in the journal.

    :param request: the request associated with this call
    :return: a rendered template of all issues
    """
    issue_objects = Issue.objects.filter(
        journal=request.journal,
        date__lte=timezone.now(),
    )
    template = "journal/issues.html"
    context = {
        "issues": issue_objects,
    }
    return render(request, template, context)


class PublishedArticlesListView(PaginatedViewMixin, FormMixin, ListView):
    """
    A list of published articles that can be searched,
    sorted, and filtered
    """

    model = submission_models.Article
    template_name = "journal/search.html"
    paginate_by = "25"
    form_class = forms.SearchForm
    context_object_name = "articles"
    exclude_children = False
    filter_by = None

    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        self.form = self.get_form(self.form_class)
        self.form.is_valid()

    def get_form_kwargs(self):
        """Return the keyword arguments for instantiating the form."""
        kwargs = {
            "initial": self.get_initial(),
            "prefix": self.get_prefix(),
            "data": self.request.GET,
            "journal": self.request.journal,
        }
        return kwargs

    def get_paginate_by(self, queryset):
        return self.form.cleaned_data.get("show", self.paginate_by)

    def _get_filters(self):
        """
        Collect constraints on the queryset.

        Some limitations are from the search form (first batch),
        some are from the filters (filter-by-kwd, etc.; second batch),
        and the rest are common for this journal's published articles.

        authors are excluded from this search because we will use full-text search on the authors' fields alone to be
        able to match full names, initials, etc.
        """
        filters = {
            "keywords__in": self.form.cleaned_data.get("keywords"),
            "section__in": self.form.cleaned_data.get("sections"),
            "date_published__year": self.form.cleaned_data.get("year", None),
            "section": self.kwargs.get("section", self.form.cleaned_data.get("article_type", None)),
            "keywords__pk": self.kwargs.get("keyword"),
            "authors": self.kwargs.get("author", None),
            "journal": self.request.journal,
            "stage": submission_models.STAGE_PUBLISHED,
            "identifier__id_type": self.form.cleaned_data.get("identifier_type", None),
            "identifier__identifier": self.form.cleaned_data.get("article_identifier", None),
            "title__icontains": self.form.cleaned_data.get("article_title", None),
            "abstract__icontains": self.form.cleaned_data.get("article_abstract", None),
        }
        if self.form.cleaned_data.get("date_from", None):
            filters["date_published__gte"] = self.form.cleaned_data.get("date_from")
        if self.form.cleaned_data.get("date_to", None):
            filters["date_published__lte"] = self.form.cleaned_data.get("date_to")
        if not self.form.cleaned_data.get("date_from", None) and not self.form.cleaned_data.get("date_to", None):
            filters["date_published__lte"] = timezone.now()
        return {item: value for item, value in filters.items() if value}

    def _get_pinned_articles(self):
        return [pin.article for pin in PinnedArticle.objects.filter(journal=self.request.journal)]

    def get_queryset(self):
        filters = self._get_filters()
        pinned_article_pks = [article.pk for article in self._get_pinned_articles()]

        search_term = ""
        global_filters = self.form.get_search_filters()
        author_filter = self.form.get_author_filter()

        if global_filters:
            search_term = next(iter(global_filters.values()))
            search_filters = global_filters
        elif author_filter:
            search_term = next(iter(author_filter.values()))
            search_filters = author_filter

        if search_term:
            articles = self.model.objects.search(
                search_term,
                search_filters,
                sort=self.get_order_by(search_term),
                site=self.request.site_object,
            )
            if isinstance(articles, RawQuerySet):
                try:
                    articles_pk = [article.id for article in articles]
                except IndexError:
                    # TODO: investigate and fix the querystring escape problem
                    logger.warning("index error due to search_term escape problem")
                    articles_pk = []
                articles = self.model.objects.filter(pk__in=articles_pk)
        else:
            articles = self.model.objects.all()
        articles = (
            articles.filter(**filters)
            .prefetch_related(
                "frozenauthor_set",
            )
            .exclude(
                pk__in=pinned_article_pks,
            )
        )

        if self.exclude_children:
            articles = articles.exclude(
                ancestors__isnull=False,
            )

        if search_term:
            # if text search is used, articles are already ordered
            return articles
        return articles.order_by(self.get_order_by(search_term))

    def get_order_by(self, search_term):
        sort = self.form.cleaned_data.get("sort", "-date_published")
        if sort == "relevance" and not search_term:
            return "-date_published"
        return sort

    def get_filter_by_configuration(self):
        if self.filter_by == "section":
            return {
                "title": _("Filter by section"),
                "paragraph": _("Publications included in this section."),
                "filtering_object": get_object_or_404(Section, pk=self.kwargs["section"]).name,
            }
        if self.filter_by == "keyword":
            return {
                "title": _("Filter by keyword"),
                "paragraph": _("Publications including this keyword are listed below."),
                "filtering_object": get_object_or_404(Keyword, pk=self.kwargs["keyword"]).word,
            }
        if self.filter_by == "author":
            return {
                "title": _("Filter by author"),
                "paragraph": _("All author's publications are listed below."),
                "filtering_object": get_object_or_404(Account, pk=self.kwargs["author"]).full_name(),
            }

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form"] = self.form
        context["filter_by"] = self.get_filter_by_configuration()
        context["pinned_articles"] = self._get_pinned_articles()
        return context


@user_passes_test(lambda user: get_hijacker() and base_permissions.can_hijack_user_role(get_hijacker(), user))
def set_notify_hijack(request):
    """Toggle silent hijacking."""
    if request.method == "POST":
        request.session["silent_hijack"] = not request.session.get("silent_hijack", False)
    return redirect(request.GET.get("next", "/"))


class KeywordListView(ListView):
    model = Keyword
    template_name = "journal/keywords.html"
    context_object_name = "keywords"
    title = _("Keywords")

    def get_queryset(self):
        return self.model.objects.filter(journal=self.request.journal).order_by("word")


class AuthorSearchView(HtmxMixin, LoginRequiredMixin, ListView):
    model = JCOMProfile
    template_name = "admin/submission/elements/author_search_results.html"
    context_object_name = "authors"

    def get_queryset(self):
        qs = super().get_queryset()
        search_text = self.request.GET.get("author_search_text", "")
        if search_text:
            search_text = search_text.strip()
            orcid_search = search_text if search_text.startswith("http") else f"https://orcid.org/{search_text}"

            qs = qs.filter(
                Q(first_name__icontains=search_text)
                | Q(last_name__icontains=search_text)
                | Q(email__icontains=search_text)
                | Q(orcid=orcid_search)
                | Q(orcid=search_text)
            )
        else:
            qs = qs.none()
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["article_id"] = self.kwargs.get("article_id")
        return context
