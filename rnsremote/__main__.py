import sys

from .client import main as _client
from .server import main as _server


def client():
    sys.exit(_client())


def server():
    sys.exit(_server())
