# Copyright 2024 Canonical Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


"""Utilities for tracing."""

from pathlib import (
    Path,
)
from typing import (
    Any,
    Callable,
    Optional,
    Sequence,
    TypeVar,
    Union,
    overload,
)

_T = TypeVar("_T", bound=type)

try:
    from charms.tempo_coordinator_k8s.v0.charm_tracing import (
        trace_type,
    )
except ImportError:

    def trace_type(cls: _T) -> _T:
        """No-op decorator for tracing."""
        return cls


try:
    from charms.tempo_coordinator_k8s.v0.charm_tracing import (
        trace_charm,
    )
except ImportError:

    def trace_charm(
        tracing_endpoint: str,
        server_cert: Optional[str] = None,
        service_name: Optional[str] = None,
        extra_types: Sequence[type] = (),
        buffer_max_events: int = 100,
        buffer_max_size_mib: int = 10,
        buffer_path: Optional[Union[str, Path]] = None,
    ) -> Callable[[_T], _T]:
        """No-op decorator for tracing."""

        def _wrapper(charm_cls: _T) -> _T:
            return charm_cls

        return _wrapper


@overload
def trace_sunbeam_charm(
    *,
    tracing_endpoint: str = "get_tracing_endpoint",
    server_cert: Optional[str] = None,
    service_name: Optional[str] = None,
    extra_types: Sequence[type] = (),
) -> Callable[[_T], _T]:
    ...  # fmt: skip


@overload
def trace_sunbeam_charm(
    charm_cls: _T,
    /,
) -> _T:
    ...  # fmt: skip


def trace_sunbeam_charm(*args, **kwargs) -> Any:
    """Decorator for tracing sunbeam charms.

    This decorator allows either decorating a charm class directly or
    passing parameters to the decorator.

    Usage:
        @trace_sunbeam_charm
        class MyCharm(...):
            ...

        or

        @trace_sunbeam_charm(
            tracing_endpoint="get_tracing_endpoint",
            server_cert="path/to/server.crt",
            service_name="my-service",
            extra_types=(MyType,),
        )
        class MyCharm(...):
            ...

        or

        class MyCharm(...):
            ...

        MyCharm = trace_sunbeam_charm(MyCharm)

        or

        MyCharm = trace_sunbeam_charm(
            tracing_endpoint="get_tracing_endpoint",
            server_cert="path/to/server.crt",
            service_name="my-service",
            extra_types=(MyType,),
        )(MyCharm)
    """
    if len(args) == 1 and not kwargs:
        charm_cls = args[0]
        return trace_charm(
            tracing_endpoint="get_tracing_endpoint",
        )(charm_cls)

    return trace_charm(
        tracing_endpoint=kwargs.get(
            "tracing_endpoint", "get_tracing_endpoint"
        ),
        server_cert=kwargs.get("server_cert"),
        service_name=kwargs.get("service_name"),
        extra_types=kwargs.get("extra_types", ()),
    )
