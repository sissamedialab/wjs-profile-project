"""Websocket server used for long-process feedback."""

import json
import logging
import tarfile
import tempfile
from base64 import b64decode
from io import BytesIO
from pathlib import Path

from channels.generic.websocket import AsyncWebsocketConsumer

logger = logging.getLogger(__name__)


class FeedbackConsumer(AsyncWebsocketConsumer):
    """Forwards messages to the client."""

    _supported_messages = ["feedback.message", "completed.data"]

    async def connect(self):
        """Accept connection and join group."""
        self.feedback_wsname = self.scope["url_route"]["kwargs"]["feedback_wsname"]
        self.feedback_group_name = f"group_{self.feedback_wsname}"

        await self.channel_layer.group_add(
            self.feedback_group_name,
            self.channel_name,
        )
        await self.accept()

    def _unpack_response_file(self, content: str) -> Path | None:
        if not content:
            return
        if content:
            content = b64decode(content)
        unpack_dir = tempfile.mkdtemp()

        with BytesIO(content) as file_obj:
            with tarfile.open(fileobj=file_obj, mode="r:gz") as tar:
                tar.extractall(path=unpack_dir)
        unpack_dir = Path(unpack_dir)

        return unpack_dir

    def _report_yakunin_errors(self, unpack_dir: Path) -> bool:
        has_error_or_critical = False
        # Assuming the file is always present if response.status_code is 200
        yakunin_log_file = next(unpack_dir.glob("yakunin-task.log"), None)

        with open(yakunin_log_file) as log_file:
            for line in log_file:
                if line.startswith("ERROR"):
                    has_error_or_critical = True
                    break
                elif line.startswith("CRITICAL"):
                    has_error_or_critical = True
                    break
        return has_error_or_critical

    async def disconnect(self, close_code):
        """Disconnect and leave group."""
        await self.channel_layer.group_discard(
            self.feedback_group_name,
            self.channel_name,
        )

    async def feedback_message(self, event):
        """Receive message from group and send to client."""
        message = event["message"]
        await self.send(text_data=json.dumps({"status_log": message["text"], "status": message["status"]}))

    async def completed_data(self, event):
        """Receive message from group and send to client."""
        message = event["message"]
        tar_content = self._unpack_response_file(message["data"])
        if tar_content and self._report_yakunin_errors(tar_content):
            message["status"] = "error"
        await self.send(text_data=json.dumps({"status_log": message["text"], "status": message["status"]}))

    async def receive(self, text_data=None, bytes_data=None):
        """Send any received message to its linked group."""
        text_data_json = json.loads(text_data)
        # Filter out unsupported message types to be able to relay supported ones without explicity knowledge of
        # the message type
        logger.debug(f"Received message: {text_data_json.get('type')}")
        if text_data_json.get("type") not in self._supported_messages:
            return
        await self.channel_layer.group_send(
            self.feedback_group_name,
            text_data_json,
        )
