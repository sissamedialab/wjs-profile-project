from core.models import AccountRole
from django.db import transaction
from django.db.models.signals import m2m_changed, post_delete, post_save, pre_save
from django.dispatch import receiver
from django.utils import timezone
from django_fsm.signals import post_transition
from hijack.signals import hijack_started
from plugins.typesetting.models import TypesettingAssignment
from review.models import EditorAssignment, ReviewAssignment
from submission.models import Article, Section

from wjs.jcom_profile import constants

from .models import (
    ArticleWorkflow,
    PastEditorAssignment,
    WjsEditorAssignment,
    WjsSection,
    WorkflowReviewAssignment,
)
from .role_cache import (
    get_director_user_ids_for_journal,
    invalidate_role_cache_for_user,
    refresh_role_cache_for_article_user_ids,
    refresh_role_cache_for_article_users,
)


@receiver(post_save, sender=Article)
def create_workflow_handler(sender, instance, created, **kwargs):
    """Create :py:class:`ArticleWorkflow` when an article is created."""
    if not created:
        return
    ArticleWorkflow.objects.create(article=instance)


@receiver(post_transition, sender=ArticleWorkflow)
def log_state_change(instance, **kwargs):
    instance.latest_state_change = timezone.now()
    instance.save()


@receiver(post_save, sender=Section)
def create_section_handler(sender, instance, created, **kwargs):
    """Create :py:class:`WjsSection` when a new Section is created."""
    if not created:
        return
    WjsSection(section=instance).save_base(raw=True)


@receiver(hijack_started, sender=None)
def store_current_url_into_session(**kwargs) -> None:
    """
    Store the current URL into the web session.

    Useful to be used as "next" URL when releasing the hijack.
    Use as:
    {% load wjs_tags %}  <!-- neede for "get_value" -->
    ...next={{ request.session|get_value:'hijack_start_url'|default:'/' }}
    """
    request = kwargs["request"]
    # We allow for missing "referer" mainly for tests
    request.session["hijack_start_url"] = request.headers.get("referer", "/")


@receiver(pre_save, sender=Article)
def store_previous_article_role_related_fields(sender, instance: Article, **kwargs):
    """Store old values needed to selectively refresh role cache after article save."""
    if not instance.pk:
        instance._role_cache_old_correspondence_author_id = None
        instance._role_cache_old_journal_id = None
        return
    old = Article.objects.filter(pk=instance.pk).only("correspondence_author_id", "journal_id").first()
    instance._role_cache_old_correspondence_author_id = getattr(old, "correspondence_author_id", None)
    instance._role_cache_old_journal_id = getattr(old, "journal_id", None)


@receiver(pre_save, sender=AccountRole)
def store_previous_account_role_related_fields(sender, instance: AccountRole, **kwargs):
    """Store old values needed to selectively refresh director role cache after role save."""
    if not instance.pk:
        instance._role_cache_old_role_slug = None
        instance._role_cache_old_user_id = None
        instance._role_cache_old_journal_id = None
        return
    old = (
        AccountRole.objects.filter(pk=instance.pk)
        .select_related("role")
        .only("user_id", "journal_id", "role__slug")
        .first()
    )
    instance._role_cache_old_role_slug = getattr(getattr(old, "role", None), "slug", None)
    instance._role_cache_old_user_id = getattr(old, "user_id", None)
    instance._role_cache_old_journal_id = getattr(old, "journal_id", None)


@receiver(post_save, sender=Article)
def refresh_role_cache_after_article_save(sender, instance: Article, **kwargs):
    """Refresh cache entries affected by article author/journal changes."""
    user_ids = set()
    if instance.correspondence_author_id:
        user_ids.add(instance.correspondence_author_id)
    old_correspondence_author_id = getattr(instance, "_role_cache_old_correspondence_author_id", None)
    if old_correspondence_author_id:
        user_ids.add(old_correspondence_author_id)

    old_journal_id = getattr(instance, "_role_cache_old_journal_id", None)
    user_ids.update(get_director_user_ids_for_journal(journal_id=instance.journal_id))
    if old_journal_id and old_journal_id != instance.journal_id:
        user_ids.update(get_director_user_ids_for_journal(journal_id=old_journal_id))

    if user_ids:
        transaction.on_commit(
            lambda: refresh_role_cache_for_article_user_ids(
                article_id=instance.pk,
                user_ids=user_ids,
            )
        )


@receiver(m2m_changed, sender=Article.authors.through)
def refresh_role_cache_after_article_authors_changed(sender, instance: Article, action: str, pk_set, **kwargs):
    """Refresh cache for users whose co-author relation changed."""
    if action == "pre_clear":
        instance._role_cache_old_author_ids = set(instance.authors.values_list("pk", flat=True))
        return

    if action not in {"post_add", "post_remove", "post_clear"}:
        return
    user_ids = set(pk_set or [])
    if action == "post_clear":
        # post_clear provides no pk_set, so use ids stored during pre_clear.
        user_ids = set(getattr(instance, "_role_cache_old_author_ids", set()))
    if not user_ids:
        return
    transaction.on_commit(
        lambda: refresh_role_cache_for_article_user_ids(
            article_id=instance.pk,
            user_ids=user_ids,
        )
    )


@receiver(post_save, sender=WjsEditorAssignment)
@receiver(post_delete, sender=WjsEditorAssignment)
@receiver(post_save, sender=EditorAssignment)
@receiver(post_delete, sender=EditorAssignment)
def refresh_role_cache_after_editor_assignment_changed(sender, instance: EditorAssignment, **kwargs):
    if instance.editor_id and instance.article_id:
        transaction.on_commit(
            lambda: refresh_role_cache_for_article_users(
                article=instance.article,
                users=[instance.editor],
            )
        )


@receiver(post_save, sender=PastEditorAssignment)
@receiver(post_delete, sender=PastEditorAssignment)
def refresh_role_cache_after_past_editor_assignment_changed(sender, instance: PastEditorAssignment, **kwargs):
    if instance.editor_id and instance.article_id:
        transaction.on_commit(
            lambda: refresh_role_cache_for_article_users(
                article=instance.article,
                users=[instance.editor],
            )
        )


@receiver(post_save, sender=ReviewAssignment)
@receiver(post_delete, sender=ReviewAssignment)
@receiver(post_save, sender=WorkflowReviewAssignment)
@receiver(post_delete, sender=WorkflowReviewAssignment)
def refresh_role_cache_after_review_assignment_changed(sender, instance: ReviewAssignment, **kwargs):
    if instance.reviewer_id and instance.article_id:
        transaction.on_commit(
            lambda: refresh_role_cache_for_article_users(
                article=instance.article,
                users=[instance.reviewer],
            )
        )


@receiver(post_save, sender=TypesettingAssignment)
@receiver(post_delete, sender=TypesettingAssignment)
def refresh_role_cache_after_typesetting_assignment_changed(sender, instance: TypesettingAssignment, **kwargs):
    if instance.typesetter_id and instance.round_id:
        transaction.on_commit(
            lambda: refresh_role_cache_for_article_users(
                article=instance.round.article,
                users=[instance.typesetter],
            )
        )


@receiver(post_save, sender=AccountRole)
@receiver(post_delete, sender=AccountRole)
def refresh_role_cache_after_account_role_changed(sender, instance: AccountRole, **kwargs):
    old_role_slug = getattr(instance, "_role_cache_old_role_slug", None)
    old_user_id = getattr(instance, "_role_cache_old_user_id", None)
    old_journal_id = getattr(instance, "_role_cache_old_journal_id", None)

    if (
        old_role_slug == instance.role.slug
        and old_user_id == instance.user_id
        and old_journal_id == instance.journal_id
    ):
        return

    user_ids_to_invalidate: set[int] = set()

    if instance.role.slug == constants.DIRECTOR_ROLE and instance.user_id and instance.journal_id:
        user_ids_to_invalidate.add(instance.user_id)

    if old_role_slug == constants.DIRECTOR_ROLE and old_user_id and old_journal_id:
        user_ids_to_invalidate.add(old_user_id)

    if not user_ids_to_invalidate:
        return

    for user_id in user_ids_to_invalidate:
        transaction.on_commit(lambda user_id=user_id: invalidate_role_cache_for_user(user_id=user_id))
