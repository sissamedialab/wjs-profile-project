from typing import TYPE_CHECKING

from django.contrib.auth import get_user_model

if TYPE_CHECKING:
    from .models import ArticleWorkflow

Account = get_user_model()


def is_section_editor(instance: "ArticleWorkflow", user: Account) -> bool:
    return user.check_role(instance.article.journal, "section-editor")


def is_editor(instance: "ArticleWorkflow", user: Account) -> bool:
    return user.check_role(instance.article.journal, "editor")


def is_director(instance: "ArticleWorkflow", user: Account) -> bool:
    return user.check_role(instance.article.journal, "director")


def is_admin(instance: "ArticleWorkflow", user: Account) -> bool:
    """Return True is the user is staff, also meaning EO."""
    return user.is_staff


def is_reviewer(instance: "ArticleWorkflow", user: Account) -> bool:
    return user.check_role(instance.article.journal, "reviewer")


def is_author(instance: "ArticleWorkflow", user: Account) -> bool:
    return user.check_role(instance.article.journal, "author")


def is_section_editor_or_editor(instance: "ArticleWorkflow", user: Account) -> bool:
    return is_section_editor(instance, user) or is_editor(instance, user)


def is_section_editor_or_reviewer(instance: "ArticleWorkflow", user: Account) -> bool:
    return is_section_editor(instance, user) or is_reviewer(instance, user)


def is_article_editor(instance: "ArticleWorkflow", user: Account) -> bool:
    return (
        is_section_editor(instance, user) or is_editor(instance, user)
    ) and instance.article.editorassignment_set.filter(editor=user).exists()


def is_system(instance: "ArticleWorkflow", user: Account) -> bool:
    """Fake permission for system-managed transitions."""
    return user is None
