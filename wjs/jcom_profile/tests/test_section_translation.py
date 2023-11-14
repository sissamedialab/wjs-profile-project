import pytest
from django.utils import translation
from submission import models


@pytest.mark.django_db
def test_edit_section_translations(journal):
    """Ensures Section object honor translations."""
    with translation.override("en"):
        section = models.Section.objects.create(
            journal=journal,
            name="section_en",
        )
    assert section.name == "section_en"
    with translation.override("en"):
        assert section.name == "section_en"
    with translation.override("de"):
        assert section.name == "section_en"
        section.name = "section_de"
        section.save()
    section.refresh_from_db()
    with translation.override("en"):
        assert section.name == "section_en"
    with translation.override("de"):
        assert section.name == "section_de"
