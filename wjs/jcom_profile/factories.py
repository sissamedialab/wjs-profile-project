"""Utility factories.

Used in management commands and tests.
"""

import factory
from comms.models import NewsItem
from core.models import Account
from django.utils import timezone
from faker.providers import lorem
from journal.models import Issue, IssueType, Journal
from submission.models import Article, Keyword, Section

from wjs.jcom_profile.models import JCOMProfile, Newsletter, Recipient

factory.Faker.add_provider(lorem)


# Not using model-baker because I could find a way to define a fake_factory field
# that depend on another one (since in J. username == email). E.g.:
# Recipe("core.Account", ...  email=fake_factory.email(),  username=email, ⇒ ERROR!


class UserFactory(factory.django.DjangoModelFactory):
    """User factory. OBSOLETE! To be replaced by JCOMProfileFactory and AccountFactory."""

    class Meta:
        model = JCOMProfile  # ← is this correct? maybe core.Account?

    first_name = factory.Faker("first_name")
    last_name = factory.Faker("last_name")
    email = factory.Faker("email")
    username = email
    is_admin = False
    is_active = True


class AccountFactory(factory.django.DjangoModelFactory):
    """Account with JCOM profile."""

    class Meta:
        model = Account

    first_name = factory.Faker("first_name")
    last_name = factory.Faker("last_name")
    email = factory.Faker("email")
    username = email
    is_admin = False
    is_active = True

    # DO NOT `jcomprofile = factory.SubFactory(JCOMProfileFactory)`
    # you'd hit wjs/specs#192 (probably...)


class JCOMProfileFactory(factory.django.DjangoModelFactory):
    """JCOM profile factory."""

    class Meta:
        model = JCOMProfile

    gdpr_checkbox = True
    profession = 4
    invitation_token = "x"

    # DO NOT `janeway_account = factory.SubFactory(AccountFactory)`
    # I think it's "circular" and would try to create the Account again...
    # DETAIL:  Key (janeway_account_id)=(1) already exists.

    first_name = factory.Faker("first_name")
    last_name = factory.Faker("last_name")
    email = factory.Faker("email")
    username = email
    is_admin = False
    is_active = True


class JournalFactory(factory.django.DjangoModelFactory):
    """Journal factory."""

    class Meta:
        model = Journal


class ArticleFactory(factory.django.DjangoModelFactory):
    """Article factory."""

    class Meta:
        model = Article

    title = factory.Faker("sentence", nb_words=7)
    abstract = factory.Faker("paragraph", nb_sentences=5)
    language = "eng"
    # Link this article to a journal
    # Problems:
    # + these give error when used by pytest (not marked for db access):
    #   - journal = factory.Iterator((Journal.objects.first(),))
    #   - journal = Journal.objects.first()
    # + the following breaks with StopIteration if used with pytest_factoryboy.register
    #   - journal = factory.Iterator(Journal.objects.all())
    journal = factory.SubFactory(JournalFactory)
    # Link to article type / section
    # + link to a random one doen't work when using this factory with pytest_factoryboy
    #   - section = factory.Iterator(submission_models.Section.objects.all())
    # + link with SubFactory also fails during teardown (credo...)
    #   - section = factory.SubFactory(SectionFactory)
    #
    # TODO: try me!
    # ... journal = factory.LazyAttribute(lambda x: factory.Iterator(Journal.objects.all()))
    # oppure
    # @factory.post_generation
    # def set1(foo, create, value, **kwargs):
    #     ... foo.value = 1

    # TODO: use dall.e (https://labs.openai.com) to fill `thumbnail_image_file`
    section = factory.LazyAttribute(lambda self: Section.objects.filter(journal=self.journal).order_by("?").first())


def yesterday():
    """Return a datetime obj representing yesterday."""
    yesterday = timezone.now() - timezone.timedelta(1)
    return yesterday


def ototoi():
    """Return a datetime obj representing the day before yesterday.

    This is useful when testing Newsletter wrt to last_sent, as it
    allows for easy creation of a date between the newsletter's
    last_sent and now() (i.e. newsletter.last_sent +
    timedelta(days=1)).

    """
    # 一昨日 (ototoi or issakujitsu) means the day before yesterday in Japanese
    ototoi = timezone.now() - timezone.timedelta(2)
    return ototoi


class SpecialIssueFactory(factory.django.DjangoModelFactory):
    """Special issues."""

    class Meta:
        model = Issue

    issue_title = factory.Faker("sentence", nb_words=5)
    short_name = factory.Faker("slug")
    description = factory.Faker("paragraph", nb_sentences=5)
    date_open = factory.LazyFunction(yesterday)
    issue_type = factory.LazyAttribute(lambda x: IssueType.objects.get_or_create(code="collection")[0])
    # wrong:
    # ... = factory.LazyAttribute(lambda x: factory.Iterator(Journal.objects.all()))
    # gives:
    # ValueError: Cannot assign "<factory.declarations.Iterator object at ...>":
    # "SpecialIssue.journal" must be a "Journal" instance.
    journal = factory.LazyAttribute(lambda x: Journal.objects.first())

    @factory.post_generation
    def allowed_sections(self, create, extracted, **kwargs):
        if not create or not extracted:
            # Simple build, or nothing to add, do nothing.
            return

        if not extracted:
            extracted = Section.objects.filter(journal=Journal.objects.first()).order_by("?")[:2]

        # Add the iterable of groups using bulk addition
        self.allowed_sections.set(extracted)


class IssueFactory(factory.django.DjangoModelFactory):
    """Standard Issue."""

    class Meta:
        model = Issue

    journal = factory.SubFactory(JournalFactory)
    volume = 1
    issue = "01"
    # JCOM issue don't usually have a title
    issue_title = ""
    issue_type = factory.LazyAttribute(lambda x: IssueType.objects.first())


class SectionFactory(factory.django.DjangoModelFactory):
    """Section factory."""

    class Meta:
        model = Section

    journal = factory.LazyAttribute(lambda x: Journal.objects.first())
    name = factory.Faker("sentence", nb_words=1)
    public_submissions = False


class KeywordFactory(factory.django.DjangoModelFactory):
    """Keyword factory."""

    class Meta:
        model = Keyword

    word = factory.Faker("sentence", nb_words=4)


class RecipientFactory(factory.django.DjangoModelFactory):
    """Recipient factory."""

    class Meta:
        model = Recipient

    journal = factory.LazyAttribute(lambda x: Journal.objects.first())
    news = factory.Faker("pybool")


class NewsItemFactory(factory.django.DjangoModelFactory):
    """NewsItem factory."""

    class Meta:
        model = NewsItem

    title = factory.Faker("sentence", nb_words=2)


class NewsletterFactory(factory.django.DjangoModelFactory):
    """Newsletter factory."""

    class Meta:
        model = Newsletter

    journal = factory.LazyAttribute(lambda x: Journal.objects.first())
    last_sent = factory.LazyFunction(ototoi)
