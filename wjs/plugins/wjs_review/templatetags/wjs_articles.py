from django import template

register = template.Library()


@register.simple_tag()
def latest_completed_revision(article):
    return article.revisionrequest_set.filter(date_completed__isnull=False).order_by("-date_requested").first()
