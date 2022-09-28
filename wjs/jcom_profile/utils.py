import base64
import hashlib


def generate_token(email: str):
    """
    Encode the given email into a token suitable for use in URLs.
    :param email: The user email
    :return: The token as a string
    """
    return base64.b64encode(hashlib.sha256(email.encode('utf-8')).digest()).hex()
