from .connection import connect as _connect, ClientLink

__all__ = ["connect", "connect_client", "ClientLink"]


def connect(
    destination_hexhash: str,
    config_path: str | None = None,
    repo_path: str = "",
    timeout: float = 60.0,
) -> ClientLink:
    """Create a client link to a remote RNS destination.

    Args:
        destination_hexhash: 32-character hex string of the destination hash.
        config_path: Optional path to RNS config directory.
        repo_path: Optional repo path to request from server.
        timeout: Timeout for path discovery in seconds.

    Returns:
        A ClientLink instance connected to the destination.

    Raises:
        ValueError: If destination hash is invalid or connection fails.
    """
    return _connect(destination_hexhash, config_path, repo_path, timeout)


connect_client = connect
