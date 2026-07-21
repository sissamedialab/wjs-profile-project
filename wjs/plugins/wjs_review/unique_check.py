from django.db.models import Q
from journal.models import Journal
from plugins.wjs_submission.unique_check import get_articles_matching_signature
from submission.models import STAGE_UNSUBMITTED

from .models import ArticleWorkflow, WjsSection


def check_article_uniqueness_by_submission_status_and_section(
    response_content: dict, journal: Journal, **kwargs
) -> bool:
    """
    Check that article is unique by submission status and section.

    Checks:
    Conditions within the same journal -
          article with same arxiv id (ignore version number) AND/OR same title and same abstract
    except
          articles with different section (article type) AND status unsubmitted (ie: they have not completed step 8) OR
                withdrawn / Not suitable OR
          articles with same section AND status unsubmitted (ie: they have not completed step 8) OR withdrawn OR
          articles which have been created by arxiv id check (current_step=0)

    :param response_content: A dictionary containing 'arxiv_id', 'title',
        and 'abstract' keys to filter candidate articles.
    :type response_content: dict
    :param journal: The Journal instance to filter candidates by.
    :type journal: Journal
    :return: A boolean
    :rtype: bool
    """
    filtered_articles = get_articles_matching_signature(response_content, journal)
    sections = WjsSection.objects.filter(journal=journal)
    filtered_articles = filtered_articles.exclude(
        # articles with different section (article type) AND status unsubmitted / withdrawn / Not suitable
        (
            (~Q(section__in=sections) & Q(stage=STAGE_UNSUBMITTED))
            | Q(
                articleworkflow__state__in=[
                    ArticleWorkflow.ReviewStates.WITHDRAWN,
                    ArticleWorkflow.ReviewStates.NOT_SUITABLE,
                ]
            )
        )
        |
        # articles with same section AND status unsubmitted / withdrawn OR
        (
            (Q(section__in=sections) & Q(stage=STAGE_UNSUBMITTED))
            | Q(articleworkflow__state=ArticleWorkflow.ReviewStates.WITHDRAWN)
        )
        |
        # If current step is 0, it's an article which just have been created via ArxivMicroservice
        Q(current_step=0)
    )
    return not filtered_articles.exists()


def check_article_uniqueness_by_submission_status_and_section_in_all_journals(response_content: dict, **kwargs):
    """
    Check that article is unique by submission status and section in all journals.

    Checks:
    Conditions among different journals -
        article with same arxiv id (ignore version number) AND/OR same title and same abstract
    except
        articles in unsubmitted (ie: they have not completed step 8) / rejected / not suitable / withdrawn state

    :param response_content: A dictionary containing 'arxiv_id', 'title',
        and 'abstract' keys to filter candidate articles.
    :type response_content: dict
    :return: A boolean
    :rtype: bool
    """
    # filtering without journal
    filtered_articles = get_articles_matching_signature(response_content)
    filtered_articles = filtered_articles.exclude(
        Q(stage=STAGE_UNSUBMITTED)
        | Q(
            articleworkflow__state__in=[
                ArticleWorkflow.ReviewStates.REJECTED,
                ArticleWorkflow.ReviewStates.NOT_SUITABLE,
                ArticleWorkflow.ReviewStates.WITHDRAWN,
            ]
        )
    )
    return not filtered_articles.exists()


def check_article_uniqueness_by_submission_status_and_section_in_all_journals_combined(
    response_content: dict, journal: Journal, **kwargs
):
    """
    Check that article is unique by submission status and section in all journals.

    Checks:
    Conditions within the same journal -
          article with same arxiv id (ignore version number) AND/OR same title and same abstract
    except
          articles with different section (article type) AND status unsubmitted (ie: they have not completed step 8) OR
                withdrawn / Not suitable OR
          articles with same section AND status unsubmitted (ie: they have not completed step 8) OR withdrawn OR
          articles which have been created by arxiv id check (current_step=0)

    AND
    Conditions among different journals -
        article with same arxiv id (ignore version number) AND/OR same title and same abstract
    except
        articles in unsubmitted (ie: they have not completed step 8) / rejected / not suitable / withdrawn state


    :param response_content: A dictionary containing 'arxiv_id', 'title',
        and 'abstract' keys to filter candidate articles.
    :type response_content: dict
    :param journal: The Journal instance to filter candidates by.
    :type journal: Journal
    :return: A boolean
    :rtype: bool
    """
    return check_article_uniqueness_by_submission_status_and_section(
        response_content, journal
    ) and check_article_uniqueness_by_submission_status_and_section_in_all_journals(response_content)
