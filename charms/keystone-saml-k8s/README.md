# keystone-saml-k8s

This charm allows conveying necessary SAML2 settings to the keystone charm, in order for keystone to create it's SAML2 identity provider configuration.

## Deployment

```bash
juju deploy keystone-saml-k8s keystone-saml-entra
juju config keystone-saml-entra \
    name="entra" \
    label="Log in with Entra SAML2" \
    metadata-url="https://login.microsoftonline.com/{YOUR_TENANT}/federationmetadata/2007-06/federationmetadata.xml?appid={YOUR_APP_ID}"
```

Integrate with keystone:

```bash
juju relate keystone-saml-entra:keystone-saml keystone:keystone-saml
```