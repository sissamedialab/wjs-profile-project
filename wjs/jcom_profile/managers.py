from django.db import models
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone


class StaffWorkloadParametersQuerySet(models.QuerySet):

    def annotate_vacancy(self):
        """
        Annotate vacancies based on availability criteria.

        This method annotates queryset entries with an additional field `no_vacancy`
        indicating whether a vacancy is available or not, based on the specified
        conditions related to vacancy start and end dates. The `no_vacancy` field is
        a boolean derived using the `Exists` clause applied on the queryset.

        :param self: The queryset instance to be annotated
        :return: A queryset annotated with the `no_vacancy` field
        :rtype: QuerySet
        """
        start = Q(
            # Vacancy start not set OR
            #   Vacancy start set in the future OR
            #     Vacancy start set in the past with vacancy end also in the past
            Q(vacancy_start__isnull=True)
            | Q(vacancy_start__gt=timezone.now())
            | Q(Q(vacancy_end__isnull=False) & Q(vacancy_end__lt=timezone.now()))
        )
        end = Q(
            # Vacancy end not set OR
            #   Vacancy end set in the pats OR
            #     Vacancy end set in the future with vacancy start also in the future
            Q(vacancy_end__isnull=True)
            | Q(vacancy_end__lt=timezone.now())
            | Q(Q(vacancy_start__isnull=False) & Q(vacancy_start__gt=timezone.now()))
        )
        no_vacancy_condition = Q(start & end)
        # As a subquery we must filter by the current user user_id to ensure the check above is done per-record
        vacancy_subquery = self.filter(no_vacancy_condition, enabled=True, user=OuterRef("user_id"))
        return self.annotate(no_vacancy=Exists(vacancy_subquery))
