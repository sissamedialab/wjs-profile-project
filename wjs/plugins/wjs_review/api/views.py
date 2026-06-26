from pathlib import Path

from core import files
from core import models as core_models
from django.core.exceptions import ObjectDoesNotExist
from django.core.files.base import ContentFile
from django.db.models import Count
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .const import TYPE_TO_MIME
from .mixins import LoggedRequestMixin, PublishedArticleAccessMixin
from .serializers import GalleyUploadSerializer


class ArticleZipDownloadView(LoggedRequestMixin, PublishedArticleAccessMixin, APIView):
    def get(self, request, pk: int):
        """
        Download a zip file containing all galleys for the given article.
        """
        article = self.get_article(request, pk)

        try:
            article_workflow = article.articleworkflow
        except ObjectDoesNotExist:
            return Response(
                {"error": {"code": "NOT_FOUND", "message": "Requested resource was not found."}},
                status=status.HTTP_404_NOT_FOUND,
            )

        core_file = getattr(article_workflow, "publication_galleys_source_file", None)
        if not core_file:
            return Response(
                {"error": {"code": "NOT_FOUND", "message": "Requested resource was not found."}},
                status=status.HTTP_404_NOT_FOUND,
            )
        galleys_filename = core_file.uuid_filename
        galleys_path = Path(article.folder_path()) / galleys_filename
        if not galleys_path.exists():
            return Response(
                {"error": {"code": "NOT_FOUND", "message": "Requested resource was not found."}},
                status=status.HTTP_404_NOT_FOUND,
            )
        return files.serve_file_to_browser(file_path=galleys_path, file_to_serve=core_file, public=True)


class ArticleGalleyListView(LoggedRequestMixin, PublishedArticleAccessMixin, APIView):
    def get(self, request, pk: int):
        """
        List all galleys for the given article.
        """
        article = self.get_article(request, pk)

        galleys = article.galley_set.all().order_by("type")

        type_counts = {
            row["type"]: row["cnt"] for row in (article.galley_set.values("type").annotate(cnt=Count("id")))
        }

        items = []
        for galley in galleys:
            core_file = getattr(galley, "file", None)
            if not core_file:
                continue

            file_type = galley.type
            sequence = galley.sequence

            if file_type == "image":
                content_type = "image/*"
            else:
                content_type = sorted(TYPE_TO_MIME.get(file_type, {"application/octet-stream"}))[0]

            if type_counts.get(file_type, 0) > 1:
                download_url = (
                    f"/plugins/wjs-review-articles/api/v1/article/{article.pk}/galley/{file_type}/{sequence}/"
                )
            else:
                download_url = f"/plugins/wjs-review-articles/api/v1/article/{article.pk}/galley/{file_type}/"

            items.append(
                {
                    "type": file_type,
                    "sequence": sequence,
                    "filename": core_file.original_filename,
                    "contentType": content_type,
                    "download_url": download_url,
                }
            )

        return Response({"article_id": article.pk, "items": items}, status=status.HTTP_200_OK)


class ArticleGalleyView(LoggedRequestMixin, PublishedArticleAccessMixin, APIView):
    def _validate_type(self, type_: str):
        if type_ not in [key for key, _ in core_models.galley_type_choices()]:
            return False
        return True

    def get(self, request, pk: int, file_type: str, sequence: int = None):
        """
        Download a galley file for the given article.
        """
        if not self._validate_type(file_type) is not None:
            return Response(
                {"error": {"code": "TYPE_NOT_FOUND", "message": "Invalid parameters."}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        article = self.get_article(request, pk)

        qs = core_models.Galley.objects.filter(article=article, type=file_type)
        if sequence is not None:
            qs = qs.filter(sequence=sequence)
        count = qs.count()
        if count == 0:
            return Response(
                {"error": {"code": "NOT_FOUND", "message": "Requested resource was not found."}},
                status=status.HTTP_404_NOT_FOUND,
            )
        if count > 1:
            return Response(
                {
                    "error": {
                        "code": "DATA_INTEGRITY_CONFLICT",
                        "message": "Multiple galleys found for the same article/type/sequence",
                    }
                },
                status=status.HTTP_409_CONFLICT,
            )

        galley = qs.first()
        core_file = getattr(galley, "file", None)
        if not core_file:
            return Response(
                {"error": {"code": "NOT_FOUND", "message": "Requested resource was not found."}},
                status=status.HTTP_404_NOT_FOUND,
            )

        file_path = core_file.self_article_path()
        return files.serve_file_to_browser(file_path=file_path, file_to_serve=core_file, public=True)

    def post(self, request, pk: int, file_type: str, sequence: int = None):
        """
        Upload or replace a galley file for the given article.
        """
        if not self._validate_type(file_type) is not None:
            return Response(
                {"error": {"code": "TYPE_NOT_FOUND", "message": "Invalid parameters."}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        article = self.get_article(request, pk)

        qs = core_models.Galley.objects.filter(article=article, type=file_type)
        if sequence is not None:
            qs = qs.filter(sequence=sequence)
        count = qs.count()
        if count == 0:
            return Response(
                {"error": {"code": "NOT_FOUND", "message": "Requested resource was not found."}},
                status=status.HTTP_404_NOT_FOUND,
            )
        if count > 1:
            return Response(
                {
                    "error": {
                        "code": "DATA_INTEGRITY_CONFLICT",
                        "message": "Multiple galleys found for the same article/type/sequence",
                    }
                },
                status=status.HTTP_409_CONFLICT,
            )

        galley = qs.first()
        core_file = getattr(galley, "file", None)
        if not core_file:
            return Response(
                {"error": {"code": "NOT_FOUND", "message": "Requested resource was not found."}},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = GalleyUploadSerializer(
            data={},
            context={"request": request},
            galley_type=file_type,
        )
        serializer.is_valid(raise_exception=True)

        uploaded_file = ContentFile(serializer.validated_data["data"], name=core_file.original_filename)

        files.overwrite_file(
            uploaded_file=uploaded_file,
            file_to_replace=core_file,
            path_parts=["articles", str(article.pk)],
        )

        resp = Response(status=status.HTTP_201_CREATED)
        resp["Location"] = f"/api/v1/article/{article.pk}/galley/{file_type}/"
        if sequence is not None:
            resp["Location"] += f"{sequence}/"
        return resp
