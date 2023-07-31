from django.contrib.auth.mixins import UserPassesTestMixin


class EditorRequiredMixin(UserPassesTestMixin):
    """Mixin to check if the user is an editor for the current journal."""

    def test_func(self):
        if self.request.user.is_anonymous:
            return False
        is_section_editor = self.request.user.check_role(self.request.journal, "section-editor")
        return is_section_editor


class ReviewerRequiredMixin(UserPassesTestMixin):
    """Mixin to check if the user is a reviewer or an editor for the current journal."""

    def test_func(self):
        if self.request.user.is_anonymous:
            return False
        is_reviewer = self.request.user.check_role(self.request.journal, "reviewer")
        is_section_editor = self.request.user.check_role(self.request.journal, "section-editor")
        return is_reviewer or is_section_editor
