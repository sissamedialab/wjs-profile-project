import pytest
from comms.models import NewsItem

from wjs.jcom_profile.templatetags.wjs_tags import news_part


@pytest.mark.parametrize(
    "text,body,abstract",
    (
        ("<p>abstract</p><hr><p>body</p>", "<p>body</p>", "<p>abstract</p>"),
        ("<p>abstract</p><p>body</p>", "<p>abstract</p><p>body</p>", ""),
    ),
)
def test_news_part(text, body, abstract):
    ni = NewsItem(title="some", body=text)

    assert news_part(ni, "abstract") == abstract
    assert news_part(ni, "body") == body
