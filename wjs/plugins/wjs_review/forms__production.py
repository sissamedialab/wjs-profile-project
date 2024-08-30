from core import files as core_files
from core.models import File
from django import forms
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404
from django.utils.translation import gettext_lazy as _
from django_summernote.widgets import SummernoteWidget
from journal.models import Issue, SectionOrdering
from plugins.typesetting.models import GalleyProofing, TypesettingAssignment
from submission import models as submission_models

from .logic__production import (
    HandleCreateAnnotatedFile,
    HandleCreateSupplementaryFile,
    HandleDeleteAnnotatedFile,
    HandleEOSendBackToTypesetter,
    UploadFile,
)
from .models import ArticleWorkflow, Message

Account = get_user_model()


class TypesetterUploadFilesForm(forms.ModelForm):
    file_to_upload = forms.FileField(
        label=_("Select file to upload"),
        required=True,
    )

    class Meta:
        model = TypesettingAssignment
        # NB: contrary to Janeway's standard behavior, this field will hold the files that the typesetter uploaded,
        # i.e. no the ones that he received/downloaded!
        fields = ["files_to_typeset"]

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user")
        self.request = kwargs.pop("request")
        super().__init__(*args, **kwargs)

    def clean_file_to_upload(self):
        file = self.cleaned_data["file_to_upload"]
        if file and file.content_type not in ["application/zip"]:
            raise ValidationError(_("Only ZIP files are allowed"))
        return file

    def get_logic_instance(self) -> UploadFile:
        """Instantiate :py:class:`UploadFile` class."""
        return UploadFile(
            typesetter=self.user,
            request=self.request,
            assignment=self.instance,
            file_to_upload=self.cleaned_data["file_to_upload"],
        )

    def save(self, commit=True) -> TypesettingAssignment:
        try:
            service = self.get_logic_instance()
            service.run()
        except ValidationError as e:
            # this is only possible because we have a single field, so it makes sense to assign all errors to it
            self.add_error("file_to_upload", e)
            raise
        self.instance.refresh_from_db()
        return self.instance


class EsmFileForm(forms.Form):
    file = forms.FileField(
        label=_("Select supplementary file"),
        required=True,
    )

    def __init__(self, *args, **kwargs):
        self.instance = kwargs.pop("instance")
        self.user = kwargs.pop("user")
        super().__init__(*args, **kwargs)

    def get_logic_instance(self) -> HandleCreateSupplementaryFile:
        """Instantiate :py:class:`HandleCreateSupplementaryFile` class."""
        return HandleCreateSupplementaryFile(
            file=self.files["file"],
            article=self.instance.article,
            user=self.user,
        )

    def save(self, commit=True) -> ArticleWorkflow:
        try:
            service = self.get_logic_instance()
            service.run()
        except ValidationError as e:
            self.add_error(None, e)
            raise
        self.instance.refresh_from_db()
        return self.instance


class WriteToTypMessageForm(forms.Form):
    """Simple form used by an author who want to contact the typesetter.

    The author cannot choose the recipient of the message,
    and the name of the typesetter should be hidden from him.
    """

    subject = forms.CharField(required=True, label="Subject")
    body = forms.CharField(required=True, label="Body", widget=SummernoteWidget())
    attachment = forms.FileField(required=False, label=_("Optional attachment"))

    def __init__(self, *args, **kwargs):
        """Store away user and article."""
        self.actor = kwargs.pop("actor")
        self.article = kwargs.pop("article")
        self.recipients = kwargs.pop("recipients")
        super().__init__(*args, **kwargs)

    def create_message(self, to_be_forwarded_to=None):
        """Create and send the message for the typesetter."""
        message = Message.objects.create(
            actor=self.actor,
            message_type=Message.MessageTypes.USER,
            content_type=ContentType.objects.get_for_model(self.article),
            object_id=self.article.pk,
            subject=self.cleaned_data["subject"],
            body=self.cleaned_data["body"],
            to_be_forwarded_to=to_be_forwarded_to,
        )
        message.recipients.add(self.recipients)

        if self.cleaned_data["attachment"]:
            attachment: File = core_files.save_file_to_article(
                file_to_handle=self.cleaned_data["attachment"],
                article=self.article,
                owner=self.actor,
                label=None,  # TODO: TBD: no label (default)
                description=None,  # TODO: TBD: no description (default)
            )
            message.attachments.add(attachment)

        message.emit_notification()

        return message


class UploadAnnotatedFilesForm(forms.ModelForm):
    file = forms.FileField(required=False)
    notes = forms.CharField(widget=forms.Textarea, required=False)
    action = forms.ChoiceField(
        required=False,
        choices=(
            ("upload_file", "Upload file"),
            ("send_corrections", "Send corrections"),
            ("delete_file", "Delete file"),
        ),
    )

    class Meta:
        model = GalleyProofing
        fields = ["notes"]

    def __init__(self, *args, **kwargs):
        self.article = kwargs.pop("article")
        self.galleyproofing = kwargs.pop("galleyproofing")
        self.request = kwargs.pop("request")
        super().__init__(*args, **kwargs)

    def get_logic_instance(self) -> HandleCreateAnnotatedFile:
        """Instantiate :py:class:`HandleCreateAnnotatedFile` class."""
        return HandleCreateAnnotatedFile(
            galleyproofing=self.galleyproofing,
            file=self.cleaned_data["file"],
            user=self.request.user,
        )

    def get_delete_logic_instance(self) -> HandleDeleteAnnotatedFile:
        """Instantiate :py:class:`HandleDeleteAnnotatedFile` class."""
        return HandleDeleteAnnotatedFile(
            file_id=self.request.POST.get("file_to_delete"),
            galleyproofing=self.galleyproofing,
            user=self.request.user,
        )

    def clean(self):
        cleaned_data = super().clean()
        if not cleaned_data.get("action"):
            raise ValidationError(_("No action selected"))
        if self.cleaned_data["action"] == "upload_file":
            if not cleaned_data.get("file"):
                self.add_error("file", ValidationError(_("No file provided")))
        if (
            cleaned_data["action"] == "send_corrections"
            and not cleaned_data.get("notes")
            and not self.galleyproofing.annotated_files.exists()
        ):
            self.add_error("notes", ValidationError(_("No correction provided")))
        return cleaned_data

    def save(self, commit=True) -> GalleyProofing:
        if self.cleaned_data["action"] == "upload_file":
            service = self.get_logic_instance()
            return service.run()
        elif self.cleaned_data["action"] == "delete_file":
            service = self.get_delete_logic_instance()
            return service.run()
        else:
            self.galleyproofing.notes = self.cleaned_data["notes"]
            self.galleyproofing.save()
            return self.galleyproofing


class EOSendBackToTypesetterForm(forms.Form):
    """Form used by the EO to send a paper back to typesetter."""

    subject = forms.CharField(required=True, label="Subject")
    body = forms.CharField(required=True, label="Body", widget=SummernoteWidget())

    def __init__(self, *args, **kwargs):
        """Store away user, article and typesetter assignement."""
        self.user = kwargs.pop("user")
        self.instance = kwargs.pop("workflow")
        self.assignment = kwargs.pop("assignment")
        super().__init__(*args, **kwargs)

    def get_logic_instance(self) -> HandleEOSendBackToTypesetter:
        """Instantiate :py:class:`HandleEOSendBackToTypesetter` class."""
        return HandleEOSendBackToTypesetter(
            workflow=self.instance,
            old_assignment=self.assignment,
            user=self.user,
            body=self.cleaned_data["body"],
            subject=self.cleaned_data["subject"],
        )

    def save(self):
        try:
            service = self.get_logic_instance()
            service.run()
        except ValidationError as e:
            self.add_error(None, e)
            raise
        self.instance.refresh_from_db()
        return self.instance


class SectionOrderForm(forms.Form):
    up = forms.IntegerField(widget=forms.HiddenInput, required=False)
    down = forms.IntegerField(widget=forms.HiddenInput, required=False)

    def __init__(self, *args, **kwargs):
        self.journal = kwargs.pop("journal")
        self.instance = kwargs.pop("instance")
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned_data = super().clean()
        if not cleaned_data.get("up") and not cleaned_data.get("down"):
            raise ValidationError("No action selected")
        return cleaned_data

    def clean_up(self):
        section_id = self.data.get("up")
        if section_id:
            section_to_move = get_object_or_404(submission_models.Section, pk=section_id, journal=self.journal)
            if section_to_move == self.instance.first_section:
                raise ValidationError("You cannot move the first section up the order list")
        return section_id

    def clean_down(self):
        section_id = self.data.get("down")
        if section_id:
            section_to_move = get_object_or_404(submission_models.Section, pk=section_id, journal=self.journal)
            if section_to_move == self.instance.last_section:
                raise ValidationError("You cannot move the last section down the order list")
        return section_id

    def move_up(self, section_id: int) -> Issue:
        sections = self.instance.all_sections

        section_to_move_up = get_object_or_404(submission_models.Section, pk=section_id, journal=self.journal)

        section_to_move_up_index = sections.index(section_to_move_up)
        section_to_move_down = sections[section_to_move_up_index - 1]

        section_to_move_up_ordering, __ = SectionOrdering.objects.get_or_create(
            issue=self.instance, section=section_to_move_up
        )
        section_to_move_down_ordering, __ = SectionOrdering.objects.get_or_create(
            issue=self.instance, section=section_to_move_down
        )

        section_to_move_up_ordering.order = section_to_move_up_index - 1
        section_to_move_down_ordering.order = section_to_move_up_index

        section_to_move_up_ordering.save()
        section_to_move_down_ordering.save()
        return self.instance

    def move_down(self, section_id: int) -> Issue:
        sections = self.instance.all_sections

        section_to_move_down = get_object_or_404(
            submission_models.Section,
            pk=section_id,
            journal=self.journal,
        )

        section_to_move_down_index = sections.index(section_to_move_down)
        section_to_move_up = sections[section_to_move_down_index + 1]

        section_to_move_up_ordering, __ = SectionOrdering.objects.get_or_create(
            issue=self.instance, section=section_to_move_up
        )
        section_to_move_down_ordering, __ = SectionOrdering.objects.get_or_create(
            issue=self.instance, section=section_to_move_down
        )

        section_to_move_up_ordering.order = section_to_move_down_index
        section_to_move_down_ordering.order = section_to_move_down_index + 1

        section_to_move_up_ordering.save()
        section_to_move_down_ordering.save()
        return self.instance

    def save(self) -> Issue:
        if self.cleaned_data.get("up"):
            return self.move_up(self.cleaned_data.get("up"))
        elif self.cleaned_data.get("down"):
            return self.move_down(self.cleaned_data.get("down"))
