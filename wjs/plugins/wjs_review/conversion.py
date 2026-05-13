import dataclasses

from django.http import HttpRequest
from django.template.loader import render_to_string
from django.urls import reverse

from .logic import (
    BaseConvertLatexReport,
    YakuninPDFGenerationError,
    YakuninPDFGenerationWarnings,
    YakuninRequestError,
)
from .models import LatexPreamble, WjsEditorAssignment, WorkflowReviewAssignment


@dataclasses.dataclass
class LatexReportConvertService:
    """
    Base service to convert latex reports to PDF.

    Use this class to wrap BaseConvertLatexReport classes.

    In case of error a specific exception is raised with detailed information and link to download the
    yakunin log in the exception message.

    The error message is rendered using django templates to ease customisation, error reported by yakunin
    should be added to rendered content because it can provide useful information for debugging.

    :ivar assignment: Represents the current assignment related to the article.
    :type assignment: WorkflowReviewAssignment | WjsEditorAssignment
    :ivar converter_class: Represents the converter class for generating the editor report.
    :type converter_class: type[ConvertEditorLatexReport]
    :ivar report_text: Text of the report to convert
    :type report_text: str
    :ivar request: The HTTP request object for this view.
    :type request: HttpRequest
    :ivar error_template: Path to django template to render error messages
    :type error_template: str
    """

    assignment: WorkflowReviewAssignment | WjsEditorAssignment
    converter_class: type[BaseConvertLatexReport]
    report_text: str
    request: HttpRequest
    error_template: str = "wjs_review/elements/conversion_error.html"

    def _render_error(self, error: Exception, **kwargs):
        """
        Render an error message to an HTML string using the provided error and additional context.

        :param error: The exception instance containing error details
        :type error: Exception
        :param kwargs: Additional context to provide to the rendering process
        :return: Rendered HTML string representing the error message
        :rtype: str
        :raises: Any exceptions raised by `render_to_string` or during template rendering
        """
        context = {
            "error": error,
        }
        context.update(kwargs)
        return render_to_string(self.error_template, context, self.request)

    def run(self):
        """
        Execute the process of generating a TeX review document and return a URL of the generated document.

        :return: Download URL for the generated TeX review document
        :rtype: str
        :raises YakuninPDFGenerationError: If a PDF generation error occurs
        :raises YakuninPDFGenerationWarnings: If a PDF generation warning occurs
        :raises YakuninRequestError: For request-related errors or invalid configuration
        :raises LatexPreamble.DoesNotExist: If the latex preamble is missing or improperly configured
        :raises Exception: For unexpected errors during the TeX document generation process
        """
        client = self.converter_class(
            report_text=self.report_text,
            instance=self.assignment,
        )
        try:
            generated_tex_review = client.run()
        except (YakuninPDFGenerationError, YakuninPDFGenerationWarnings) as e:
            if client.logfile:
                download_url = reverse(
                    "download_single_file",
                    args=[self.assignment.article.pk, client.logfile.pk],
                )
                raise YakuninRequestError(self._render_error(e, conversion_log_url=download_url)) from e
            else:
                raise
        except (YakuninRequestError, LatexPreamble.DoesNotExist) as e:
            raise YakuninRequestError(self._render_error(e)) from e
        except Exception as e:
            raise Exception(self._render_error(e)) from e
        download_url = reverse(
            "download_single_file",
            args=[self.assignment.article.pk, generated_tex_review.pk],
        )
        return download_url
