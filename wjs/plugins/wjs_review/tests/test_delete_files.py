"""Accademic tests."""

from io import BytesIO
from pathlib import Path

import pytest
from core.files import save_file_to_article
from core.models import Account
from core.models import File as JanewayFile
from django.core.files import File as DjangoFile
from submission.models import Article


@pytest.mark.skipif("not config.getoption('--run-academic')")
@pytest.mark.django_db
def test_delete_file(article: Article):
    """
    Test that Object.delete() is different from Object.objects.all().delete().

    Yes, this is clearly documented:

        The delete() method does a bulk delete and does not call any delete() methods on your models. It does, however,
        emit the pre_delete and post_delete signals for all deleted objects (including cascaded deletions).
        https://docs.djangoproject.com/en/4.2/ref/models/querysets/#delete

    Yes, I knew and forgot! 😦
    """
    owner = Account.objects.all().first()
    janeway_file = save_file_to_article(
        file_to_handle=DjangoFile(file=BytesIO(b"ciaone"), name="ciaone"),
        article=article,
        owner=owner,
    )

    # janeway_file is a proper object, with it's id, and a real path on the filesystem
    assert janeway_file.id > 0
    file_path = janeway_file.self_article_path()
    assert Path(file_path).exists()

    # when I delete the object, File.delete() calls File.unlink_file() and the file on the filesystem is deleted; 👍
    janeway_file.delete()

    assert not Path(file_path).exists()
    #      ⇧⇧⇧

    # Now let's try the same, but applying "delete()" on a queryset:

    janeway_file_bis = save_file_to_article(
        file_to_handle=DjangoFile(file=BytesIO(b"ciaone"), name="ciaone"),
        article=article,
        owner=owner,
    )
    assert janeway_file_bis.id > 0
    file_path_bis = janeway_file_bis.self_article_path()
    assert Path(file_path_bis).exists()

    JanewayFile.objects.filter(id=janeway_file_bis.id).delete()

    # the record in the DB is gone...
    assert not JanewayFile.objects.filter(id=janeway_file_bis.id).exists()  # 👍

    # but the file on the filesystem is still there 😠
    assert Path(file_path_bis).exists()
