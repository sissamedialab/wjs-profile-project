"""Websocket server used for long-process feedback."""

import json
import logging
from typing import ClassVar

from asgiref.sync import sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from core.models import File
from django.db import transaction
from plugins.wjs_submission.conversion import get_feedback_logfile

logger = logging.getLogger(__name__)
# Define importance hierarchy of results: success < warning < error
importance = {"success": 1, "warning": 2, "error": 3}


class FeedbackConsumer(AsyncWebsocketConsumer):
    """Forwards messages to the client."""

    _supported_messages: ClassVar = ["feedback.message"]

    def _update_logfile(self, log_filename, status, result):
        """
        Update the logfile with the given status and result.

        This method uses a database transaction to update the label and description
        of a logfile associated with an article. If the logfile's current label is
        not "completed", it updates the label to the given status. The logfile's
        description is updated only if the new result has a higher importance than
        the current description. Logs an error if the specified logfile cannot be
        found.

        Using select_for_update to lock the row for update to prevent race conditions with other websocket messages.
        Log file is updated only if status is not completed, which is guaranteed to be isolated and atomic by
        the row-level-locking.

        :param log_filename: The name of the log file to update
        :type log_filename: str
        :param status: The new status to apply to the logfile
        :type status: str
        :param result: The result to update the logfile's description
        :type result: str
        :raises File.DoesNotExist: If the log file with the given filename does not exist
        """
        try:
            with transaction.atomic():
                logfile = File.objects.select_for_update().get(
                    article_id=self.article_id, original_filename=log_filename
                )

                # HELP: can we have the last message from yakunin
                # being processed _after_ the "completed.data" message from wjs-review?
                if logfile.label != "completed":
                    logfile.label = status
                    # Only update description if new result is more important than current
                    current_importance = importance.get(logfile.description, 0)
                    new_importance = importance.get(result, 0)
                    if new_importance > current_importance:
                        logfile.description = result

                    logfile.save()
                else:
                    logger.warning(f"Skipping setting {status} on a completed log.")

        except File.DoesNotExist:
            logger.error(f"Cannot find log file {log_filename}")

    async def _persist_feedback(self, feedback: dict):
        """
        Persist feedback info in a log file.

        See the description of the conventions used in
        wjs_review.logic.ConvertManuscriptToPdf.create_log_file().

        """
        # Sanitize input before using:
        result = {
            "success": "success",
            "warning": "warning",
            "error": "error",
            "failed": "failed",
        }.get(feedback["result"], "unknown")

        status = {
            "running": "running",
            "completed": "completed",
            "failed": "failed",
        }.get(feedback["status"], "running")

        log_filename = get_feedback_logfile(self.uuid)
        logger.debug(f"Persisting log to {log_filename} - status: {status} - result: {result}")
        await sync_to_async(self._update_logfile)(log_filename, status, result)

    async def connect(self):
        """Accept connection and join group."""
        self.feedback_wsname = self.scope["url_route"]["kwargs"]["feedback_wsname"]
        self.article_id = self.scope["url_route"]["kwargs"]["article_id"]
        self.uuid = self.scope["url_route"]["kwargs"]["uuid"]
        self.feedback_group_name = f"group_{self.feedback_wsname}"

        await self.channel_layer.group_add(
            self.feedback_group_name,
            self.channel_name,
        )
        await self.accept()

    async def disconnect(self, close_code):
        """Disconnect and leave group."""
        await self.channel_layer.group_discard(
            self.feedback_group_name,
            self.channel_name,
        )

    async def receive(self, text_data=None, bytes_data=None):
        """Send any received message to its linked group."""
        text_data_json = json.loads(text_data)
        # Filter out unsupported message types to be able to relay supported ones without explicity knowledge of
        # the message type
        #
        # ATM, only feedback messages are allowed through the websocket.
        # Other operations (completed.data and error.log) are to be called directly.
        if text_data_json.get("type") not in self._supported_messages:
            await sync_to_async(logger.warning)(
                f"""Received message type "{text_data_json.get("type")}" not supported!""",
            )
            return

        await self._persist_feedback(
            {
                "result": text_data_json.get("result"),
                "status": "running" if text_data_json.get("type") == "feedback.message" else "undefined",
            },
        )

        await self.channel_layer.group_send(
            self.feedback_group_name,
            text_data_json,
        )

    async def feedback_message(self, event):
        """Receive message from group and send to client."""
        message = event["message"]
        payload = {
            "result": message.get("result"),
            "status": "running",
            "log_url": message.get("data"),
            "status_log": message.get("text"),
        }
        logger.debug(f"Received feedback_message: {json.dumps(message)}")
        await self.send(text_data=json.dumps(payload))

    async def completed_data(self, event):
        """
        Receive message from group and send to client.

        This is intended to be used directly by the backend code, not the client.
        This is why completed.data is not among the _supported_messages.

        """
        message = event["message"]
        payload = {
            "result": message.get("result"),
            "status": "completed",
            "log_url": message.get("data"),
            "status_log": message.get("text"),
        }
        # Do not `await self._persist_feedback(payload)`
        # Let this be handled elsewhere.
        # I.e. we do not rely on the WS feedback to persist this status, but on the business logic
        logger.debug(f"Received completed_data: {json.dumps(message)}")
        await self.send(text_data=json.dumps(payload))

    async def error_log(self, event):
        """
        Receive message from group and send to client.

        This is intended to be used directly by the backend code, not the client.
        This is why error.log is not among the _supported_messages.

        """
        message = event["message"]
        payload = {
            "result": message.get("result"),
            "status": "failed",
            "log_url": message.get("log_url"),
            "status_log": message.get("text"),
        }
        if message.get("data"):
            payload["log_file"] = message["data"]
        # Do not _persist_feedback() (see above).
        logger.debug(f"Received error_log: {json.dumps(message)}")
        await self.send(text_data=json.dumps(payload))
