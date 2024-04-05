from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from plugins.typesetting.models import TypesettingAssignment

from .logic__production import UploadFile

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
