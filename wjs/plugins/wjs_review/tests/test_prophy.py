import pytest
from django.http import HttpRequest
from events import logic as events_logic

from ..logic import HandleDecision
from ..models import (
    ArticleWorkflow,
    ProphyAccount,
    ProphyCandidate,
    WjsEditorAssignment,
)
from ..plugin_settings import STAGE
from ..prophy import Prophy


@pytest.mark.parametrize(
    "action,decision",
    (
        ("reject", ArticleWorkflow.Decisions.REJECT),
        ("not_suitable", ArticleWorkflow.Decisions.NOT_SUITABLE),
        ("publish", None),
    ),
)
@pytest.mark.django_db
def test_two_articles_one_declined(
    assigned_article,
    article_factory,
    fake_request: HttpRequest,
    action: str,
    decision: str,
):
    # test description:
    #
    # - 2 articles: assigned_article second_article
    #     both have 2 Prophy candidates
    #     one candidate is common to both articles
    #     both are in review
    # - 1 article is published / rejected / deemed not-suitable
    # - assert that all and only the candidates of the "finished" article have been deleted

    jcom = fake_request.journal

    editor_user = WjsEditorAssignment.objects.get_current(assigned_article).editor

    second_article = article_factory(
        journal=jcom,
        title="second article with prophy candidates",
    )

    # prophy json
    # assigned article author ids: 402953,  1635441
    json_response_text_assigned_article = f"""{{"authors_groups_settings":null,
    "candidates":[
        {{"affiliation":"Fluminense Federal University, Brazil, Niter\u00f3i",
        "articlesCount":222,
        "author_id":402953,"authors_groups":[],"citationsCount":1058,
        "email":"rrr@a.it",
        "first_name":"Refaella","hIndex":16,"last_name":"Reviewer","middle_name":"A.",
        "name":"Refaella A. Reviewer","orcid":"0000-0001-0001-0001",
        "score":1.2441566316924686,"suffix":null,"url":
        "https://www.pro.sci/author/402953/RR/"}},
        {{"affiliation":"University of Zurich, Switzerland, Zurich",
        "articlesCount":200,"author_id":1635441,
        "authors_groups":[],"citationsCount":3971,
        "email":"rr2@a.it",
        "first_name":"Refaele","hIndex":30,"last_name":"Reviewer","middle_name":"B.",
        "name":"Refaele B. Reviewer",
        "orcid":"0000-0001-1111-1111",
        "score":1.147765597026575,"suffix":null,
        "url":"https://www.pro.sci/author/1635441/MS/"}}],
        "debug_info":{{"authors_info":{{"authors_count":1,"emails_count":0,"orcids_count":0}},
        "extracted_concepts":5,"parsed_references":0,"parsed_text_len":0,"source_file":"Not provided"}},
        "manuscript_id":94971,
        "origin_id":"{assigned_article.id}"}}"""

    # prophy json
    # second article author ids: 402953, 4992186
    json_response_text_second_article = f"""{{"authors_groups_settings":null,
    "candidates":[
        {{"affiliation":"Fluminense Federal University, Brazil, Niter\u00f3i",
        "articlesCount":222,
        "author_id":402953,"authors_groups":[],"citationsCount":1058,
        "email":"rrr@a.it",
        "first_name":"Refaella","hIndex":16,"last_name":"Reviewer","middle_name":"A.",
        "name":"Refaella A. Reviewer","orcid":"0000-0001-0001-0001",
        "score":1.2441566316924686,"suffix":null,"url":
        "https://www.pro.sci/author/402953/RR/"}},
        {{"affiliation":"University of Warwick, United Kingdom, Coventry","articlesCount":111,
        "author_id":4992186,"authors_groups":[],"citationsCount":1490,
        "email":"ppp@a.it",
        "first_name":"Peter","hIndex":21,"last_name":"Prophycand","middle_name":"P.",
        "name":
        "Peter P. Prophycand","orcid":"0000-0000-0000-0000",
        "score":1.069413466861101,"suffix":null,
        "url":"https://www.pro.sci/author/4992186/PP/"}}],
        "debug_info":{{"authors_info":{{"authors_count":1,"emails_count":0,"orcids_count":0}},
        "extracted_concepts":5,"parsed_references":0,"parsed_text_len":0,"source_file":"Not provided"}},
        "manuscript_id":94972,
        "origin_id":"{second_article.id}"}}"""

    # add prophy candidates to articles

    p_assigned_article = Prophy(assigned_article)
    p_assigned_article.store_json(json_response_text_assigned_article)

    p_second_article = Prophy(second_article)
    p_second_article.store_json(json_response_text_second_article)

    # prophy account assigned_article author ids: 402953 (common), 1635441
    # prophy account second_article   author ids: 402953 (common), 4992186
    assert sorted(ProphyAccount.objects.all().values_list("author_id", flat=True)) == sorted(
        [402953, 1635441, 4992186],
    )

    # candidates assigned_article
    num_candidates1 = ProphyCandidate.objects.filter(article=assigned_article.id).count()
    assert num_candidates1 == 2

    # candidates second_article
    num_candidates_second_article = ProphyCandidate.objects.filter(article=second_article.id).count()
    assert num_candidates_second_article == 2

    assert assigned_article.articleworkflow.state == "EditorSelected"

    if action in ("reject", "not_suitable"):
        fake_request.user = editor_user
        form_data = {
            "decision": decision,
            "decision_editor_report": "random message",
            "decision_internal_note": "random internal message",
            "withdraw_notice": "notice",
        }
        handle = HandleDecision(
            workflow=assigned_article.articleworkflow,
            form_data=form_data,
            user=editor_user,
            request=fake_request,
        )
        handle.run()
    elif action == "publish":
        # TODO: replace with publish action when defined
        # used event instead
        kwargs = {
            "article": assigned_article,
            "request": fake_request,
        }
        assigned_article.stage = STAGE
        assigned_article.articleworkflow.state = ArticleWorkflow.ReviewStates.PUBLISHED
        events_logic.Events.raise_event(
            events_logic.Events.ON_ARTICLE_PUBLISHED,
            task_object=assigned_article,
            **kwargs,
        )

    # candidates assigned_article after reject, not_suitable or publish
    num_candidates_assigned_article = ProphyCandidate.objects.filter(article=assigned_article.id).count()
    assert num_candidates_assigned_article == 0

    # candidates second_article after reject, not_suitable or publish
    num_candidates_second_article = ProphyCandidate.objects.filter(article=second_article.id).count()
    assert num_candidates_second_article == 2

    # prophy account deleted 1635441 (account related to assigned article only))
    assert not ProphyAccount.objects.filter(author_id=1635441).exists()

    # prophy account not deleted 402953 (both articles)
    # prophy account not deleted 4992186 (second article only)
    assert ProphyAccount.objects.filter(author_id__in=[402953, 4992186]).count() == 2
