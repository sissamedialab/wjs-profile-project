"""Tests for the uniqueness of (parameters, keyword) on StaffKeyword (wjs/specs#3436)."""

import pytest
from django.db import IntegrityError, transaction

from wjs.jcom_profile.models import StaffKeyword, StaffWorkloadParameters


@pytest.mark.django_db
def test_duplicate_staff_keyword_is_rejected(section_editor, journal, keywords):
    """The same keyword cannot be linked twice to the same editor parameters."""
    parameters = StaffWorkloadParameters.objects.get_or_create(user=section_editor, journal=journal)[0]
    keyword = keywords[0]
    StaffKeyword.objects.create(parameters=parameters, keyword=keyword, weight=3)

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            StaffKeyword.objects.create(parameters=parameters, keyword=keyword, weight=5)

    assert StaffKeyword.objects.filter(parameters=parameters, keyword=keyword).count() == 1, "duplicate was stored"


@pytest.mark.django_db
def test_same_keyword_for_different_parameters_is_allowed(section_editor, editor, journal, keywords):
    """Two different editors can share a keyword."""
    keyword = keywords[0]
    for user in (section_editor, editor):
        parameters = StaffWorkloadParameters.objects.get_or_create(user=user, journal=journal)[0]
        StaffKeyword.objects.create(parameters=parameters, keyword=keyword)

    assert StaffKeyword.objects.filter(keyword=keyword).count() == 2, "both editors should have the keyword"


@pytest.mark.django_db
def test_different_keywords_for_same_parameters_are_allowed(section_editor, journal, keywords):
    """One editor can have several keywords."""
    parameters = StaffWorkloadParameters.objects.get_or_create(user=section_editor, journal=journal)[0]
    for keyword in keywords[:2]:
        StaffKeyword.objects.create(parameters=parameters, keyword=keyword)

    assert StaffKeyword.objects.filter(parameters=parameters).count() == 2, "both keywords should be stored"
