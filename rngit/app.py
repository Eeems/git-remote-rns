import ctypes
import inspect
import json
import logging
import os
import threading
import time
import traceback
import typing
from argparse import Namespace
from collections import defaultdict
from enum import Enum
from hashlib import sha256
from io import BufferedReader
from subprocess import CalledProcessError
from typing import (
    Any,
    Callable,
    NoReturn,
    cast,
)

import RNS

from . import micron
from ._compat import override
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
        for key in (name, f"var_{name}", f"field_{name}"):
            if key in self.data:
                return True

        return False

    def param(self, name: str) -> Any:  # pyright: ignore[reportExplicitAny, reportAny]
        for key in (name, f"var_{name}", f"field_{name}"):
            if key in self.data:
                return self.data[key]  # pyright: ignore[reportAny]

        return None


class BadRequestMethod(Exception):
    pass


class InvalidParameterType(ExceptionGroup):
    pass


class MissingParameter(Exception):
    pass


class TemplateExists(Exception):
    pass


class ThreadTimeout(BaseException):
    pass


FileResponse = tuple[BufferedReader, dict[str, bytes]]
Handler = Callable[
    [
        str,
        dict[str, Any],  # pyright: ignore[reportExplicitAny]
        bytes,
        RNS.Identity | None,
        float,
    ],
    bytes | FileResponse | None,
]
HandlerRegistration = tuple[str, Handler, int, list[str], bool]
RequestHandler = Callable[..., FileResponse | bytes | None]
PageHandler = Callable[..., bytes | None]
FileHandler = Callable[..., FileResponse | None]


# Hack to allow returning "Not found" errors for pages without handlers"
class RequestHandlers(defaultdict):  # pyright: ignore[reportMissingTypeArgument]
    def __init__(self, app: "Application"):
        super().__init__()  # pyright: ignore[reportUnknownMemberType]
        self._default: HandlerRegistration = (
            "?",
            app.default_handler,
            RNS.Destination.ALLOW_ALL,
            [],
            True,
        )

    @override
    def __contains__(self, _, /) -> bool:
        return True

    @override
    def __missing__(self, _, /) -> HandlerRegistration:
        return self._default


class Template:
    def __init__(self, template: str) -> None:
        self._template: str = template

    def __call__(self, *args: object, **kwds: object) -> bytes:
        return self._template.format(*args, **kwds).encode()

    def __bytes__(self) -> bytes:
        return self()


class SpecialPermissions(Enum):
    ALL = "(any)"
    NONE = "(none)"


class Application:
    def __init__(
        self,
        app_name: str,
        aspects: list[str],
        announce_name: bytes | None = None,
        announce_interval: int | None = None,
        templates: dict[str, str] | None = None,
    ) -> None:
        self.announce_name: bytes = announce_name or app_name.encode()
        self.announce_interval: int | None = announce_interval
        self._destination: RNS.Destination | None = None
        self.app_name: str = app_name
        self.aspects: list[str] = aspects
        self._identity: RNS.Identity | None = None
        self.handlers: dict[str, tuple[Handler, bool]] = {}
        self.permissions: defaultdict[str, list[str]] = defaultdict(list)
        if templates is None:
            templates = {}

        self.templates: dict[str, Template] = {
            "not-identified": Template("#!c=0\n> Not identified"),
            "not-allowed": Template("#!c=0\n> Not allowed"),
            "exception": Template("#!c=0\n> {title}\n {type}: {message}"),
            "unknown": Template(
                "#!c=0\n> Not Found\nNo route configured for this path"
            ),
            "timeout": Template("#!c=0\n> Timeout\nRequest timed out"),
            **{k: Template(v) for k, v in templates.items()},
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
            self._destination.request_handlers = RequestHandlers(self)
            assert "x" in self._destination.request_handlers
            self.destination.set_link_established_callback(self.on_link_established)  # pyright: ignore[reportUnknownMemberType]

        return self._destination

    def on_link_established(self, link: RNS.Link) -> None:
        log.debug("Connection established: %s", link)
        link.set_remote_identified_callback(self.on_remote_identified)  # pyright: ignore[reportUnknownMemberType]

    def on_remote_identified(self, link: RNS.Link, identity: RNS.Identity) -> None:
        log.debug("Connection %s identified: %s", link, identity)

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

        assert RNS.Reticulum.configdir is not None  # pyright: ignore[reportUnknownMemberType]
        if identity_or_path is None:
            identity_or_path = os.path.join(RNS.Reticulum.configdir, "identity")  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]

        assert identity_or_path is not None
        log.info("Identity: %s", identity_or_path)
        identity = None
        if os.path.exists(identity_or_path):
            identity = RNS.Identity.from_file(identity_or_path)  # pyright: ignore[reportUnknownMemberType]

        if identity is None:
            identity = RNS.Identity(True)
            _ = identity.to_file(identity_or_path)  # pyright: ignore[reportUnknownMemberType]

        assert identity is not None
        assert identity.hexhash is not None
        self._identity = identity

    def announce(self) -> None:
        log.debug("Sending announce")
        _ = self.destination.announce(self.announce_name)  # pyright: ignore[reportUnknownMemberType]

    def register_handlers(self) -> None:
        for path, (handler, compress) in self.handlers.items():
            log.debug("Registering handler for %s", path)
            self.destination.register_request_handler(  # pyright: ignore[reportUnknownMemberType]
                path,
                handler,
                RNS.Destination.ALLOW_ALL,
                auto_compress=compress,
            )

    def unregister_handlers(self) -> None:
        for path, _ in self.handlers.items():
            log.debug("Deregistering handler for %s", path)
            _ = self.destination.deregister_request_handler(path)  # pyright: ignore[reportUnknownMemberType]

    def run(self, args: Namespace | None = None) -> NoReturn:
        self.args = args
        self.register_handlers()
        self.announce()
        if self.announce_interval is None:
            while True:
                time.sleep(10)

        while True:
            time.sleep(self.announce_interval or 0.1)
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

                params[name] = parameter.default  # pyright: ignore[reportAny]
                continue

            exceptions: list[Exception] = []
            value = request.param(name)  # pyright: ignore[reportAny]
            for sub_type in typing.get_args(param_type) or (param_type,):
                try:
                    parsed = sub_type(value)  # pyright: ignore[reportAny]
                    if sub_type is str:
                        assert isinstance(parsed, str)
                        parsed = micron.paramunescape(parsed)

                    params[name] = parsed
                    break

                except Exception as e:
                    exceptions.append(e)

            else:
                raise InvalidParameterType(
                    f"Unable to convert parameter {name} into {getattr(param_type, '__name__', str(param_type))}",
                    exceptions,
                )

        return params

    def permit(
        self,
        identity_or_hexhash_or_special: RNS.Identity | str | SpecialPermissions,
        permission: str,
    ):
        if isinstance(identity_or_hexhash_or_special, SpecialPermissions):
            hexhash = identity_or_hexhash_or_special.value

        elif isinstance(identity_or_hexhash_or_special, RNS.Identity):
            hexhash = identity_or_hexhash_or_special.hexhash

        elif not is_valid_hexhash(identity_or_hexhash_or_special):
            raise ValueError(f"Invalid hexhash: {identity_or_hexhash_or_special}")

        else:
            hexhash = identity_or_hexhash_or_special

        assert hexhash is not None
        self.permissions[permission].append(hexhash)

    def _log_request_state(
        self,
        state: str,
        request_hex: str,
        remote_identity: RNS.Identity | None,
        path: str,
    ) -> None:
        log.debug(
            "%s %s... %s | %s",
            state,
            request_hex[:9],
            remote_identity or "<            unknown             >",
            path,
        )

    def exception(self, request: Request, exception: Exception) -> bytes | None:
        stacktrace = traceback.format_exc()
        tpl = self.template("exception")
        cls = exception.__class__.__name__
        if isinstance(exception, CalledProcessError):
            if exception.stdout:  # pyright: ignore[reportAny]
                stdout = exception.stdout  # pyright: ignore[reportAny]
                if isinstance(stdout, bytes):
                    stdout = stdout.decode()

                stacktrace += f"\nstdout: {stdout}"

            if exception.stderr:  # pyright: ignore[reportAny]
                stderr = exception.stderr  # pyright: ignore[reportAny]
                if isinstance(stderr, bytes):
                    stderr = stderr.decode()

                stacktrace += f"\nstderr: {stderr}"

            if isinstance(exception.cmd, list):  # pyright: ignore[reportAny]
                cmd = cast(str | bytes, exception.cmd[0])

            else:
                cmd = cast(str | bytes, exception.cmd).split()[0]

            if isinstance(cmd, bytes):
                cmd = cmd.decode()

            message = f"{cmd} returned with exit code {exception.returncode}"

        else:
            message = str(exception)

        log.error(stacktrace)
        title = "Unable to serve"
        if request.path:
            title += " " + request.path

        identity = request.identity
        if (
            identity is not None
            and identity.hexhash is not None
            and identity.hexhash in self.permissions["debug"]
        ):
            message += "\n" + "\n".join(
                [f"  {x}" for x in stacktrace.splitlines(False)]
            )

        return tpl(
            title=title,
            type=cls,
            message=message,
        )

    def default_handler(
        self,
        path: str,
        _data: dict[str, Any],  # pyright: ignore[reportExplicitAny]
        request_id: bytes,
        remote_identity: RNS.Identity | None,
        _request_at: float,
    ) -> bytes | None:
        request_hex = hex(int.from_bytes(request_id, "big"))[2:]
        self._log_request_state("REQUEST", request_hex, remote_identity, path)
        self._log_request_state("UNKNOWN", request_hex, remote_identity, path)
        return self.template("unknown")()

    def page(
        self,
        *paths: str,
        permissions: list[str] | None = None,
        ttl: float | bool = False,
        timeout: float | None = 10.0,
        compress: bool = True,
    ) -> Callable[[PageHandler], None]:
        return self.request(
            *[f"/page/{x}.mu" for x in paths],
            permissions=permissions,
            ttl=ttl,
            timeout=timeout,
            compress=compress,
        )

    def file(
        self,
        *paths: str,
        permissions: list[str] | None = None,
        ttl: float | bool = False,
        timeout: float | None = 10.0,
    ) -> Callable[[FileHandler], None]:
        return self.request(
            *[f"/file/{x}" for x in paths],
            permissions=permissions,
            ttl=ttl,
            timeout=timeout,
            compress=False,
        )

    def request(  # noqa: MC0001
        self,
        *paths: str,
        permissions: list[str] | None = None,
        ttl: float | bool = False,
        timeout: float | None = 10.0,
        compress: bool = True,
    ) -> Callable[[RequestHandler], None]:
        assert ttl is False or ttl >= 0  # noqa: E712
        if permissions is None:
            permissions = []

        def decorator(fn: RequestHandler) -> None:
            signature = inspect.signature(fn)
            if len(signature.parameters) == 0:
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
            cache: dict[str, tuple[float, bytes | None]] = {}
            # TODO lock per path  pylint: disable=W0511
            lock: threading.Lock = threading.Lock()

            def handler(
                path: str,
                data: dict[str, Any],  # pyright: ignore[reportExplicitAny]
                request_id: bytes,
                remote_identity: RNS.Identity | None,
                request_at: float,
            ) -> bytes | FileResponse | None:
                request_hex = hex(int.from_bytes(request_id, "big"))[2:]
                self._log_request_state("REQUEST", request_hex, remote_identity, path)
                if permissions:
                    for permission in permissions:
                        if SpecialPermissions.ALL.value in self.permissions[permission]:
                            continue

                        if (
                            SpecialPermissions.NONE.value
                            in self.permissions[permission]
                        ):
                            self._log_request_state(
                                "DENIED ",
                                request_hex,
                                remote_identity,
                                path,
                            )
                            return self.template("not-allowed")()

                        if remote_identity is None:
                            self._log_request_state(
                                "DENIED ",
                                request_hex,
                                remote_identity,
                                path,
                            )
                            return self.template("not-identified")()

                        if permission == "identified":
                            continue

                        assert remote_identity.hexhash is not None
                        hexhash = remote_identity.hexhash
                        if hexhash not in self.permissions[permission]:
                            self._log_request_state(
                                "DENIED ",
                                request_hex,
                                remote_identity,
                                path,
                            )
                            return self.template("not-allowed")()

                request = Request(
                    path,
                    data,
                    request_hex,
                    remote_identity,
                    request_at,
                )
                with lock:
                    try:
                        params = self._parse_params(request, parameters)
                        idx = sha256(
                            path.encode() + json.dumps(params, sort_keys=True).encode()
                        ).hexdigest()
                        res: Exception | bytes | FileResponse | None = None
                        if ttl is not False and idx in cache:
                            _ttl, res = cache[idx]
                            if time.time() <= _ttl:
                                self._log_request_state(
                                    "CACHED ",
                                    request_hex,
                                    remote_identity,
                                    path,
                                )
                                return res

                            self._log_request_state(
                                "STALE  ",
                                request_hex,
                                remote_identity,
                                path,
                            )
                            del cache[idx]

                        def target(
                            fn: RequestHandler,
                            request: Request,
                            **kwargs,  # pyright: ignore[reportUnknownParameterType, reportMissingParameterType]
                        ):
                            nonlocal res
                            try:
                                res = fn(request, **kwargs)

                            except Exception as e:
                                res = e

                            except ThreadTimeout:
                                log.error("Request %s thread interrupted", request_hex)

                        thread = threading.Thread(
                            target=target,  # pyright: ignore[reportUnknownArgumentType]
                            args=(
                                fn,
                                request,
                            ),
                            kwargs=params,
                        )
                        thread.start()
                        thread.join(timeout)
                        if timeout is not None and thread.is_alive():
                            self._log_request_state(
                                "TIMEOUT",
                                request_hex,
                                remote_identity,
                                path,
                            )
                            thread_id = ctypes.c_long(thread.ident)  # pyright: ignore[reportArgumentType]
                            returncode = ctypes.pythonapi.PyThreadState_SetAsyncExc(  # pyright: ignore[reportAny]
                                thread_id,
                                ctypes.py_object(ThreadTimeout),
                            )
                            if returncode < 1:
                                log.error(
                                    "Request %s thread not found when trying to inject exception",
                                    request_hex,
                                )

                            elif returncode > 1:
                                ctypes.pythonapi.PyThreadState_SetAsyncExc(thread_id, 0)

                            thread.join(5)
                            if thread.is_alive():
                                log.error(
                                    "Request %s thread still not stopped, there may now be zombie threads",
                                    request_hex,
                                )

                            return self.template("timeout")()

                        if isinstance(res, Exception):
                            raise res

                        self._log_request_state(
                            "HANDLED",
                            request_hex,
                            remote_identity,
                            path,
                        )
                        if isinstance(res, bytes | None) and ttl is not False:  # pyright: ignore[reportUnnecessaryIsInstance]
                            cache[idx] = (time.time() + ttl, res)

                        return res

                    except Exception as e:
                        self._log_request_state(
                            "ERRORED",
                            request_hex,
                            remote_identity,
                            path,
                        )
                        return self.exception(request, e)

            for path in paths:
                self.handlers[path] = (handler, compress)

        return decorator

    def template(self, name: str, template: str | None = None) -> Template:
        if template is None:
            return self.templates[name]

        if name in self.templates:
            raise TemplateExists(name)

        self.templates[name] = Template(template)
        return self.templates[name]
