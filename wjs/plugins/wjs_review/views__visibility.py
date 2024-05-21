from core.models import Account
from django import forms
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.views.generic import FormView

from .logic__visibility import GrantPermissionDispatcher
from .models import ArticleWorkflow, PermissionAssignment, WjsEditorAssignment


class AssignPermissionForm(forms.Form):
    user = forms.ModelChoiceField(queryset=None)
    permission_type = forms.ChoiceField(choices=PermissionAssignment.PermissionType.choices)

    def _get_users_for_article(self, article):
        authors = article.authors.values_list("pk", flat=True)
        editors = WjsEditorAssignment.objects.get_all(article).values_list("editor", flat=True)
        past_editors = article.past_editor_assignments.all().values_list("editor", flat=True)
        reviewers = article.reviewassignment_set.values_list("reviewer", flat=True)
        selected_ids = set(authors) | set(editors) | set(past_editors) | set(reviewers)
        return Account.objects.filter(pk__in=selected_ids)

    def __init__(self, *args, **kwargs):
        """Set the queryset for the recipient."""
        self.article = kwargs.pop("article")
        self.object_type = kwargs.pop("object_type")
        self.object_id = kwargs.pop("object_id")
        super().__init__(*args, **kwargs)
        allowed_recipients = self._get_users_for_article(self.article)
        self.fields["user"].queryset = allowed_recipients

    def get_logic_instance(self) -> GrantPermissionDispatcher:
        """Instantiate :py:class:`GrantPermissionDispatcher` class."""
        service = GrantPermissionDispatcher(
            user=self.cleaned_data.get("user"),
            object_type=self.object_type,
            object_id=self.object_id,
        )
        return service

    def save(self) -> PermissionAssignment:
        """Change the reviewer report due date using :py:class:`GrantPermissionDispatcher`."""
        try:
            service = self.get_logic_instance()
            self.instance = service.run(self.cleaned_data.get("permission_type"))
        except ValidationError as e:
            self.add_error(None, e)
            raise
        return self.instance


class AssignPermission(FormView):
    model = ArticleWorkflow
    form_class = AssignPermissionForm
    template_name = "wjs_review/assign_permission.html"

    def setup(self, request, *args, **kwargs):
        super().setup(request, *args, **kwargs)
        self.workflow = self.model.objects.get(pk=kwargs["pk"])

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["article"] = self.workflow.article
        kwargs["object_type"] = self.kwargs.get("object_type", "editorassignment")
        kwargs["object_id"] = self.kwargs.get("object_id", WjsEditorAssignment.objects.get_current(self.workflow).pk)
        return kwargs

    def form_valid(self, form):
        form.save()
        messages.add_message(self.request, messages.SUCCESS, "Permissions added.")
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["workflow"] = self.workflow
        return context

    def get_success_url(self):
        return self.workflow.get_absolute_url()
