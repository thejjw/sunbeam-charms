#!/usr/bin/env python3
#
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
#
#
# Learn more at: https://juju.is/docs/sdk

"""Charm the service.

Refer to the following post for a quick-start guide that will help you
develop a new k8s charm using the Operator Framework:

    https://discourse.charmhub.io/t/4208
"""

import base64
import binascii
import json
import logging
from collections import (
    defaultdict,
)
from pathlib import (
    Path,
)
from typing import (
    Callable,
    Dict,
    List,
)

import charms.keystone_k8s.v0.domain_config as sunbeam_dc_svc
import charms.keystone_k8s.v0.identity_credentials as sunbeam_cc_svc
import charms.keystone_k8s.v0.identity_resource as sunbeam_ops_svc
import charms.keystone_k8s.v1.identity_service as sunbeam_id_svc
import jinja2
import ops.charm
import ops.pebble
import ops_sunbeam.charm as sunbeam_charm
import ops_sunbeam.config_contexts as sunbeam_contexts
import ops_sunbeam.container_handlers as sunbeam_chandlers
import ops_sunbeam.core as sunbeam_core
import ops_sunbeam.guard as sunbeam_guard
import ops_sunbeam.job_ctrl as sunbeam_job_ctrl
import ops_sunbeam.relation_handlers as sunbeam_rhandlers
import pwgen
from charms.certificate_transfer_interface.v0.certificate_transfer import (
    CertificateTransferProvides,
)
from ops.charm import (
    ActionEvent,
    RelationChangedEvent,
)
from ops.framework import (
    StoredState,
)
from ops.main import (
    main,
)
from ops.model import (
    MaintenanceStatus,
    ModelError,
    Relation,
    SecretNotFoundError,
    SecretRotate,
)
from utils import (
    certs,
    manager,
)

logger = logging.getLogger(__name__)

KEYSTONE_CONTAINER = "keystone"
FERNET_KEYS_PREFIX = "fernet-"
CREDENTIALS_SECRET_PREFIX = "credentials_"
SECRET_PREFIX = "secret://"
CERTIFICATE_TRANSFER_LABEL = "certs_to_transfer"
KEYSTONE_CONF = "/etc/keystone/keystone.conf"
LOGGING_CONF = "/etc/keystone/logging.conf"


class KeystoneLoggingAdapter(sunbeam_contexts.ConfigContext):
    """Config adapter to collect logging config."""

    def context(self):
        """Configuration context."""
        config = self.charm.model.config
        ctxt = {}
        if config["debug"]:
            ctxt["root_level"] = "DEBUG"
        log_level = config["log-level"]
        if log_level in ["DEBUG", "INFO", "WARNING", "ERROR"]:
            ctxt["log_level"] = log_level
        else:
            logger.error(
                "log-level must be one of the following values "
                f'(DEBUG, INFO, WARNING, ERROR) not "{log_level}"'
            )
            ctxt["log_level"] = None
        return ctxt


class KeystoneConfigAdapter(sunbeam_contexts.ConfigContext):
    """Config adapter to collect keystone config."""

    def context(self):
        """Configuration context."""
        config = self.charm.model.config
        return {
            "api_version": 3,
            "admin_role": self.charm.admin_role,
            "assignment_backend": "sql",
            "service_tenant_id": self.charm.service_project_id,
            "admin_domain_name": self.charm.admin_domain_name,
            "admin_domain_id": self.charm.admin_domain_id,
            "auth_methods": "external,password,token,oauth1,mapped,application_credential",
            "default_domain_id": self.charm.default_domain_id,
            "public_port": self.charm.service_port,
            "debug": config["debug"],
            "token_expiration": 3600,  # 1 hour
            "allow_expired_window": 169200,  # 2 days - 1 hour
            "catalog_cache_expiration": config["catalog-cache-expiration"],
            "dogpile_cache_expiration": config["dogpile-cache-expiration"],
            "identity_backend": "sql",
            "token_provider": "fernet",
            "fernet_max_active_keys": 4,  # adjusted to make rotation daily
            "public_endpoint": self.charm.public_endpoint,
            "admin_endpoint": self.charm.admin_endpoint,
            "domain_config_dir": self.charm.domain_config_dir,
            "domain_ca_dir": self.charm.domain_ca_dir,
            "log_config": "/etc/keystone/logging.conf.j2",
            "paste_config_file": "/etc/keystone/keystone-paste.ini",
        }


class IdentityServiceProvidesHandler(sunbeam_rhandlers.RelationHandler):
    """Handler for identity service relation."""

    def __init__(
        self,
        charm: ops.charm.CharmBase,
        relation_name: str,
        callback_f: Callable,
    ):
        super().__init__(charm, relation_name, callback_f)

    def setup_event_handler(self):
        """Configure event handlers for an Identity service relation."""
        logger.debug("Setting up Identity Service event handler")
        id_svc = sunbeam_id_svc.IdentityServiceProvides(
            self.charm,
            self.relation_name,
        )
        self.framework.observe(
            id_svc.on.ready_identity_service_clients,
            self._on_identity_service_ready,
        )
        return id_svc

    def _on_identity_service_ready(self, event) -> None:
        """Handles AMQP change events."""
        # Ready is only emitted when the interface considers
        # that the relation is complete (indicated by a password)
        self.callback_f(event)

    @property
    def ready(self) -> bool:
        """Report if relation is ready."""
        return True


class DomainConfigHandler(sunbeam_rhandlers.RelationHandler):
    """Handler for domain config relation."""

    def __init__(
        self,
        charm: ops.charm.CharmBase,
        relation_name: str,
        callback_f: Callable,
    ):
        super().__init__(charm, relation_name, callback_f)

    def setup_event_handler(self) -> ops.framework.Object:
        """Configure event handlers for an Identity service relation."""
        logger.debug("Setting up Identity Service event handler")
        self.dc = sunbeam_dc_svc.DomainConfigRequires(
            self.charm,
            self.relation_name,
        )
        self.framework.observe(
            self.dc.on.config_changed,
            self._on_dc_config_changed,
        )
        self.framework.observe(
            self.dc.on.goneaway,
            self._on_dc_config_changed,
        )
        return self.dc

    def _on_dc_config_changed(self, event) -> Dict:
        """Handles relation data changed events."""
        self.callback_f(event)

    def get_domain_configs(self, exclude=None):
        """Return domain config from relations."""
        return self.dc.get_domain_configs(exclude=exclude)

    @property
    def ready(self) -> bool:
        """Report if relation is ready."""
        return bool(self.get_domain_configs())


class IdentityCredentialsProvidesHandler(sunbeam_rhandlers.RelationHandler):
    """Handler for identity credentials relation."""

    def __init__(
        self,
        charm: ops.charm.CharmBase,
        relation_name: str,
        callback_f: Callable,
    ):
        super().__init__(charm, relation_name, callback_f)

    def setup_event_handler(self):
        """Configure event handlers for a Identity Credentials relation."""
        logger.debug("Setting up Identity Credentials event handler")
        id_svc = sunbeam_cc_svc.IdentityCredentialsProvides(
            self.charm,
            self.relation_name,
        )
        self.framework.observe(
            id_svc.on.ready_identity_credentials_clients,
            self._on_identity_credentials_ready,
        )
        return id_svc

    def _on_identity_credentials_ready(self, event) -> None:
        """Handles identity credentials change events."""
        # Ready is only emitted when the interface considers
        # that the relation is complete (indicated by a username)
        self.callback_f(event)

    @property
    def ready(self) -> bool:
        """Check if handler is ready."""
        return True


class IdentityResourceProvidesHandler(sunbeam_rhandlers.RelationHandler):
    """Handler for identity resource relation."""

    def __init__(
        self,
        charm: ops.charm.CharmBase,
        relation_name: str,
        callback_f: Callable,
    ):
        super().__init__(charm, relation_name, callback_f)

    def setup_event_handler(self):
        """Configure event handlers for an Identity resource relation."""
        logger.debug("Setting up Identity Resource event handler")
        ops_svc = sunbeam_ops_svc.IdentityResourceProvides(
            self.charm,
            self.relation_name,
        )
        self.framework.observe(
            ops_svc.on.process_op,
            self._on_process_op,
        )
        return ops_svc

    def _on_process_op(self, event) -> None:
        """Handles keystone ops events."""
        self.callback_f(event)

    @property
    def ready(self) -> bool:
        """Check if handler is ready."""
        return True


class WSGIKeystonePebbleHandler(sunbeam_chandlers.WSGIPebbleHandler):
    """Keystone Pebble Handler."""

    def init_service(self, context: sunbeam_core.OPSCharmContexts) -> None:
        """Enable and start WSGI service."""
        container = self.charm.unit.get_container(self.container_name)
        try:
            process = container.exec(["a2dissite", "keystone"], timeout=5 * 60)
            out, warnings = process.wait_output()
            if warnings:
                for line in warnings.splitlines():
                    logger.warning("a2dissite warn: %s", line.strip())
            logging.debug(f"Output from a2dissite: \n{out}")
        except ops.pebble.ExecError:
            logger.exception("Failed to disable keystone site in apache")
        super().init_service(context)


class KeystoneOperatorCharm(sunbeam_charm.OSBaseOperatorAPICharm):
    """Charm the service."""

    _state = StoredState()
    _authed = False
    service_name = "keystone"
    wsgi_admin_script = "/usr/bin/keystone-wsgi-admin"
    wsgi_public_script = "/usr/bin/keystone-wsgi-public"
    domain_config_dir = Path("/etc/keystone/domains")
    domain_ca_dir = Path("/usr/local/share/ca-certificates")
    service_port = 5000
    mandatory_relations = {"database", "ingress-public"}
    db_sync_cmds = [
        [
            "sudo",
            "-u",
            "keystone",
            "keystone-manage",
            "--config-dir",
            "/etc/keystone",
            "db_sync",
        ]
    ]
    IDSVC_RELATION_NAME = "identity-service"
    IDCREDS_RELATION_NAME = "identity-credentials"
    IDOPS_RELATION_NAME = "identity-ops"
    SEND_CA_CERT_RELATION_NAME = "send-ca-cert"

    def __init__(self, framework):
        # NOTE(gboutry): super().__init__ will call self.bootstrapped() which tries to
        # make use of the keystone_manager
        self.keystone_manager = manager.KeystoneManager(
            self, KEYSTONE_CONTAINER
        )
        super().__init__(framework)
        self._state.set_default(admin_domain_name="admin_domain")
        self._state.set_default(admin_domain_id=None)
        self._state.set_default(default_domain_id=None)
        self._state.set_default(service_project_id=None)

        self.certificate_transfer = CertificateTransferProvides(
            self, self.SEND_CA_CERT_RELATION_NAME
        )

        self.framework.observe(
            self.on.peers_relation_changed, self._on_peer_data_changed
        )
        self.framework.observe(
            self.on.send_ca_cert_relation_joined,
            self._handle_certificate_transfer_on_event,
        )
        self.framework.observe(
            self.on.get_admin_password_action, self._get_admin_password_action
        )
        self.framework.observe(
            self.on.get_admin_account_action, self._get_admin_account_action
        )
        self.framework.observe(
            self.on.get_service_account_action,
            self._get_service_account_action,
        )
        self.framework.observe(
            self.on.regenerate_password_action,
            self._regenerate_password_action,
        )
        self.framework.observe(
            self.on.add_ca_certs_action,
            self._add_ca_certs_action,
        )
        self.framework.observe(
            self.on.remove_ca_certs_action,
            self._remove_ca_certs_action,
        )
        self.framework.observe(
            self.on.list_ca_certs_action,
            self._list_ca_certs_action,
        )

    def _retrieve_or_set_secret(
        self,
        username: str,
        scope: dict = {},
        rotate: SecretRotate = SecretRotate.NEVER,
        add_suffix_to_username: bool = False,
    ) -> str:
        label = f"{CREDENTIALS_SECRET_PREFIX}{username}"
        credentials_id = self.peers.get_app_data(label)
        if credentials_id:
            if "relation" in scope:
                secret = self.model.get_secret(id=credentials_id)
                secret.grant(scope["relation"])
            return credentials_id

        password = pwgen.pwgen(12)
        if add_suffix_to_username:
            suffix = pwgen.pwgen(6)
            username = f"{username}-{suffix}"
        credentials_secret = self.model.app.add_secret(
            {"username": username, "password": password},
            label=label,
            rotate=rotate,
        )
        self.peers.set_app_data(
            {
                label: credentials_secret.id,
            }
        )
        if "relation" in scope:
            credentials_secret.grant(scope["relation"])

        return credentials_secret.id

    def _get_admin_password_action(self, event: ActionEvent) -> None:
        if not self.unit.is_leader():
            event.fail("Please run action on lead unit.")
            return
        event.set_results({"password": self.admin_password})

    def _get_admin_account_action(self, event: ActionEvent) -> None:
        """Get details for the admin account.

        This action handler will provide a full set of details
        to access the cloud using the admin account.
        """
        if not self.unit.is_leader():
            event.fail("Please run action on lead unit.")
            return
        openrc = f"""# openrc for access to OpenStack
export OS_AUTH_URL={self.public_endpoint}
export OS_USERNAME={self.admin_user}
export OS_PASSWORD={self.admin_password}
export OS_PROJECT_DOMAIN_NAME={self.admin_domain_name}
export OS_USER_DOMAIN_NAME={self.admin_domain_name}
export OS_PROJECT_NAME=admin
export OS_IDENTITY_API_VERSION=3
export OS_AUTH_VERSION=3
"""
        event.set_results(
            {
                "username": self.admin_user,
                "password": self.admin_password,
                "user-domain-name": self.admin_domain_name,
                "project-name": "admin",
                "project-domain-name": self.admin_domain_name,
                "region": self.model.config["region"],
                "internal-endpoint": self.internal_endpoint,
                "public-endpoint": self.public_endpoint,
                "api-version": 3,
                "openrc": openrc,
            }
        )

    def _get_service_account_action(self, event: ActionEvent) -> None:
        """Create/get details for a service account.

        This action handler will create a new services account
        for the provided username.  This account can be used
        to provide access to OpenStack services from outside
        of the Charmed deployment.
        """
        if not self.unit.is_leader():
            event.fail("Please run action on lead unit.")
            return

        # TODO: refactor into general helper method.
        username = event.params["username"]

        user_password = None
        try:
            credentials_id = self._retrieve_or_set_secret(username)
            credentials = self.model.get_secret(id=credentials_id)
            user_password = credentials.get_content(refresh=True).get(
                "password"
            )
        except SecretNotFoundError:
            logger.warning("Secret for {username} not found")

        service_domain = self.keystone_manager.ksclient.show_domain(
            name="service_domain"
        )
        service_project = self.keystone_manager.ksclient.show_project(
            name=self.service_project, domain=service_domain.get("name")
        )
        self.keystone_manager.create_service_account(
            username=username,
            password=user_password,
            project=service_project.get("name"),
            domain=service_domain.get("name"),
        )

        event.set_results(
            {
                "username": username,
                "password": user_password,
                "user-domain-name": service_domain.get("name"),
                "project-name": service_project.get("name"),
                "project-domain-name": service_domain.get("name"),
                "region": self.model.config["region"],
                "internal-endpoint": self.internal_endpoint,
                "public-endpoint": self.public_endpoint,
                "api-version": 3,
            }
        )

    def _regenerate_password_action(self, event: ActionEvent) -> None:
        """Regenerate password for a user account.

        This action handler will update the user account
        with a new password.
        """
        if not self.unit.is_leader():
            event.fail("Please run action on lead unit.")
            return

        username = event.params["username"]
        try:
            credentials_id = self._retrieve_or_set_secret(username)
            credentials = self.model.get_secret(id=credentials_id)
            password = pwgen.pwgen(12)
            self.keystone_manager.ksclient.update_user(
                user=username, password=password
            )
            credentials.set_content(
                {"username": username, "password": password}
            )
            event.set_results({"password": password})
        except SecretNotFoundError:
            event.fail(f"Secret for {username} not found")
        except Exception as e:
            event.fail(f"Regeneration of password failed: {e}")

    def _add_ca_certs_action(self, event: ActionEvent):
        """Distribute CA certs."""
        if not self.unit.is_leader():
            event.fail("Please run action on lead unit.")
            return

        name = event.params.get("name")
        ca = event.params.get("ca")
        chain = event.params.get("chain")
        ca_cert = None
        chain_certs = None

        try:
            ca_bytes = base64.b64decode(ca)
            ca_cert = ca_bytes.decode()
            if not certs.certificate_is_valid(ca_bytes):
                event.fail("Invalid CA certificate")
                return

            if chain:
                chain_bytes = base64.b64decode(chain)
                chain_certs = chain_bytes.decode()
                ca_chain_list = certs.parse_ca_chain(chain_certs)
                for _ca in ca_chain_list:
                    if not certs.certificate_is_valid(_ca.encode()):
                        event.fail("Invalid certificate in CA Chain")
                        return

                if not certs.ca_chain_is_valid(ca_chain_list):
                    event.fail("Invalid CA Chain")
        except (binascii.Error, TypeError, ValueError) as e:
            event.fail(str(e))
            return

        certificates_str = (
            self.peers.get_app_data(CERTIFICATE_TRANSFER_LABEL) or "{}"
        )
        certificates = json.loads(certificates_str)
        if name in certificates:
            event.fail("Certificate bundle already transferred")
            return False

        certificates[name] = {"ca": ca_cert, "chain": chain_certs}
        certificates_str = json.dumps(certificates)
        self.peers.set_app_data({CERTIFICATE_TRANSFER_LABEL: certificates_str})
        self._handle_certificate_transfers()

    def _remove_ca_certs_action(self, event: ActionEvent):
        """Remove CA certs."""
        if not self.unit.is_leader():
            event.fail("Please run action on lead unit.")
            return

        name = event.params.get("name")
        certificates_str = (
            self.peers.get_app_data(CERTIFICATE_TRANSFER_LABEL) or "{}"
        )
        certificates = json.loads(certificates_str)
        if name not in certificates:
            event.fail("Certificate bundle does not exist")
            return

        certificates.pop(name)
        certificates_str = json.dumps(certificates)
        self.peers.set_app_data({CERTIFICATE_TRANSFER_LABEL: certificates_str})
        self._handle_certificate_transfers()

    def _list_ca_certs_action(self, event: ActionEvent):
        """List CA certs."""
        if not self.unit.is_leader():
            event.fail("Please run action on lead unit.")
            return

        certificates_str = (
            self.peers.get_app_data(CERTIFICATE_TRANSFER_LABEL) or "{}"
        )
        certificates = json.loads(certificates_str)
        event.set_results(certificates)

    def _on_peer_data_changed(self, event: RelationChangedEvent):
        """Process fernet updates if possible."""
        if self._state.unit_bootstrapped and self.is_leader_ready():
            self.update_fernet_keys_from_peer()
        else:
            logger.debug(
                "Deferring _on_peer_data_changed event as node is not "
                "bootstrapped yet"
            )
            event.defer()
            return

    def update_fernet_keys_from_peer(self):
        """Check the peer data updates for updated fernet keys.

        Then we can pull the keys from the app data,
        and tell the local charm to write them to disk.
        """
        fernet_secret_id = self.peers.get_app_data("fernet-secret-id")
        if fernet_secret_id:
            fernet_secret = self.model.get_secret(id=fernet_secret_id)
            keys = fernet_secret.get_content(refresh=True)

            # Remove the prefix from keys retrieved from juju secrets
            # startswith can be replaced with removeprefix for python >= 3.9
            prefix_len = len(FERNET_KEYS_PREFIX)
            keys = {
                (k[prefix_len:] if k.startswith(FERNET_KEYS_PREFIX) else k): v
                for k, v in keys.items()
            }

            existing_keys = self.keystone_manager.read_keys(
                key_repository="/etc/keystone/fernet-keys"
            )
            if keys and keys != existing_keys:
                logger.info("Updating fernet keys")
                self.keystone_manager.write_keys(
                    key_repository="/etc/keystone/fernet-keys", keys=keys
                )

        credential_keys_secret_id = self.peers.get_app_data(
            "credential-keys-secret-id"
        )
        if credential_keys_secret_id:
            credential_keys_secret = self.model.get_secret(
                id=credential_keys_secret_id
            )
            keys = credential_keys_secret.get_content(refresh=True)

            # Remove the prefix from keys retrieved from juju secrets
            # startswith can be replaced with removeprefix for python >= 3.9
            prefix_len = len(FERNET_KEYS_PREFIX)
            keys = {
                (k[prefix_len:] if k.startswith(FERNET_KEYS_PREFIX) else k): v
                for k, v in keys.items()
            }

            existing_keys = self.keystone_manager.read_keys(
                key_repository="/etc/keystone/credential-keys"
            )
            if keys and keys != existing_keys:
                logger.info("Updating credential keys")
                self.keystone_manager.write_keys(
                    key_repository="/etc/keystone/credential-keys", keys=keys
                )

    def _on_secret_changed(self, event: ops.charm.SecretChangedEvent):
        logger.debug(
            f"secret-changed triggered for label {event.secret.label}"
        )
        if event.secret.label == "fernet-keys":
            keys = event.secret.get_content(refresh=True)
            prefix_len = len(FERNET_KEYS_PREFIX)
            keys = {
                (k[prefix_len:] if k.startswith(FERNET_KEYS_PREFIX) else k): v
                for k, v in keys.items()
            }
            existing_keys = self.keystone_manager.read_keys(
                key_repository="/etc/keystone/fernet-keys"
            )
            if keys and keys != existing_keys:
                logger.info("secret-change event: Updating the fernet keys")
                self.keystone_manager.write_keys(
                    key_repository="/etc/keystone/fernet-keys", keys=keys
                )
        elif event.secret.label == "credential-keys":
            keys = event.secret.get_content(refresh=True)
            prefix_len = len(FERNET_KEYS_PREFIX)
            keys = {
                (k[prefix_len:] if k.startswith(FERNET_KEYS_PREFIX) else k): v
                for k, v in keys.items()
            }
            existing_keys = self.keystone_manager.read_keys(
                key_repository="/etc/keystone/credential-keys"
            )
            if keys and keys != existing_keys:
                logger.info(
                    "secret-change event: Updating the credential keys"
                )
                self.keystone_manager.write_keys(
                    key_repository="/etc/keystone/credential-keys", keys=keys
                )
        else:
            # By default read the latest content of secret
            # this will allow juju to trigger secret-remove
            # event for old revision
            event.secret.get_content(refresh=True)

    def _on_secret_rotate(self, event: ops.charm.SecretRotateEvent):
        # All the juju secrets are created on leader unit, so return
        # if unit is not leader at this stage instead of checking at
        # each secret.
        logger.debug(f"secret-rotate triggered for label {event.secret.label}")
        if not self.unit.is_leader():
            logger.warning("Not leader, not rotating the fernet keys")
            return

        if event.secret.label == "fernet-keys":
            logger.info("secret-rotate event: Rotating the fernet keys")
            self.keystone_manager.rotate_fernet_keys()
            fernet_keys = self.keystone_manager.read_keys(
                key_repository="/etc/keystone/fernet-keys"
            )
            # Secret content keys should be at least 3 characters long,
            # no number to start, no dash to end
            # prepend fernet- to the key names
            fernet_keys_ = {
                f"{FERNET_KEYS_PREFIX}{k}": v for k, v in fernet_keys.items()
            }
            event.secret.set_content(fernet_keys_)
        elif event.secret.label == "credential-keys":
            logger.info("secret-rotate event: Rotating the credential keys")
            self.keystone_manager.rotate_credential_keys()
            fernet_keys = self.keystone_manager.read_keys(
                key_repository="/etc/keystone/credential-keys"
            )
            # Secret content keys should be at least 3 characters long,
            # no number to start, no dash to end
            # prepend fernet- to the key names
            fernet_keys_ = {
                f"{FERNET_KEYS_PREFIX}{k}": v for k, v in fernet_keys.items()
            }
            event.secret.set_content(fernet_keys_)
            return

        # Secret labels in identity-service relation are
        # based on remote app name. So generate labels for
        # all the remote apps related to identity-service.
        identity_labels = [
            f"{CREDENTIALS_SECRET_PREFIX}svc_{relation.app.name}"
            for relation in self.model.relations[self.IDSVC_RELATION_NAME]
        ]
        if event.secret.label in identity_labels:
            suffix = pwgen.pwgen(6)
            username = event.secret.label[
                event.secret.label.startswith(CREDENTIALS_SECRET_PREFIX)
                and len(CREDENTIALS_SECRET_PREFIX) :  # noqa: E203
            ]
            username = f"{username}-{suffix}"
            password = pwgen.pwgen(12)

            logger.info(f"Creating service account with username {username}")
            self.keystone_manager.create_service_account(username, password)
            olduser = event.secret.get_content(refresh=True).get("username")
            event.secret.set_content(
                {"username": username, "password": password}
            )
            old_service_users = self.peers.get_app_data("old_service_users")
            service_users_to_delete = (
                json.loads(old_service_users) if old_service_users else []
            )
            if olduser not in service_users_to_delete:
                service_users_to_delete.append(olduser)
                self.peers.set_app_data(
                    {"old_service_users": json.dumps(service_users_to_delete)}
                )

    def _on_secret_remove(self, event: ops.charm.SecretRemoveEvent):
        logger.info(f"secret-remove triggered for label {event.secret.label}")
        if (
            event.secret.label == "fernet-keys"
            or event.secret.label == "credential-keys"
            or event.secret.label
            == f"{CREDENTIALS_SECRET_PREFIX}{self.admin_user}"
            or event.secret.label
            == f"{CREDENTIALS_SECRET_PREFIX}{self.charm_user}"
        ):
            # TODO: Remove older revisions of the secret
            # event.secret.remove_revision(event.revision)
            return

        # Secret labels in identity-service relation are
        # based on remote app name. So generate labels for
        # all the remote apps related to identity-service.
        identity_labels = [
            f"{CREDENTIALS_SECRET_PREFIX}svc_{relation.app.name}"
            for relation in self.model.relations[self.IDSVC_RELATION_NAME]
        ]
        if event.secret.label in identity_labels:
            deleted_users = []
            old_service_users = self.peers.get_app_data("old_service_users")
            service_users_to_delete = (
                json.loads(old_service_users) if old_service_users else []
            )
            for user in service_users_to_delete:
                # Only delete users created during rotation of event.secret
                if f"{CREDENTIALS_SECRET_PREFIX}{user}".startswith(
                    event.secret.label
                ):
                    logger.info(f"Deleting user {user} from keystone")
                    self.keystone_manager.ksclient.delete_user(user)
                    deleted_users.append(user)
            service_users_to_delete = [
                x for x in service_users_to_delete if x not in deleted_users
            ]
            self.peers.set_app_data(
                {"old_service_users": json.dumps(service_users_to_delete)}
            )

    def get_pebble_handlers(self) -> List[sunbeam_chandlers.PebbleHandler]:
        """Pebble handlers for the service."""
        return [
            WSGIKeystonePebbleHandler(
                self,
                self.service_name,
                self.service_name,
                self.container_configs,
                self.template_dir,
                self.configure_charm,
                f"wsgi-{self.service_name}",
            )
        ]

    def get_relation_handlers(
        self, handlers=None
    ) -> List[sunbeam_rhandlers.RelationHandler]:
        """Relation handlers for the service."""
        handlers = handlers or []
        if self.can_add_handler(self.IDSVC_RELATION_NAME, handlers):
            self.id_svc = IdentityServiceProvidesHandler(
                self,
                self.IDSVC_RELATION_NAME,
                self.register_service_from_event,
            )
            handlers.append(self.id_svc)

        if self.can_add_handler(self.IDCREDS_RELATION_NAME, handlers):
            self.cc_svc = IdentityCredentialsProvidesHandler(
                self,
                self.IDCREDS_RELATION_NAME,
                self.add_credentials_from_event,
            )
            handlers.append(self.cc_svc)

        if self.can_add_handler(self.IDOPS_RELATION_NAME, handlers):
            self.ops_svc = IdentityResourceProvidesHandler(
                self,
                self.IDOPS_RELATION_NAME,
                self.handle_ops_from_event,
            )
            handlers.append(self.ops_svc)

        if self.can_add_handler("domain-config", handlers):
            self.dc = DomainConfigHandler(
                self,
                "domain-config",
                self.configure_charm,
            )
            handlers.append(self.dc)

        return super().get_relation_handlers(handlers)

    @property
    def config_contexts(self) -> List[sunbeam_contexts.ConfigContext]:
        """Configuration adapters for the operator."""
        contexts = super().config_contexts
        contexts.extend(
            [
                KeystoneConfigAdapter(self, "ks_config"),
                KeystoneLoggingAdapter(self, "ks_logging"),
            ]
        )
        return contexts

    @property
    def container_configs(self):
        """Container configs for keystone."""
        _cconfigs = [
            sunbeam_core.ContainerConfigFile(
                self.service_conf,
                "root",
                self.service_group,
                0o640,
            ),
            sunbeam_core.ContainerConfigFile(
                LOGGING_CONF,
                "root",
                self.service_group,
                0o640,
            ),
        ]
        return _cconfigs

    def can_service_requests(self) -> bool:
        """Check if unit can process client requests."""
        if self.bootstrapped() and self.unit.is_leader():
            logger.debug("Can service client requests")
            return True
        else:
            logger.debug(
                "Cannot service client requests. "
                "Bootstrapped: {} Leader {}".format(
                    self.bootstrapped(), self.unit.is_leader()
                )
            )
            return False

    def check_outstanding_identity_service_requests(self) -> None:
        """Check requests from identity service relation."""
        for relation in self.framework.model.relations[
            self.IDSVC_RELATION_NAME
        ]:
            app_data = relation.data[relation.app]
            if relation.data[self.app].get(
                "service-credentials"
            ) and relation.data[self.app].get("admin-role"):
                logger.debug(
                    "Identity service request already processed for "
                    f"{relation.app.name} {relation.name}/{relation.id}"
                )
            else:
                if app_data.get("service-endpoints"):
                    logger.debug(
                        "Processing register service request for "
                        f"{relation.app.name} {relation.name}/{relation.id}"
                    )
                    self.register_service(
                        relation.id,
                        relation.name,
                        json.loads(app_data["service-endpoints"]),
                        app_data["region"],
                        relation.app.name,
                    )
                else:
                    logger.debug(
                        "Cannot process client request, 'service-endpoints' "
                        "not supplied"
                    )

    def check_outstanding_identity_credentials_requests(
        self, ignore_processed: bool = True
    ) -> None:
        """Check requests from identity credentials relation.

        If ignore_processed flag is False, process identtiy credentials on all the connected
        relations even if its already processed.
        """
        for relation in self.framework.model.relations[
            self.IDCREDS_RELATION_NAME
        ]:
            app_data = relation.data[relation.app]
            if (
                ignore_processed
                and relation.data[self.app].get("credentials")
                and relation.data[self.app].get("admin-role")
            ):
                logger.debug(
                    "Credential request already processed for "
                    f"{relation.app.name} {relation.name}/{relation.id}"
                )
            else:
                if app_data.get("username"):
                    logger.debug(
                        "Processing credentials request from "
                        f"{relation.app.name} {relation.name}/{relation.id}"
                    )
                    self.add_credentials(
                        relation.id, relation.name, app_data["username"]
                    )
                else:
                    logger.debug(
                        "Cannot process client request, 'username' not "
                        "supplied"
                    )

    def remove_old_domains(
        self, domain_configs: dict, container: ops.model.Container
    ) -> List[str]:
        """Remove domain files from domains no longer related."""
        active_domains = [c["domain-name"] for c in domain_configs]
        removed_domains = []
        for domain_file in container.list_files(self.domain_config_dir):
            domain_on_disk = domain_file.name.split(".")[1]
            if domain_on_disk in active_domains:
                logger.debug("Keeping {}".format(domain_file.name))
            else:
                container.remove_path(domain_file.path)
                removed_domains.append(domain_on_disk)
        for domain_file in container.list_files(self.domain_ca_dir):
            domain_on_disk = domain_file.name.split(".")[1]
            if domain_on_disk in active_domains:
                logger.debug("Keeping CA {}".format(domain_file.name))
            else:
                container.remove_path(domain_file.path)
        return removed_domains

    def update_domain_config(
        self, domain_configs: dict, container: ops.model.Container
    ) -> List[str]:
        """Update domain configuration."""
        updated_domains = []
        for domain_config in domain_configs:
            domain_name = domain_config["domain-name"]
            domain = self.keystone_manager.ksclient.get_domain_object(
                domain_name
            )
            if not domain:
                self.keystone_manager.ksclient.create_domain(name=domain_name)
            domain_config_file = (
                self.domain_config_dir / f"keystone.{domain_name}.conf"
            )
            domain_ca_file = self.domain_ca_dir / f"keystone.{domain_name}.crt"
            try:
                original_contents = container.pull(domain_config_file).read()
            except (ops.pebble.PathError, FileNotFoundError):
                original_contents = None
            if original_contents != domain_config["config-contents"]:
                container.push(
                    domain_config_file,
                    domain_config["config-contents"],
                    **{
                        "user": "keystone",
                        "group": "keystone",
                        "permissions": 0o600,
                    },
                )
                updated_domains.append(domain_name)
            if domain_config.get("ca"):
                try:
                    original_contents = container.pull(domain_ca_file).read()
                except (ops.pebble.PathError, FileNotFoundError):
                    original_contents = None
                if original_contents != domain_config["ca"]:
                    container.push(
                        domain_ca_file,
                        domain_config["ca"],
                        **{
                            "user": "keystone",
                            "group": "keystone",
                            "permissions": 0o644,
                        },
                    )
                    updated_domains.append(domain_name)

        return updated_domains

    def configure_domains(self, event: ops.framework.EventBase = None) -> None:
        """Configure LDAP backed domains."""
        if isinstance(event, sunbeam_dc_svc.DomainConfigGoneAwayEvent):
            exclude = [event.relation]
        else:
            exclude = []
        container = self.unit.get_container(KEYSTONE_CONTAINER)
        for d in [self.domain_config_dir, self.domain_ca_dir]:
            if not container.isdir(d):
                container.make_dir(d, make_parents=True)
        domain_configs = self.dc.get_domain_configs(exclude=exclude)
        removed_domains = self.remove_old_domains(domain_configs, container)
        updated_domains = self.update_domain_config(domain_configs, container)
        if removed_domains or updated_domains:
            ph = self.get_named_pebble_handler(KEYSTONE_CONTAINER)
            ph.start_all(restart=True)

    def check_outstanding_identity_ops_requests(self) -> None:
        """Check requests from identity ops relation."""
        for relation in self.framework.model.relations[
            self.IDOPS_RELATION_NAME
        ]:
            app_data = relation.data[relation.app]
            request = {}
            response = {}
            if app_data.get("request"):
                request = json.loads(app_data.get("request"))
            if relation.data[self.app].get("response"):
                response = json.loads(relation.data[self.app].get("response"))

            request_id = request.get("id")
            if request_id != response.get("id"):
                logger.debug(
                    "Processing identity ops request from"
                    f"{relation.app.name} {relation.name}/{relation.id}"
                    f" for request id {request_id}"
                )
                self.handle_op_request(relation.id, relation.name, request)

    def check_outstanding_requests(self) -> bool:
        """Process any outstanding client requests."""
        logger.debug("Checking for outstanding client requests")
        if not self.can_service_requests():
            return

        self.check_outstanding_identity_service_requests()
        self.check_outstanding_identity_credentials_requests()
        self.check_outstanding_identity_ops_requests()

    def register_service_from_event(self, event):
        """Process service request event.

        NOTE: The event will not be deferred. If it cannot be processed now
              then it will be picked up by `check_outstanding_requests`
        """
        if self.can_service_requests():
            self.register_service(
                event.relation_id,
                event.relation_name,
                event.service_endpoints,
                event.region,
                event.client_app_name,
            )

    def register_service(
        self,
        relation_id: str,
        relation_name: str,
        service_endpoints: str,
        region: str,
        client_app_name: str,
    ):
        """Register service in keystone."""
        logger.debug(f"Registering service requested by {client_app_name}")
        relation = self.model.get_relation(relation_name, relation_id)
        binding = self.framework.model.get_binding(relation)
        ingress_address = str(binding.network.ingress_address)

        service_domain = self.keystone_manager.ksclient.show_domain(
            name="service_domain"
        )
        admin_domain = self.keystone_manager.ksclient.show_domain(
            name="admin_domain"
        )
        service_project = self.keystone_manager.ksclient.show_project(
            name=self.service_project, domain=service_domain.get("name")
        )
        admin_project = self.keystone_manager.ksclient.show_project(
            name="admin", domain=admin_domain.get("name")
        )
        admin_user = self.keystone_manager.ksclient.show_user(
            name=self.model.config["admin-user"],
            domain=admin_domain.get("name"),
            project=admin_project.get("name"),
            project_domain=admin_domain.get("name"),
        )

        for ep_data in service_endpoints:
            service_username = "svc_{}".format(
                client_app_name.replace("-", "_")
            )
            event_relation = self.model.get_relation(
                relation_name, relation_id
            )
            scope = {"relation": event_relation}
            service_credentials = None
            service_password = None
            try:
                service_credentials = self._retrieve_or_set_secret(
                    service_username,
                    scope=scope,
                    rotate=SecretRotate.MONTHLY,
                    add_suffix_to_username=True,
                )
                credentials = self.model.get_secret(id=service_credentials)
                credentials = credentials.get_content(refresh=True)
                service_username = credentials.get("username")
                service_password = credentials.get("password")
            except SecretNotFoundError:
                logger.warning(f"Secret for {service_username} not found")

            service_user = self.keystone_manager.create_service_account(
                username=service_username,
                password=service_password,
                project=service_project.get("name"),
                domain=service_domain.get("name"),
            )

            service = self.keystone_manager.ksclient.create_service(
                name=ep_data["service_name"],
                service_type=ep_data["type"],
                description=ep_data["description"],
                may_exist=True,
            )
            for interface in ["admin", "internal", "public"]:
                self.keystone_manager.ksclient.create_endpoint(
                    service=service,
                    interface=interface,
                    url=ep_data[f"{interface}_url"],
                    region=region,
                    may_exist=True,
                )
            self.id_svc.interface.set_identity_service_credentials(
                relation_name,
                relation_id,
                "v3",
                ingress_address,
                self.default_public_ingress_port,
                "http",
                ingress_address,
                self.default_public_ingress_port,
                "http",
                ingress_address,
                self.default_public_ingress_port,
                "http",
                admin_domain,
                admin_project,
                admin_user,
                service_domain,
                service_project,
                service_user,
                self.internal_endpoint,
                self.admin_endpoint,
                self.public_endpoint,
                service_credentials,
                self.admin_role,
            )

    def add_credentials_from_event(self, event):
        """Process service request event.

        NOTE: The event will not be deferred. If it cannot be processed now
              then it will be picked up by `check_outstanding_requests`
        """
        if self.can_service_requests():
            self.add_credentials(
                event.relation_id, event.relation_name, event.username
            )

    def add_credentials(
        self, relation_id: str, relation_name: str, username: str
    ):
        """Add credentials from user defined in event.

        :param event:
        :return:
        """
        logger.debug("Processing credentials request")
        relation = self.model.get_relation(relation_name, relation_id)
        binding = self.framework.model.get_binding(relation)
        ingress_address = str(binding.network.ingress_address)
        event_relation = self.model.get_relation(relation_name, relation_id)
        scope = {"relation": event_relation}
        user_password = None
        try:
            credentials_id = self._retrieve_or_set_secret(username, scope)
            credentials = self.model.get_secret(id=credentials_id)
            user_password = credentials.get_content(refresh=True).get(
                "password"
            )
        except SecretNotFoundError:
            logger.warning(f"Secret for {username} not found")

        service_domain = self.keystone_manager.ksclient.show_domain(
            name="service_domain"
        )
        service_project = self.keystone_manager.ksclient.show_project(
            name=self.service_project, domain=service_domain.get("name")
        )
        self.keystone_manager.create_service_account(
            username=username,
            password=user_password,
            project=service_project.get("name"),
            domain=service_domain.get("name"),
        )

        self.cc_svc.interface.set_identity_credentials(
            relation_name=relation_name,
            relation_id=relation_id,
            api_version="3",
            auth_host=ingress_address,
            auth_port=self.default_public_ingress_port,
            auth_protocol="http",
            internal_host=ingress_address,  # XXX(wolsen) internal address?
            internal_port=self.default_public_ingress_port,
            internal_protocol="http",
            credentials=credentials_id,
            project_name=service_project.get("name"),
            project_id=service_project.get("id"),
            user_domain_name=service_domain.get("name"),
            user_domain_id=service_domain.get("id"),
            project_domain_name=service_domain.get("name"),
            project_domain_id=service_domain.get("id"),
            region=self.model.config["region"],  # XXX(wolsen) region matters?
            admin_role=self.admin_role,
        )

    @property
    def default_public_ingress_port(self):
        """Default public ingress port."""
        return 5000

    @property
    def default_domain_id(self):
        """Default domain id."""
        return self._state.default_domain_id

    @property
    def admin_domain_name(self):
        """Admin domain name."""
        return self._state.admin_domain_name

    @property
    def admin_domain_id(self):
        """Admin domain ID."""
        return self._state.admin_domain_id

    @property
    def admin_password(self) -> str:
        """Retrieve the password for the Admin user."""
        try:
            credentials_id = self._retrieve_or_set_secret(self.admin_user)
            credentials = self.model.get_secret(id=credentials_id)
            return credentials.get_content(refresh=True).get("password")
        except SecretNotFoundError:
            logger.warning("Secret for admin credentials not found")

        return None

    @property
    def admin_user(self):
        """Admin User."""
        return self.model.config["admin-user"]

    @property
    def admin_role(self):
        """Admin role."""
        return self.model.config["admin-role"]

    @property
    def charm_user(self):
        """The admin user specific to the charm.

        This is a special admin user reserved for the charm to interact with
        keystone.
        """
        return "_charm-keystone-admin"

    @property
    def charm_password(self) -> str:
        """The password for the charm admin user."""
        try:
            credentials_id = self._retrieve_or_set_secret(self.charm_user)
            credentials = self.model.get_secret(id=credentials_id)
            return credentials.get_content(refresh=True).get("password")
        except SecretNotFoundError:
            logger.warning("Secret for charm credentials not found")

        return None

    @property
    def service_project(self):
        """Service project name."""
        return self.model.config["service-tenant"]

    @property
    def service_project_id(self):
        """Service project id."""
        return self._state.service_project_id

    @property
    def admin_endpoint(self):
        """Admin endpoint for keystone api."""
        admin_hostname = self.model.get_binding(
            self.IDSVC_RELATION_NAME
        ).network.ingress_address
        return f"http://{admin_hostname}:{self.service_port}/v3"

    @property
    def internal_endpoint(self):
        """Internal endpoint for keystone api."""
        if self.ingress_internal and self.ingress_internal.url:
            return self.ingress_internal.url + "/v3"

        internal_hostname = self.model.get_binding(
            self.IDSVC_RELATION_NAME
        ).network.ingress_address
        return f"http://{internal_hostname}:{self.service_port}/v3"

    @property
    def public_endpoint(self):
        """Public endpoint for keystone api."""
        if self.ingress_public and self.ingress_public.url:
            return self.ingress_public.url + "/v3"

        address = self.public_ingress_address
        if not address:
            address = self.model.get_binding(
                self.IDSVC_RELATION_NAME
            ).network.ingress_address
        return f"http://{address}:{self.service_port}/v3"

    @property
    def healthcheck_http_url(self) -> str:
        """Healthcheck HTTP URL for the service."""
        return f"http://localhost:{self.default_public_ingress_port}/v3"

    def _create_fernet_secret(self) -> None:
        """Create fernet juju secret.

        Create a fernet juju secret if peer relation app data
        does not contain a fernet secret. This function might
        re-trigger until bootstrap is successful. So check if
        fernet secret is already created and update juju fernet
        secret with existing fernet keys on the unit.
        """
        # max_keys = max(self.model.config['fernet-max-active-keys'], 3)
        # exp = self.model.config['token-expiration']
        # exp_window = self.model.config['allow-expired-window']
        # rotation_seconds = (exp + exp_window) / (max_keys - 2)

        fernet_secret_id = self.peers.get_app_data("fernet-secret-id")

        existing_keys = self.keystone_manager.read_keys(
            key_repository="/etc/keystone/fernet-keys"
        )
        # Secret content keys should be at least 3 characters long,
        # no number to start, no dash to end
        # prepend fernet- to the key names
        # existing_keys_ will be in format {'fernet-{filename}: data', ...}
        existing_keys_ = {f"fernet-{k}": v for k, v in existing_keys.items()}

        # juju secret already created, update content with the fernet
        # keys on the unit if necessary.
        if fernet_secret_id:
            fernet_secret = self.model.get_secret(id=fernet_secret_id)
            keys = fernet_secret.get_content(refresh=True)
            if keys and keys != existing_keys_:
                logger.info("Updating Fernet juju secret")
                fernet_secret.set_content(existing_keys_)
        else:
            # If fernet secret does not exist in peer relation data,
            # create a new one
            # Fernet keys are rotated on daily basis considering 1 hour
            # as token expiration, 47 hours as allow-expired-window and
            # fernet-max-active-keys to 4.
            fernet_secret = self.model.app.add_secret(
                existing_keys_,
                label="fernet-keys",
                rotate=SecretRotate("daily"),
            )
            logger.info(f"Fernet keys secret created: {fernet_secret.id}")
            self.peers.set_app_data({"fernet-secret-id": fernet_secret.id})
            return

    def _create_credential_keys_secret(self) -> None:
        """Create credential_keys juju secret.

        Create a credential_keys juju secret if peer relation app
        data does not contain a credential_keys secret. This function
        might re-trigger until bootstrap is successful. So check if
        the secret is already created and update juju credential_keys
        secret with existing fernet keys on the unit.
        """
        credential_keys_secret_id = self.peers.get_app_data(
            "credential-keys-secret-id"
        )

        existing_keys = self.keystone_manager.read_keys(
            key_repository="/etc/keystone/credential-keys"
        )
        # Secret content keys should be at least 3 characters long,
        # no number to start, no dash to end
        # prepend fernet- to the key names
        # existing_keys_ will be in format {'fernet-{filename}: data', ...}
        existing_keys_ = {f"fernet-{k}": v for k, v in existing_keys.items()}

        # juju secret already created, update content with the fernet
        # keys on the unit if necessary.
        if credential_keys_secret_id:
            credential_keys_secret = self.model.get_secret(
                id=credential_keys_secret_id
            )
            keys = credential_keys_secret.get_content(refresh=True)
            if keys and keys != existing_keys_:
                logger.info("Updating Credential keys juju secret")
                credential_keys_secret.set_content(existing_keys_)
        else:
            # If credential_keys secret does not exist in peer relation data,
            # create a new one
            credential_keys_secret = self.model.app.add_secret(
                existing_keys_,
                label="credential-keys",
                rotate=SecretRotate("monthly"),
            )
            logger.info(
                f"Credential keys secret created: {credential_keys_secret.id}"
            )
            self.peers.set_app_data(
                {"credential-keys-secret-id": credential_keys_secret.id}
            )

    @sunbeam_job_ctrl.run_once_per_unit("keystone_bootstrap")
    def keystone_bootstrap(self) -> bool:
        """Starts the appropriate services in the order they are needed.

        If the service has not yet been bootstrapped, then this will
         1. Create the database
         2. Bootstrap the keystone users service
         3. Setup the fernet tokens
        """
        if self.unit.is_leader():
            try:
                self.keystone_manager.setup_keystone()
            except (ops.pebble.ExecError, ops.pebble.ConnectionError):
                raise sunbeam_guard.BlockedExceptionError(
                    "Failed to bootstrap"
                )

            try:
                self._create_fernet_secret()
                self._create_credential_keys_secret()
            except (ops.pebble.ExecError, ops.pebble.ConnectionError) as error:
                logger.exception(error)
                raise sunbeam_guard.BlockedExceptionError(
                    "Failed to create fernet keys"
                )

            try:
                self.keystone_manager.setup_initial_projects_and_users()
            except Exception:
                # keystone might fail with Internal server error, not
                # sure of exact exceptions to be caught. List below that
                # are observed:
                # keystoneauth1.exceptions.connection.ConnectFailure
                raise sunbeam_guard.BlockedExceptionError(
                    "Failed to setup projects and users"
                )
        self.unit.status = MaintenanceStatus("Starting Keystone")

    def configure_app_leader(self, event):
        """Configure the lead unit."""
        self.keystone_bootstrap()
        self.set_leader_ready()
        self.check_outstanding_requests()

    def unit_fernet_bootstrapped(self) -> bool:
        """Check if fernet tokens have been setup."""
        try:
            existing_keys = self.keystone_manager.read_keys(
                key_repository="/etc/keystone/fernet-keys"
            )
        except AttributeError:
            return False
        except ops.pebble.ConnectionError as e:
            logger.debug(
                "Pebble is not ready, cannot check fernet keys, reason: %s",
                e,
            )
            return False

        if existing_keys:
            logger.debug("Keys found")
            return True
        else:
            logger.debug("Keys not found")
            return False

    def configure_unit(self, event: ops.framework.EventBase) -> None:
        """Run configuration on this unit."""
        self.check_leader_ready()
        self.check_relation_handlers_ready(event)
        self.open_ports()
        self.init_container_services()
        self.check_pebble_handlers_ready()
        self.run_db_sync()
        pre_update_fernet_ready = self.unit_fernet_bootstrapped()
        self.update_fernet_keys_from_peer()
        # If the wsgi service was running with no tokens it will be in a
        # wedged state so restart it.
        if self.unit_fernet_bootstrapped() and not pre_update_fernet_ready:
            container = self.unit.get_container(self.wsgi_container_name)
            container.stop("wsgi-keystone")
            container.start("wsgi-keystone")
        self.configure_domains(event)
        self._state.unit_bootstrapped = True

    def bootstrapped(self) -> bool:
        """Determine whether the service has been bootstrapped."""
        return super().bootstrapped() and self.unit_fernet_bootstrapped()

    def _ingress_changed(self, event: ops.framework.EventBase) -> None:
        """Ingress changed callback.

        Invoked when the data on the ingress relation has changed. This will
        update the keystone endpoints, and then call the configure_charm.
        """
        logger.debug("Received an ingress_changed event")
        if self.bootstrapped():
            self.keystone_manager.update_service_catalog_for_keystone()

        if self.can_service_requests():
            self.check_outstanding_identity_credentials_requests(
                ignore_processed=False
            )
        self.configure_charm(event)

    def _sanitize_secrets(self, request: dict) -> dict:
        """Sanitize any secrets.

        Look for any secrets in op parameters and retrieve the secret value.
        Use the same parameter name while retrieving value from secret.
        """
        for op in request.get("ops", []):
            for param, value in op.get("params", {}).items():
                if isinstance(value, str) and value.startswith(SECRET_PREFIX):
                    try:
                        credentials = self.model.get_secret(id=value)
                        op["params"][param] = credentials.get_content(
                            refresh=True
                        ).get(param)
                    except (ModelError, SecretNotFoundError) as e:
                        logger.debug(
                            f"Not able to retrieve secret {value}: {str(e)}"
                        )

        return request

    def handle_ops_from_event(self, event):
        """Process ops request event."""
        logger.debug("Handle ops from event")
        if not self.can_service_requests():
            logger.debug(
                "handle_ops_from_event: Service not ready, request "
                f"{event.request} not processed"
            )
            return

        request = json.loads(event.request)
        self.handle_op_request(
            event.relation_id, event.relation_name, request=request
        )

    def handle_op_request(
        self, relation_id: str, relation_name: str, request: dict
    ):
        """Process op request."""
        response = {}
        response["id"] = request.get("id")
        response["tag"] = request.get("tag")
        response["ops"] = [
            {"name": op.get("name"), "return-code": -2, "value": None}
            for op in request.get("ops", [])
        ]
        context = defaultdict(list)

        request = self._sanitize_secrets(request)
        for idx, op in enumerate(request.get("ops", [])):
            func_name = op.get("name")
            try:
                func = getattr(self.keystone_manager.ksclient, func_name)
                params = op.get("params", {})
                computed_params = params.copy()
                for key, value in params.items():
                    if isinstance(value, str):
                        templated_value = jinja2.Template(value).render(
                            context
                        )
                        logger.debug(
                            f"handle_op_request: {value} templated to {templated_value}"
                        )
                        computed_params[key] = templated_value
                result = func(**computed_params)
                response["ops"][idx]["return-code"] = 0
                response["ops"][idx]["value"] = result
            except Exception as e:
                response["ops"][idx]["return-code"] = -1
                response["ops"][idx]["value"] = str(e)
            context[func_name].append(response["ops"][idx]["value"])

        logger.debug(f"handle_op_request: Sending response {response}")
        self.ops_svc.interface.set_ops_response(
            relation_id, relation_name, ops_response=response
        )

    def _get_combined_ca_and_chain(self) -> (str, list):
        """Combine all certs for CA and chain.

        Action add-ca-certs allows to add multiple CA cert and chain certs.
        Combine all CA certs in the secret and chains in the secret.
        """
        certificates = self.peers.get_app_data(CERTIFICATE_TRANSFER_LABEL)
        if not certificates:
            logger.debug("No certificates to transfer")
            return "", []

        ca_list = []
        chain_list = []
        certificates = json.loads(certificates)
        for name, bundle in certificates.items():
            _ca = bundle.get("ca")
            _chain = bundle.get("chain")
            if _ca:
                ca_list.append(_ca)
            if _chain:
                chain_list.append(_chain)

        ca = "\n".join(ca_list)
        # chain sent as list of single string containing complete chain
        chain = []
        if chain:
            chain = ["\n".join(chain_list)]

        return ca, chain

    def _handle_certificate_transfers(
        self, relations: List[Relation] | None = None
    ):
        """Transfer certs on given relations.

        If relation is not specified, send on all the send-ca-cert
        relations.
        """
        if not relations:
            relations = [
                relation
                for relation in self.framework.model.relations[
                    self.SEND_CA_CERT_RELATION_NAME
                ]
            ]

        ca, chain = self._get_combined_ca_and_chain()

        for relation in relations:
            logger.debug(
                "Transferring certificates for relation "
                f"{relation.app.name} {relation.name}/{relation.id}"
            )
            self.certificate_transfer.set_certificate(
                certificate="",
                ca=ca,
                chain=chain,
                relation_id=relation.id,
            )

    def _handle_certificate_transfer_on_event(self, event):
        if not self.unit.is_leader():
            logger.debug("Skipping send ca cert as unit is not leader.")
            return

        logger.debug(f"Handling send ca cert event: {event}")
        self._handle_certificate_transfers([event.relation])


if __name__ == "__main__":
    main(KeystoneOperatorCharm)
