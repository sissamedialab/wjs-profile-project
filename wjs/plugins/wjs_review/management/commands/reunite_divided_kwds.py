"""
Fix broken kwds on an article.

See wjs-help#138 and related issues.

Articles with "suspicious" kwds can be found with:

Article.objects
  .filter(journal__code="JCOM")
  .annotate(kc=Count("keywords"))
  .filter(kc__gt=0)
  .filter(keywords__journal__isnull=True)
  .values("pk")

Some suspicious kwds can be checked by:

Article.objects
  .filter(keywords__word__regex="^[a-z]")
  .values_list("keywords__word", "id")

Dirty data:
- some non-English kwd in JCOMAL
  1265, 1270, 1271, ...

  I expect that kwds always have an English value,
  and translations are used only when the language context changes.

  ATM, kwds exist that only have Spanish translation (i.e. no value in word_en field);
  these are clearly wrong.


- probably manually edited list, with missing pieces of "divided" kwds
  1432, 3606
  IIC, two very similar kwds:
  - Professionalism, professional development and training in science communication
  - Professionalism, professional development and teaching in science communication
                                                  ⬆ diff ⬆
  have been divided, producing:
  - Professionalism
  - professional development and training in science communication
  - Professionalism
  - professional development and teaching in science communication

  but "Professionalism" appeared twice and ended-up removed


- there is a bug in reunite_divided_kwds() so that for a list such as the following,
  the function must be run twice in order to fix both the "splits" (JCOM_1478, JCOM_1566,...)
  -  [3] 'Science and media' (JCOM, JCOMAL)
  - [76] 'Professionalism' ()
  - [77] 'professional development and training in science communication' ()
  - [87] 'Diversity' ()
  - [88] 'equity' ()
  - [89] 'inclusion and accessibility in science communication' ()


"""

from django.core.management.base import BaseCommand, CommandError
from plugins.wjs_review.logic__production import reunite_divided_kwds
from submission.models import Article, Keyword, KeywordArticle


class Command(BaseCommand):
    help = "Reunite keywords that were split across multiple entries."  # noqa

    def add_arguments(self, parser):
        parser.add_argument("article", type=int, help="PK of the Article to process.")
        # TODO: add a parameter to force-yes the kwds substitution

    def handle(self, *args, **options):
        try:
            article = Article.objects.get(pk=options["article"])
        except Article.DoesNotExist as err:
            raise CommandError(f"Article {options['article']} not found.") from err

        article_kwds = article.keywords.all()

        for kwd in article_kwds.exclude(journal__isnull=True):
            kwd_journals = list(kwd.journal_set.all().values_list("code", flat=True))
            if article.journal.code not in kwd_journals:
                self.stderr.write(
                    self.style.ERROR(
                        f"Keyword {kwd.pk} ({kwd.word!r}) belongs to "
                        f" {kwd_journals} but article belongs to {article.journal}.",
                    ),
                )

        # TODO: parametrize the following operation and run reunite_divided_kwds() on
        # - all article kwds (when arguemnt "--fast" has the default value of "false")
        # - only on kwds without journal if argument "--fast" is true
        kwds_without_journal = article_kwds.filter(journal__isnull=True)
        good_ids, bad_ids = reunite_divided_kwds(kwds_without_journal)

        self.stdout.write("\nCurrent article keywords:")
        for kwd in article_kwds:
            kwd_journals = ", ".join(list(kwd.journal_set.all().values_list("code", flat=True)))
            self.stdout.write(f"  [{kwd.pk}] {kwd.word!r} ({kwd_journals})")

        if not good_ids and not bad_ids:
            self.stdout.write("\nNothing to do.")
            return

        good_kwds = list(Keyword.objects.filter(id__in=good_ids))
        bad_kwds = list(Keyword.objects.filter(id__in=bad_ids))
        self.stdout.write("\nBad kwds:")
        for kwd in bad_kwds:
            self.stdout.write(f"  [{kwd.pk}] {kwd.word!r}")
        self.stdout.write("\nTo be replaced by:")
        for kwd in good_kwds:
            self.stdout.write(f"  [{kwd.pk}] {kwd.word!r}")

        # Prepare the new kwds list for the article
        #
        # start with the current article kwds in the current order
        #
        # for each good kwd,
        #   scan the current list
        #   if a bad kwd starts with the same string as a good one,
        #   insert the good kwd
        #   break out of the loop and go to the next good kwd
        #
        # at the end, remove all the kwds with the "bad" ids

        current_ordered = list(article_kwds)  # ordered by KeywordArticle.order
        good_kwds_to_check = good_kwds.copy()
        new_list = []
        for kwd in current_ordered:
            if kwd.id not in bad_ids:
                new_list.append(kwd)
                continue

            for good_kwd in good_kwds_to_check:
                if good_kwd.word.startswith(kwd.word):
                    new_list.append(good_kwd)
                    good_kwds_to_check.remove(good_kwd)
                    break

        if len(good_kwds_to_check) > 0:
            for kwd in good_kwds_to_check:
                self.stderr.write(
                    self.style.ERROR(
                        f"Cannot place {kwd.pk} ({kwd.word!r}) among {current_ordered}.",
                    ),
                )

        final_kwds = [kwd for kwd in new_list if kwd.id not in bad_ids]

        self.stdout.write("\nSituation after replacement:")
        for kwd in final_kwds:
            self.stdout.write(f"  [{kwd.pk}] {kwd.word!r}")

        confirm = input("\nProceed with replacement? [y/N] ")
        if confirm.strip().lower() != "y":
            self.stdout.write("Aborted.")
            return

        article.keywords.clear()
        for order, kwd in enumerate(final_kwds):
            KeywordArticle.objects.create(article=article, keyword=kwd, order=order)
        self.stdout.write(self.style.SUCCESS("done"))

        for kwd in bad_kwds:
            if not kwd.article_set.exists() and not kwd.journal_set.exists():
                confirm = input(
                    f'\nNo article linked to "[{kwd.pk}] {kwd.word}", '
                    "and kwd not linked to any journal. Delete? [y/N] ",
                )
                if confirm.strip().lower() != "y":
                    self.stdout.write("Aborted.")
                    return
                kwd.delete()
                continue

            if kwd.journal_set.exists():
                self.stdout.write(
                    self.style.WARNING(
                        f'\nKwd "[{kwd.pk}] {kwd.word}" linked '
                        "to {kwd.journal_set.all().values_list('journal__code')} ",
                    ),
                )
