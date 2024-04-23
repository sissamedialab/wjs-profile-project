from core import files as core_files
from core.models import File
from django import forms
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.utils.translation import ugettext_lazy as _
from django_summernote.widgets import SummernoteWidget
from plugins.typesetting.models import TypesettingAssignment

from .logic__production import UploadFile
from .models import Message

Account = get_user_model()


class TypesetterUploadFilesForm(forms.ModelForm):
    file_to_upload = forms.FileField(
        label="Select a file",
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

    def get_logic_instance(self) -> UploadFile:
        """Instantiate :py:class:`UploadFile` class."""
        return UploadFile(
            typesetter=self.user,
            request=self.request,
            assignment=self.instance,
            file_to_upload=self.cleaned_data["file_to_upload"],
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


class FileForm(forms.Form):
    file = forms.FileField()


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
            message_type=Message.MessageTypes.VERBOSE,
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
