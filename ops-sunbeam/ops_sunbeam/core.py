# Copyright 2021 Canonical Ltd.
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

"""Collection of core components."""

import collections
import secrets
import string
from typing import (
    TYPE_CHECKING,
    Generator,
    Mapping,
    MutableMapping,
    Sequence,
    Union,
)

import ops_sunbeam.tracing as sunbeam_tracing

if TYPE_CHECKING:
    from ops_sunbeam.charm import (
        OSBaseOperatorCharm,
    )
    from ops_sunbeam.config_contexts import (
        ConfigContext,
    )
    from ops_sunbeam.relation_handlers import (
        RelationHandler,
    )

ContainerConfigFile = collections.namedtuple(
    "ContainerConfigFile",
    ["path", "user", "group", "permissions"],
    defaults=(None,),
)

RelationDataMapping = MutableMapping[str, str]
ConfigMapping = Mapping[str, bool | int | float | str | None]
ContextMapping = RelationDataMapping | ConfigMapping


@sunbeam_tracing.trace_type
class OPSCharmContexts:
    """Set of config contexts and contexts from relation handlers."""

    def __init__(self, charm: "OSBaseOperatorCharm") -> None:
        """Run constructor."""
        self.charm = charm
        self.namespaces: list[str] = []

    def add_relation_handler(self, handler: "RelationHandler") -> None:
        """Add relation handler."""
        interface, relation_name = handler.get_interface()
        _ns = relation_name.replace("-", "_")
        self.namespaces.append(_ns)
        ctxt = handler.context()
        obj_name = "".join([w.capitalize() for w in relation_name.split("-")])
        obj = collections.namedtuple(obj_name, ctxt.keys())(*ctxt.values())
        setattr(self, _ns, obj)
        # Add special sobriquet for peers.
        if _ns == "peers":
            self.namespaces.append("leader_db")
            setattr(self, "leader_db", obj)

    def add_config_contexts(
        self, config_adapters: Sequence["ConfigContext"]
    ) -> None:
        """Add multiple config contexts."""
        for config_adapter in config_adapters:
            self.add_config_context(config_adapter, config_adapter.namespace)

    def add_config_context(
        self, config_adapter: "ConfigContext", namespace: str
    ) -> None:
        """Add add config adapter to context."""
        self.namespaces.append(namespace)
        setattr(self, namespace, config_adapter)

    def __iter__(
        self,
    ) -> Generator[
        tuple[str, Union["ConfigContext", "RelationHandler"]], None, None
    ]:
        """Iterate over the relations presented to the charm."""
        for namespace in self.namespaces:
            yield namespace, getattr(self, namespace)


class PostInitMeta(type):
    """Allows object to run post init methods."""

    def __call__(cls, *args, **kw):
        """Call __post_init__ after __init__."""
        instance = super().__call__(*args, **kw)
        instance.__post_init__()
        return instance


def random_string(length: int) -> str:
    """Utility function to generate secure random string."""
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for i in range(length))
