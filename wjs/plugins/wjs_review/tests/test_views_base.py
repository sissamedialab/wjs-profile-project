from unittest.mock import patch

import pytest
from core.models import File
from plugins.typesetting.models import GalleyProofing, TypesettingAssignment

from ..communication_utils import log_operation, log_silent_operation
from ..models import ProphyAccount, WjsEditorAssignment


@pytest.mark.django_db
def test_wjs_review_list(assigned_article, client):
    assignment = WjsEditorAssignment.objects.get_current(assigned_article)
    client.force_login(assignment.editor)
    response = client.get(f"/{assigned_article.journal.code}/plugins/wjs-review-articles/editor/pending/")
    assert response.status_code == 200


@pytest.mark.django_db
def test_wjs_review_archived_papers(assigned_article, client):
    assignment = WjsEditorAssignment.objects.get_current(assigned_article)
    client.force_login(assignment.editor)
    response = client.get(f"/{assigned_article.journal.code}/plugins/wjs-review-articles/editor/archived/")
    assert response.status_code == 200


@pytest.mark.django_db
def test_wjs_review_eo_pending(journal, eo_user, client):
    client.force_login(eo_user)
    response = client.get(f"/{journal.code}/plugins/wjs-review-articles/eo/pending/")
    assert response.status_code == 200


@pytest.mark.django_db
def test_wjs_review_eo_archived(journal, eo_user, client):
    client.force_login(eo_user)
    response = client.get(f"/{journal.code}/plugins/wjs-review-articles/eo/archived/")
    assert response.status_code == 200


@pytest.mark.django_db
def test_wjs_review_eo_production(journal, eo_user, client):
    client.force_login(eo_user)
    response = client.get(f"/{journal.code}/plugins/wjs-review-articles/eo/production/")
    assert response.status_code == 200


@pytest.mark.django_db
def test_wjs_review_eo_workon(journal, eo_user, client):
    client.force_login(eo_user)
    response = client.get(f"/{journal.code}/plugins/wjs-review-articles/eo/workon/")
    assert response.status_code == 200


@pytest.mark.django_db
def test_wjs_review_director_pending(journal, director, client):
    client.force_login(director)
    response = client.get(f"/{journal.code}/plugins/wjs-review-articles/director/pending/")
    assert response.status_code == 200


@pytest.mark.django_db
def test_wjs_review_director_archived(journal, director, client):
    client.force_login(director)
    response = client.get(f"/{journal.code}/plugins/wjs-review-articles/director/archived/")
    assert response.status_code == 200


@pytest.mark.django_db
def test_wjs_review_director_workon(journal, director, client):
    client.force_login(director)
    response = client.get(f"/{journal.code}/plugins/wjs-review-articles/director/workon/")
    assert response.status_code == 200


@pytest.mark.django_db
def test_wjs_review_author_pending(assigned_article, client):
    client.force_login(assigned_article.correspondence_author)
    response = client.get(f"/{assigned_article.journal.code}/plugins/wjs-review-articles/author/pending/")
    assert response.status_code == 200


@pytest.mark.django_db
def test_wjs_review_author_archived(assigned_article, client):
    client.force_login(assigned_article.correspondence_author)
    response = client.get(f"/{assigned_article.journal.code}/plugins/wjs-review-articles/author/archived/")
    assert response.status_code == 200


@pytest.mark.django_db
def test_wjs_review_rfp_author(assigned_article, client):
    client.force_login(assigned_article.correspondence_author)
    response = client.get(
        f"/{assigned_article.journal.code}/plugins/wjs-review-articles/author/rfp/"
        f"{assigned_article.articleworkflow.pk}/"
    )
    assert response.status_code == 302


@pytest.mark.django_db
def test_wjs_review_reviewer_pending(review_assignment, client):
    client.force_login(review_assignment.reviewer)
    response = client.get(f"/{review_assignment.article.journal.code}/plugins/wjs-review-articles/reviewer/pending/")
    assert response.status_code == 200


@pytest.mark.django_db
def test_wjs_review_reviewer_archived(review_assignment, client):
    client.force_login(review_assignment.reviewer)
    response = client.get(f"/{review_assignment.article.journal.code}/plugins/wjs-review-articles/reviewer/archived/")
    assert response.status_code == 200


@pytest.mark.django_db
def test_wjs_review_typesetter_pending(assigned_to_typesetter_article_with_files_to_typeset, client):
    assignment = TypesettingAssignment.objects.get(round__article=assigned_to_typesetter_article_with_files_to_typeset)
    client.force_login(assignment.typesetter)
    response = client.get(
        f"/{assigned_to_typesetter_article_with_files_to_typeset.journal.code}/"
        f"plugins/wjs-review-articles/typesetter/pending/"
    )
    assert response.status_code == 200


@pytest.mark.django_db
def test_wjs_review_typesetter_workingon(assigned_to_typesetter_article_with_files_to_typeset, client):
    assignment = TypesettingAssignment.objects.get(round__article=assigned_to_typesetter_article_with_files_to_typeset)
    client.force_login(assignment.typesetter)
    response = client.get(
        f"/{assigned_to_typesetter_article_with_files_to_typeset.journal.code}/"
        f"plugins/wjs-review-articles/typesetter/workingon/"
    )
    assert response.status_code == 200


@pytest.mark.django_db
def test_wjs_review_typesetter_archived(assigned_to_typesetter_article_with_files_to_typeset, client):
    assignment = TypesettingAssignment.objects.get(round__article=assigned_to_typesetter_article_with_files_to_typeset)
    client.force_login(assignment.typesetter)
    response = client.get(
        f"/{assigned_to_typesetter_article_with_files_to_typeset.journal.code}/"
        f"plugins/wjs-review-articles/typesetter/archived/"
    )
    assert response.status_code == 200


@pytest.mark.django_db
def test_wjs_review_rfp2(assigned_to_typesetter_article_with_files_to_typeset, client):
    assignment = TypesettingAssignment.objects.get(round__article=assigned_to_typesetter_article_with_files_to_typeset)
    client.force_login(assignment.typesetter)
    response = client.get(
        f"/{assigned_to_typesetter_article_with_files_to_typeset.journal.code}/"
        f"plugins/wjs-review-articles/typesetter/rfp/{assigned_to_typesetter_article_with_files_to_typeset.pk}/"
    )
    assert response.status_code == 302


@pytest.mark.django_db
def test_wjs_assign_eo(assigned_article, eo_user, client):
    client.force_login(eo_user)
    response = client.get(
        f"/{assigned_article.journal.code}/plugins/wjs-review-articles/assign_eo/"
        f"{assigned_article.articleworkflow.pk}/"
    )
    assert response.status_code == 200


@pytest.mark.django_db
def test_wjs_editor_assigns_themselves_as_reviewer(assigned_article, client):
    assignment = WjsEditorAssignment.objects.get_current(assigned_article)
    client.force_login(assignment.editor)
    response = client.get(
        f"/{assigned_article.journal.code}/plugins/wjs-review-articles/editor_assigns_themselves_as_reviewer/"
        f"{assigned_article.articleworkflow.pk}/"
    )
    assert response.status_code == 200


@pytest.mark.django_db
def test_wjs_select_reviewer(assigned_article, client):
    assignment = WjsEditorAssignment.objects.get_current(assigned_article)
    client.force_login(assignment.editor)
    response = client.get(
        f"/{assigned_article.journal.code}/plugins/wjs-review-articles/select_reviewer/"
        f"{assigned_article.articleworkflow.pk}/"
    )
    assert response.status_code == 200


@pytest.mark.django_db
def test_wjs_deselect_reviewer(review_assignment, client):
    client.force_login(review_assignment.editor)
    response = client.get(
        f"/{review_assignment.article.journal.code}/plugins/wjs-review-articles/"
        f"deselect_reviewer/{review_assignment.pk}/"
    )
    assert response.status_code == 200


@pytest.mark.django_db
def test_wjs_assigns_editor(assigned_article, eo_user, client):
    client.force_login(eo_user)
    response = client.get(
        f"/{assigned_article.journal.code}/plugins/wjs-review-articles/assigns_editor/"
        f"{assigned_article.articleworkflow.pk}/"
    )
    assert response.status_code == 200


@pytest.mark.django_db
def test_wjs_postpone_reviewer_due_date(review_assignment, eo_user, client):
    client.force_login(review_assignment.editor)
    response = client.get(
        f"/{review_assignment.article.journal.code}/plugins/wjs-review-articles/"
        f"postpone_duedate/{review_assignment.pk}/"
    )
    assert response.status_code == 200


@pytest.mark.django_db
def test_wjs_invite_reviewer(assigned_article, eo_user, client):
    assignment = WjsEditorAssignment.objects.get_current(assigned_article)
    client.force_login(assignment.editor)
    response = client.get(
        f"/{assigned_article.journal.code}/plugins/wjs-review-articles/"
        f"invite_reviewer/{assigned_article.articleworkflow.pk}/"
    )
    assert response.status_code == 200


@pytest.mark.django_db
def test_wjs_postpone_revision_request(editor_revision, client):
    client.force_login(editor_revision.editor)
    response = client.get(
        f"/{editor_revision.article.journal.code}/plugins/wjs-review-articles/"
        f"postpone_revision_request/{editor_revision.pk}/"
    )
    assert response.status_code == 200


@pytest.mark.skip(reason="FIXME: Creating ProphyAccount is complex.")
@pytest.mark.django_db
def test_wjs_invite_reviewer_prophy(assigned_article, eo_user, client):
    assignment = WjsEditorAssignment.objects.get_current(assigned_article)
    prophy_account = ProphyAccount.objects.create()
    client.force_login(assignment.editor)
    response = client.get(
        f"/{assigned_article.journal.code}/plugins/wjs-review-articles/"
        f"invite_reviewer/{assigned_article.articleworkflow.pk}/{prophy_account.pk}/"
    )
    assert response.status_code == 200


@pytest.mark.django_db
def test_wjs_article_details(assigned_article, eo_user, client):
    client.force_login(eo_user)
    response = client.get(
        f"/{assigned_article.journal.code}/plugins/wjs-review-articles/status/{assigned_article.articleworkflow.pk}/"
    )
    assert response.status_code == 200


@pytest.mark.django_db
def test_wjs_article_decision(assigned_article, client):
    assignment = WjsEditorAssignment.objects.get_current(assigned_article)
    client.force_login(assignment.editor)
    response = client.get(
        f"/{assigned_article.journal.code}/plugins/wjs-review-articles/decision/{assigned_article.articleworkflow.pk}/"
    )
    assert response.status_code == 200


@pytest.mark.django_db
def test_wjs_article_admin_decision(assigned_article, eo_user, client):
    client.force_login(eo_user)
    response = client.get(
        f"/{assigned_article.journal.code}/plugins/wjs-review-articles/"
        f"admin_decision/{assigned_article.articleworkflow.pk}/"
    )
    assert response.status_code == 200


@pytest.mark.django_db
def test_wjs_article_dispatch_assignment(assigned_article, eo_user, client):
    assigned_article.articleworkflow.state = assigned_article.articleworkflow.ReviewStates.PAPER_MIGHT_HAVE_ISSUES
    assigned_article.articleworkflow.save()
    client.force_login(eo_user)
    response = client.get(
        f"/{assigned_article.journal.code}/plugins/wjs-review-articles/"
        f"dispatch_assignment/{assigned_article.articleworkflow.pk}/"
    )
    assert response.status_code == 302


@pytest.mark.django_db
def test_wjs_unassign_assignment(assigned_article, client):
    assignment = WjsEditorAssignment.objects.get_current(assigned_article)
    client.force_login(assignment.editor)
    response = client.get(
        f"/{assigned_article.journal.code}/plugins/wjs-review-articles/"
        f"decision/unassign/{assigned_article.articleworkflow.pk}/"
    )
    assert response.status_code == 200


@pytest.mark.django_db
def test_wjs_review_review(review_assignment, client):
    client.force_login(review_assignment.reviewer)
    response = client.get(
        f"/{review_assignment.article.journal.code}/plugins/wjs-review-articles/review/{review_assignment.pk}/"
    )
    assert response.status_code == 200


@pytest.mark.django_db
def test_wjs_review_end(review_assignment, client):
    client.force_login(review_assignment.reviewer)
    response = client.get(
        f"/{review_assignment.article.journal.code}/plugins/wjs-review-articles/review/{review_assignment.pk}/end/"
    )
    assert response.status_code == 200


@pytest.mark.django_db
def test_wjs_evaluate_review_direct(review_assignment, client):
    client.force_login(review_assignment.reviewer)
    response = client.get(
        f"/{review_assignment.article.journal.code}/plugins/wjs-review-articles/review/"
        f"{review_assignment.pk}/evaluate/"
    )
    assert response.status_code == 200


@pytest.mark.django_db
def test_wjs_evaluate_review_token(review_assignment_invited_user, client):
    token = review_assignment_invited_user.reviewer.jcomprofile.invitation_token
    response = client.get(
        f"/{review_assignment_invited_user.article.journal.code}/plugins/wjs-review-articles/"
        f"review/{review_assignment_invited_user.pk}/evaluate/{token}/"
    )
    assert response.status_code == 200


@pytest.mark.django_db
def test_wjs_declined_review(review_assignment, client):
    client.force_login(review_assignment.reviewer)
    response = client.get(
        f"/{review_assignment.article.journal.code}/plugins/wjs-review-articles/review/"
        f"{review_assignment.pk}/declined/"
    )
    assert response.status_code == 200


@pytest.mark.django_db
def test_wjs_upload_file(editor_revision, client):
    article = editor_revision.article
    client.force_login(article.correspondence_author)
    response = client.get(
        f"/{article.journal.code}/plugins/wjs-review-articles/article/{article.pk}/"
        f"revision/{editor_revision.pk}/upload/"
    )
    assert response.status_code == 200


@pytest.mark.django_db
def test_wjs_article_messages(assigned_article, client):
    client.force_login(assigned_article.correspondence_author)
    response = client.get(
        f"/{assigned_article.journal.code}/plugins/wjs-review-articles/messages/{assigned_article.articleworkflow.pk}/"
    )
    assert response.status_code == 200


@pytest.mark.django_db
def test_wjs_message_write(assigned_article, eo_user, client):
    client.force_login(eo_user)
    response = client.get(
        f"/{assigned_article.journal.code}/plugins/wjs-review-articles/messages/"
        f"{assigned_article.articleworkflow.pk}/{assigned_article.correspondence_author.pk}/"
    )
    assert response.status_code == 200


@pytest.mark.django_db
def test_wjs_message_toggle_read_by_eo(assigned_article, eo_user, client):
    message = log_operation(assigned_article, "test", "test", recipients=[eo_user])
    client.force_login(eo_user)
    response = client.get(
        f"/{assigned_article.journal.code}/plugins/wjs-review-articles/messages/toggle_read_by_eo/{message.pk}/"
    )
    assert response.status_code == 200


@pytest.mark.django_db
def test_wjs_message_toggle_read(assigned_article, eo_user, client):
    message = log_operation(assigned_article, "test", "test", recipients=[eo_user])
    client.force_login(eo_user)
    response = client.get(
        f"/{assigned_article.journal.code}/plugins/wjs-review-articles/messages/toggle_read/{message.pk}/{eo_user.pk}/"
    )
    assert response.status_code == 200


@pytest.mark.skip(reason="FIXME: How to create proper janeway's File.")
@pytest.mark.django_db
def test_wjs_message_download_attachment(assigned_article, eo_user, client):
    file = File.objects.create()
    message = log_operation(assigned_article, "test", "test", recipients=[eo_user])
    message.attachments.add(file)
    client.force_login(eo_user)
    response = client.get(
        f"/{assigned_article.journal.code}/plugins/wjs-review-articles/messages/attachment/{message.pk}/{file.pk}/"
    )
    assert response.status_code == 200


@pytest.mark.django_db
def test_wjs_message_write_to_typ(assigned_to_typesetter_article_with_files_to_typeset, eo_user, client):
    client.force_login(eo_user)
    response = client.get(
        f"/plugins/wjs-review-articles/messages/writetotyp/"
        f"{assigned_to_typesetter_article_with_files_to_typeset.articleworkflow.pk}/"
    )
    assert response.status_code == 200


@pytest.mark.django_db
def test_wjs_message_write_to_auwm(assigned_to_typesetter_article_with_files_to_typeset, client):
    assignment = TypesettingAssignment.objects.get(round__article=assigned_to_typesetter_article_with_files_to_typeset)
    client.force_login(assignment.typesetter)
    response = client.get(
        f"/{assigned_to_typesetter_article_with_files_to_typeset.journal.code}/plugins/wjs-review-articles/messages/"
        f"writetoau/{assigned_to_typesetter_article_with_files_to_typeset.articleworkflow.pk}/"
    )
    assert response.status_code == 200


@pytest.mark.django_db
def test_wjs_message_forward(assigned_article, eo_user, client):
    client.force_login(eo_user)
    message = log_operation(assigned_article, "test", "test", recipients=[eo_user])
    message.to_be_forwarded_to = assigned_article.correspondence_author
    message.save()
    response = client.get(
        f"/{assigned_article.journal.code}/plugins/wjs-review-articles/messages/forward/{message.pk}/"
    )
    assert response.status_code == 200


@pytest.mark.xfail(reason="This test fails as not recipient is set for silent messages")
@pytest.mark.django_db
def test_wjs_message_forward_no_recipients(assigned_article, eo_user, client):
    client.force_login(eo_user)
    message = log_silent_operation(assigned_article, "test")
    response = client.get(
        f"/{assigned_article.journal.code}/plugins/wjs-review-articles/messages/forward/{message.pk}/"
    )
    assert response.status_code == 200


@pytest.mark.django_db
def test_wjs_article_reminders(assigned_article, eo_user, client):
    client.force_login(eo_user)
    response = client.get(
        f"/{assigned_article.journal.code}/plugins/wjs-review-articles/article/{assigned_article.pk}/reminders/"
    )
    assert response.status_code == 200


@pytest.mark.django_db
def test_wjs_journal_editors(journal, eo_user, client):
    client.force_login(eo_user)
    response = client.get(f"/{journal.code}/plugins/wjs-review-articles/journal_editors/")
    assert response.status_code == 200


@pytest.mark.django_db
def test_wjs_typesetter_upload_files(assigned_to_typesetter_article_with_files_to_typeset, client):
    assignment = TypesettingAssignment.objects.get(round__article=assigned_to_typesetter_article_with_files_to_typeset)
    client.force_login(assignment.typesetter)
    response = client.get(
        f"/{assigned_to_typesetter_article_with_files_to_typeset.journal.code}/"
        f"plugins/wjs-review-articles/upload_files/{assignment.pk}/"
    )
    assert response.status_code == 200


@pytest.mark.django_db
def test_wjs_ready_for_proofreading(assigned_to_typesetter_article_with_files_to_typeset, client):
    assignment = TypesettingAssignment.objects.get(round__article=assigned_to_typesetter_article_with_files_to_typeset)
    client.force_login(assignment.typesetter)
    response = client.get(
        f"/{assigned_to_typesetter_article_with_files_to_typeset.journal.code}/"
        f"plugins/wjs-review-articles/ready_for_proofreading/{assignment.pk}/"
    )
    assert response.status_code == 302


@pytest.mark.django_db
def test_wjs_list_annotated_files(stage_proofing_article, client):
    proofing = GalleyProofing.objects.get(round__article=stage_proofing_article)
    client.force_login(stage_proofing_article.correspondence_author)
    response = client.get(
        f"/{stage_proofing_article.journal.code}/plugins/wjs-review-articles/annotated_files/{proofing.pk}/"
    )
    assert response.status_code == 200


@pytest.mark.django_db
def test_wjs_author_sends_corrections(stage_proofing_article, client):
    assignment = TypesettingAssignment.objects.get(round__article=stage_proofing_article)
    client.force_login(stage_proofing_article.correspondence_author)
    response = client.get(
        f"/{stage_proofing_article.journal.code}/plugins/wjs-review-articles/send_corrections/{assignment.pk}/"
    )
    assert response.status_code == 302


@pytest.mark.django_db
def test_wjs_toggle_publishable(assigned_to_typesetter_article_with_files_to_typeset, typesetter, client):
    client.force_login(typesetter)
    response = client.get(
        f"/plugins/wjs-review-articles/paper_publishable/"
        f"{assigned_to_typesetter_article_with_files_to_typeset.articleworkflow.pk}/"
    )
    assert response.status_code == 200


@pytest.mark.django_db
@patch("plugins.wjs_review.views__production.TypesetterTestsGalleyGeneration")
def test_wjs_typesetter_galley_generation(
    galley_generation, assigned_to_typesetter_article_with_files_to_typeset, typesetter, client
):
    client.force_login(typesetter)
    assignment = TypesettingAssignment.objects.get(round__article=assigned_to_typesetter_article_with_files_to_typeset)
    response = client.get(
        f"/{assigned_to_typesetter_article_with_files_to_typeset.journal.code}/"
        f"plugins/wjs-review-articles/galley_generation/{assignment.pk}/"
    )
    assert response.status_code == 200


@pytest.mark.django_db
def test_wjs_edit_permission(assigned_article, typesetter, eo_user, client):
    client.force_login(eo_user)
    response = client.get(
        f"/{assigned_article.journal.code}/plugins/wjs-review-articles/"
        f"article/{assigned_article.pk}/permissions/{typesetter.pk}/"
    )
    assert response.status_code == 200


@pytest.mark.django_db
def test_wjs_send_back_to_typ(assigned_to_typesetter_article_with_files_to_typeset, eo_user, client):
    client.force_login(eo_user)
    response = client.get(
        f"/{assigned_to_typesetter_article_with_files_to_typeset.journal.code}/plugins/wjs-review-articles/"
        f"send_back_to_typesetter/{assigned_to_typesetter_article_with_files_to_typeset.articleworkflow.pk}/"
    )
    assert response.status_code == 200


@pytest.mark.django_db
def test_wjs_typ_take_in_charge(assigned_article, typesetter, client):
    client.force_login(typesetter)
    response = client.get(
        f"/{assigned_article.journal.code}/plugins/wjs-review-articles/take_in_charge/"
        f"{assigned_article.articleworkflow.pk}/"
    )
    assert response.status_code == 302


@pytest.mark.django_db
def test_wjs_review_publish(rfp_article, eo_user, client):
    client.force_login(eo_user)
    response = client.get(
        f"/{rfp_article.journal.code}/plugins/wjs-review-articles/publish/{rfp_article.articleworkflow.pk}/",
    )
    assert response.status_code == 302
