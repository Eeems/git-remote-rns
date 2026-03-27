import inspect
import logging
import os
import time
import traceback
from argparse import Namespace
from collections import defaultdict
from subprocess import CalledProcessError
from typing import (
    Any,
    Callable,
    NoReturn,
)

import RNS

from .shared import is_valid_hexhash

log: logging.Logger = logging.getLogger(__name__)


class Request:
    def __init__(
        self,
        path: str,
        data: dict[str, Any] | None,  # pyright: ignore[reportExplicitAny]
        request_hex: str,
        identity: RNS.Identity | None,
        request_at: float,
    ) -> None:
        self.path: str = path
        self.data: dict[str, Any] = data or {}  # pyright: ignore[reportExplicitAny]
        self.request_hex: str = request_hex
        self.identity: RNS.Identity | None = identity
        self.request_at: float = request_at

    def __contains__(self, name: str) -> bool:
        return (
            name in self.data
            or f"var_{name}" in self.data
            or f"field_{name}" in self.data
        )

    def param(self, name: str) -> Any:  # pyright: ignore[reportExplicitAny, reportAny]
        return (
            self.data.get(name, None)
            or self.data.get(f"var_{name}", None)
            or self.data.get(f"field_{name}", None)
        )


class BadRequestMethod(Exception):
    pass


class InvalidParameterType(Exception):
    pass


class MissingParameter(Exception):
    pass


RequestHandlerCallable = Callable[
    [
        str,
        dict[str, Any],  # pyright: ignore[reportExplicitAny]
        bytes,
        RNS.Identity | None,
        float,
    ],
    bytes | None,
]


class Application:
    def __init__(
        self,
        app_name: str,
        aspects: list[str],
        announce_name: bytes | None = None,
        announce_interval: int | None = None,
    ) -> None:
        self.announce_name: bytes = announce_name or app_name.encode()
        self.announce_interval: int | None = announce_interval
        self._destination: RNS.Destination | None = None
        self.app_name: str = app_name
        self.aspects: list[str] = aspects
        self._identity: RNS.Identity | None = None
        self.handlers: dict[str, RequestHandlerCallable] = {}
        self.permissions: defaultdict[str, list[str]] = defaultdict(list)
        self.templates: dict[str, bytes] = {
            "not-identified": b"#!c=0\n> Not identified",
            "not-allowed": b"#!c=0\n> Not allowed",
            "exception": b"#!c=0\n> Exception\n",
        }
        self.args: Namespace | None = None

    @property
    def destination(self) -> RNS.Destination:
        if self._destination is None:
            self._destination = RNS.Destination(
                self.identity,
                RNS.Destination.IN,
                RNS.Destination.SINGLE,
                self.app_name,
                *self.aspects,
            )

        return self._destination

    @property
    def identity(self) -> RNS.Identity | None:
        return self._identity

    @identity.setter
    def identity(self, identity_or_path: str | RNS.Identity | None):  # pyright: ignore[reportPropertyTypeMismatch]
        if self._identity is not None:
            raise ValueError("Identity already set")

        if isinstance(identity_or_path, RNS.Identity):
            self._identity = identity_or_path
            return

        assert RNS.Reticulum.configdir is not None  # pyright: ignore[reportUnknownMemberType] # nosec B101
        if identity_or_path is None:
            identity_or_path = os.path.join(RNS.Reticulum.configdir, "identity")  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]

        assert identity_or_path is not None  # nosec B101
        log.info("Identity: %s", identity_or_path)
        identity = None
        if os.path.exists(identity_or_path):
            identity = RNS.Identity.from_file(identity_or_path)  # pyright: ignore[reportUnknownMemberType]

        if identity is None:
            identity = RNS.Identity(True)
            _ = identity.to_file(identity_or_path)  # pyright: ignore[reportUnknownMemberType]

        assert identity is not None  # nosec B101
        assert identity.hexhash is not None  # nosec B101
        self._identity = identity

    def announce(self) -> None:
        log.debug("Sending announce")
        _ = self.destination.announce(self.announce_name)  # pyright: ignore[reportUnknownMemberType]

    def register_handlers(self) -> None:
        for path, handler in self.handlers.items():
            self.destination.register_request_handler(  # pyright: ignore[reportUnknownMemberType]
                path,
                handler,
                RNS.Destination.ALLOW_ALL,
            )

    def unregister_handlers(self) -> None:
        for path, _ in self.handlers.items():
            _ = self.destination.deregister_request_handler(path)  # pyright: ignore[reportUnknownMemberType]

    def run(self, args: Namespace | None = None) -> NoReturn:
        self.args = args
        self.register_handlers()
        self.announce()
        if self.announce_interval is None:
            while True:
                time.sleep(10)

        while True:
            time.sleep(self.announce_interval)
            self.announce()

    def _parse_params(
        self, request: Request, parameters: list[inspect.Parameter]
    ) -> dict[str, Any]:  # pyright: ignore[reportExplicitAny]
        params: dict[str, Any] = {}  # pyright: ignore[reportExplicitAny]
        for parameter in parameters:
            name = parameter.name
            param_type: type = parameter.annotation  # pyright: ignore[reportAny]
            if name not in request:
                if parameter.default == parameter.empty:  # pyright: ignore[reportAny]
                    raise MissingParameter(f"Missing {name} parameter")

                else:
                    params[name] = parameter.default  # pyright: ignore[reportAny]

            try:
                value = request.param(name)  # pyright: ignore[reportAny]
                parsed = param_type(value)  # pyright: ignore[reportAny]
                params[name] = parsed

            except Exception as e:
                raise InvalidParameterType(
                    f"Unable to convert parameter {name} into {param_type.__name__}:"
                    + str(e)
                )

        return params

    def permit(self, identity_or_hexhash: RNS.Identity | str, permission: str):
        if isinstance(identity_or_hexhash, RNS.Identity):
            hexhash = identity_or_hexhash.hexhash

        elif not is_valid_hexhash(identity_or_hexhash):
            raise ValueError(f"Invalid hexhash: {identity_or_hexhash}")

        else:
            hexhash = identity_or_hexhash

        assert hexhash is not None
        self.permissions[permission].append(hexhash)

    def request(
        self,
        *paths: str,
        permissions: list[str] | None = None,
        ttl: float | bool = False,
    ):
        assert ttl == False or ttl >= 0
        if permissions is None:
            permissions = []

        def decorator(
            fn: Callable[..., bytes | None],
        ):
            signature = inspect.signature(fn)
            if not len(signature.parameters):
                raise BadRequestMethod(
                    "request methods must accept a Request as the first parameter: None"
                )

            parameter_iter = iter(signature.parameters.values())
            annotation: type = next(parameter_iter).annotation  # pyright: ignore[reportAny]
            if annotation != Request:
                raise BadRequestMethod(
                    f"request methods must accept a Request as the first parameter: {annotation}"
                )

            parameters: list[inspect.Parameter] = list(parameter_iter)
            cache: dict[tuple, tuple[float, bytes | None]] = {}

            def handler(
                path: str,
                data: dict[str, Any],  # pyright: ignore[reportExplicitAny]
                request_id: bytes,
                remote_identity: RNS.Identity | None,
                request_at: float,
            ) -> bytes | None:
                request_hex = hex(int.from_bytes(request_id, "big"))[2:]
                log.debug(
                    "REQUEST %s... %s | %s",
                    request_hex[:9],
                    remote_identity or "<            unknown             >",
                    path,
                )
                if permissions:
                    if remote_identity is None:
                        return self.templates["not-identified"]

                    assert remote_identity.hexhash is not None
                    hexhash = remote_identity.hexhash
                    for permission in permissions:
                        if permission == "identified":
                            continue

                        if hexhash not in self.permissions[permission]:
                            return self.templates["not-allowed"]

                try:
                    idx = tuple(parameters)
                    if ttl is not False and idx in cache:
                        _ttl, res = cache[idx]
                        if time.time() < _ttl:
                            log.debug(
                                "CACHED  %s... %s | %s",
                                request_hex[:9],
                                remote_identity or "<            unknown             >",
                                path,
                            )
                            return res

                        log.debug(
                            "STALE   %s... %s | %s",
                            request_hex[:9],
                            remote_identity or "<            unknown             >",
                            path,
                        )
                        del cache[idx]

                    request = Request(
                        path,
                        data,
                        request_hex,
                        remote_identity,
                        request_at,
                    )
                    res = fn(request, **self._parse_params(request, parameters))
                    log.debug(
                        "HANDLED %s... %s | %s",
                        request_hex[:9],
                        remote_identity or "<            unknown             >",
                        path,
                    )
                    if ttl is not False:
                        cache[idx] = (
                            time.time() + ttl if ttl else 0,
                            res,
                        )

                    return res

                except CalledProcessError as e:
                    log.error(traceback.format_exc())
                    return (
                        self.templates["exception"]
                        + f"Child processed returned {e.returncode}".encode()
                    )

                except Exception as e:
                    log.error(traceback.format_exc())
                    return self.templates["exception"] + str(e).encode()

            for path in paths:
                self.handlers[path] = handler

        return decorator
