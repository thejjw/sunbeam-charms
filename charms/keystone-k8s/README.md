# keystone-k8s

## Description

keystone-k8s is an operator to manage the Keystone identity service
on a Kubernetes based environment.

## Usage

### Deployment

keystone-k8s is deployed using below command:

    juju deploy keystone-k8s keystone --trust

Now connect the keystone operator to an existing database.

    juju relate mysql:database keystone:database

### Configuration

This section covers common and/or important configuration options. See file
`config.yaml` for the full list of options, along with their descriptions and
default values. See the [Juju documentation][juju-docs-config-apps] for details
on configuring applications.

### Actions

This section covers Juju [actions][juju-docs-actions] supported by the charm.
Actions allow specific operations to be performed on a per-unit basis. To
display action descriptions run `juju actions keystone`. If the charm is not
deployed then see file `actions.yaml`.

## Relations

The charm supports the following relations. They are primarily of use to
developers:

* `identity-credentials`: Used by charms to obtain Keystone credentials without
  creating a service catalogue entry. Set 'username' only on the relation and
  Keystone will set defaults and return authentication details. Possible
  relation settings:

  * `username`: Username to be created.
  * `project`: Project (tenant) name to be created. Defaults to service's
               project.
  * `domain`: Keystone v3 domain the user will be created in. Defaults to the
              Default domain.

* `identity-service`: Used by API endpoints to request an entry in the Keystone
  service catalogue and the endpoint template catalogue.

  When a relation is established Keystone receives the following data from the
  requesting API endpoint:

  * `service_name`
  * `region`
  * `public_url`
  * `admin_url`
  * `internal_url`

  Keystone verifies that the requested service is supported (the list of
  supported services should remain updated). The following will occur for a
  supported service:

  1. an entry in the service catalogue is created
  1. an endpoint template is created
  1. an admin token is generated.

  The API endpoint receives the token and is informed of the ports that
  Keystone is listening on.

## Federation

The charm supports integrating Keystone with an external identity provider that implements OpenID Connect. There are three relations that facilitate this integration:

* `oauth`: Used by keystone to request OAuth credentials and endpoint information from a charm that provides OpenID.
* `receive-ca-cert`: This relation is optional, but can be used to receive CA certificates needed to trust the OpenID Connect provider. In the context of [Canonical Identity Platform](https://charmhub.io/topics/canonical-identity-platform), this relation is used to receive the CA certificate from the certificate authority charm deployed as part of the platform.
* `trusted-dashboard`: Used to integrate a dashboard such as [Horizon](https://charmhub.io/horizon-k8s) into the OpenID Connect flow, allowing users to select between keystone credentials and OpenID Connect credentials when logging in.

### Requirements for Federation

Before you enable federation, you will need:

* [TLS enabled](https://canonical-openstack.readthedocs-hosted.com/en/latest/how-to/features/tls-ca/) in your sunbeam deployment. Most IDPs will refuze to redirect back to a non-TLS URL.
* An OpenID connect provider that exposez the `oauth` provider relation

To make thigs easy, you can deploy the [Canonical Identity Platform](https://charmhub.io/topics/canonical-identity-platform) which provides all the required components to enable federation with OpenID Connect.

    # Please consult the Canonical Identity Platform documentation linked above,
    # for up to date instructions on how to deploy the platform.
    juju add-model iam
    juju deploy identity-platform --trust

Create an offer for `hydra` and `self-signed-certificates`:

    juju offer iam.hydra:oauth
    juju offer iam.self-signed-certificates:send-ca-cert

Get offer URLs:

    HYDRA_OFFER=$(juju list-offers --format=json -m iam hydra | jq -r '.hydra.["offer-url"]')
    SEND_CA_CERT_OFFER=$(
      juju list-offers \
      --format=json \
      -m iam \
      self-signed-certificates | jq -r '.["self-signed-certificates"].["offer-url"]')

Consume the offers in the keystone model:

    juju consume -m openstack $HYDRA_OFFER canonical-identity-platform
    juju consume -m openstack $SEND_CA_CERT_OFFER iam-certs

### Enable Federation

To enable federation, we need to create the needed integrations:

    juju relate keystone:oauth canonical-identity-platform:oauth
    juju relate keystone:receive-ca-cert iam-certs:send-ca-cert

Enable federation in the Horizon dashboard:

    juju relate keystone:trusted-dashboard horizon:trusted-dashboard

IDPs configured in keystone need to have a name and a protocol. The charm will use the remote application name as the IDP name. In the above examplem we consumed hydra from the IAM model with the name `canonical-identity-platform`. The provider ID will be identical to the application name. The protocol for any `oauth` relation will always be `openid`.

You will need this information when configuring the identity provider using the openstack command.

### Configure the provider in openstack

To configure the provider in openstack we need a few pieces of information:

* The issuer URL of the OpenID provider. For public OIDC providers such as Google, Okta, you can find this information in their documentation. For the Canonical Identity Platform, we can fetch the issuer URL from the relation data available to keystone after the relation is established. Details bellow.
* The provider name. In our case it is `canonical-identity-platform`.
* The protocol. For OIDC, this is always `openid`.
* Rules that dictate how to map the OIDC claims to OpenStack roles.

To get the issuer URL, run the following command:

    ISSUER_URL=$(juju show-unit -m openstack keystone/0 | yq -r '(.["keystone/0"].["relation-info"].[] | select(.endpoint == "oauth")).["application-data"].issuer_url')

Create a domain which we want to associate with the provider:

    openstack domain create \
        --description "Domain for OIDC federation" \
        canonical

Now we can create the provider in OpenStack:

    openstack identity provider create \
        --remote-id $ISSUER_URL \
        --domain canonical \
        canonical-identity-platform

Create a mapping rules:

    cat > rules.json <<EOF
    [
        {
            "local": [
                {
                    "user": {
                        "name": "{0}"
                    },
                    "group": {
                        "domain": {
                            "name": "canonical"
                        },
                        "name": "federated_users"
                    }
                }
            ],
            "remote": [
                {
                    "type": "REMOTE_USER"
                }
            ]
        }
    ]
    EOF

NOTE: The above rules assume that the OpenID provider will return the username in the `REMOTE_USER` claim. You may need to adjust the rules based on the claims returned by your OpenID provider.

Create the mapping:

    openstack mapping create \
        --rules rules.json canonical_openid

Create a group for federated users:

    openstack group create federated_users \
        --domain canonical

Create a project for the provider:

    openstack project create \
        --domain canonical \
        federated_project

Add a default role for the group:

    openstack role add \
        --group federated_users \
        --project federated_project \
        --group-domain canonical \
        --project-domain canonical \
        member

Create a protocol:

    openstack federation protocol create \
        --identity-provider canonical-identity-platform \
        --mapping canonical_openid \
        openid

You should now be able to log into the Horizon dashboard using the new OpenID connect provider.

### Configuring the CLI for OpenID Connect

OpenID connect has a number of authentication flows. The configuration of the CLI depends on the flows your IDP supports. The openstack CLI has a number of [build in plugins](https://docs.openstack.org/keystoneauth/latest/plugin-options.html#built-in-plugins) which cover some of the flows.

In this example, we're using hydra. We will first check which grant are supported by hydra, then pick one of the supported types and configure the CLI to use it. This method can be used against any OpenID provider.

Get supported grant types:

    $ curl -k -s  $ISSUER_URL/.well-known/openid-configuration | jq .grant_types_supported
    [
      "authorization_code",
      "implicit",
      "client_credentials",
      "refresh_token"
    ]

Out of the supported grant types we will use `client_credentials` as that doesn't require us to fetch a token from a browser. The `client_credentials` flow is a machine-to-machine flow, so it is suitable for CLI usage. To get client credentials from hydra we can run the following action:

    CLIENT_CREDS=$(juju run hydra/0 create-oauth-client \
        grant-types='["authorization_code", "client_credentials"]' \
        response-types='["id_token", "code", "token"]' \
        scope='["openid", "email", "profile"]')
    export CLIENT_ID=$(echo $CLIENT_CREDS | yq -r '.["client-id"]')
    export CLIENT_SECRET=$(echo $CLIENT_CREDS | yq -r '.["client-secret"]')

Now we can generate the openrc file to use with the OpenID provider:

    cat > openrc-idp <<EOF
    # You will need to set this to your keystone URL.
    export OS_AUTH_URL=http://172.16.1.204/openstack-keystone/v3
    export OS_PROJECT_NAME=federated_project
    export OS_PROJECT_DOMAIN_NAME=canonical
    export OS_AUTH_VERSION=3
    export OS_IDENTITY_API_VERSION=3

    export OS_AUTH_TYPE=v3oidcclientcredentials
    export OS_DISCOVERY_ENDPOINT="$ISSUER_URL/.well-known/openid-configuration"
    export OS_OPENID_SCOPE="openid email profile"
    export OS_CLIENT_ID="$CLIENT_ID"
    export OS_CLIENT_SECRET="$CLIENT_SECRET"
    export OS_IDENTITY_PROVIDER=canonical-identity-platform
    export OS_PROTOCOL=openid
    EOF

You can now source the `openrc-idp` file and use the OpenID provider with the OpenStack CLI:

    source openrc-idp
    openstack catalog list

## OCI Images

The charm by default uses `ghcr.io/canonical/keystone:2025.1-24.04_edge` image.

## Contributing

Please see the [Juju SDK docs](https://juju.is/docs/sdk) for guidelines
on enhancements to this charm following best practice guidelines, and
[CONTRIBUTING.md](contributors-guide) for developer guidance.

## Bugs

Please report bugs on [Launchpad][lp-bugs-charm-keystone-k8s].

<!-- LINKS -->

[juju-docs-actions]: https://jaas.ai/docs/actions
[juju-docs-config-apps]: https://juju.is/docs/configuring-applications
[lp-bugs-charm-keystone-k8s]: https://bugs.launchpad.net/charm-keystone-k8s/+filebug
