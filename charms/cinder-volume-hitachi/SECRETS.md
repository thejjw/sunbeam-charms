# Juju Secrets Configuration for Hitachi VSP Charm

This charm uses Juju secrets to securely manage sensitive credentials for Hitachi VSP storage arrays.

## Required Secrets

### Array Credentials (Required)
The primary storage array credentials are required for all deployments:

```bash
# Create the secret
juju add-secret array-creds \
  username=storage-admin \
  password=your-secure-password
juju grant-secret array-creds cinder-volume-hitachi
# Configure the charm
juju config cinder-volume-hitachi \
  san-credentials-secret=secret:d25n5snmp25c76m87ij0
```

## Optional Secrets

### CHAP Authentication (iSCSI only)
If using iSCSI with CHAP authentication:

```bash
# Create CHAP credentials secret
juju add-secret chap-creds \
  username=chap-user \
  password=chap-secret
juju grant-secret chap-creds cinder-volume-hitachi
# Configure the charm
juju config cinder-volume-hitachi \
  use-chap-auth=true \
  chap-credentials-secret=secret:chap-creds
```

### Replication Credentials (Optional)
For replication features with secondary storage systems:

```bash
# Mirror CHAP credentials (for GAD replication)
juju add-secret mirror-chap-creds \
  username=mirror-chap-user \
  password=mirror-chap-secret

# Mirror REST API credentials (for secondary array management)
juju add-secret mirror-rest-creds \
  username=rest-admin \
  password=rest-password

# Configure the charm
juju config cinder-volume-hitachi \
  hitachi-mirror-chap-credentials-secret=secret:mirror-chap-creds \
  hitachi-mirror-rest-credentials-secret=secret:mirror-rest-creds
```

## Complete Deployment Example

```bash
# 1. Create required array credentials
juju add-secret array-creds \
  username=storage-admin \
  password=array-password
juju grant-secret array-creds cinder-volume-hitachi
# 2. Deploy the charm
juju deploy cinder-volume-hitachi

# 3. Configure basic settings
juju config cinder-volume-hitachi \
  san-ip=192.168.1.100 \
  san-credentials-secret=secret:d25n5snmp25c76m87ij0 \
  hitachi-storage-id=450000 \
  hitachi-pools=pool1,pool2 \
  protocol=FC

# 4. Integrate with cinder-volume
juju integrate cinder-volume-hitachi cinder-volume
```

## Secret Management

### Updating Credentials
To rotate credentials, update the secret content:

```bash
# Update array credentials
juju update-secret array-creds \
  username=new-admin \
  password=new-password

# The charm will automatically use the new credentials
```

### Viewing Secret Information
```bash
# List all secrets
juju secrets

# Show secret metadata (not content)
juju show-secret array-creds

# Grant access to view secret content
juju grant-secret array-creds cinder-volume-hitachi
```

## Security Benefits

- **Secure Storage**: Credentials stored in Juju's encrypted secret store
- **Access Control**: Secrets can be granted/revoked per application
- **Rotation**: Easy credential rotation without charm reconfiguration
- **Audit Trail**: Secret access and modifications are logged
- **No Plain Text**: Credentials never appear in configuration or logs

## Troubleshooting

### Secret Access Errors
If you see "Failed to retrieve credentials from secret":

1. Verify the secret exists: `juju secrets`
2. Check secret content: `juju show-secret <secret-name> --reveal`
3. Ensure the secret contains 'username' and 'password' keys
4. Grant access if needed: `juju grant-secret <secret-name> cinder-volume-hitachi`

### Required Keys
All credential secrets must contain exactly these keys:
- `username` - The username for authentication
- `password` - The password for authentication

Example of correct secret content:
```bash
juju add-secret my-creds username=admin password=secret123
# ✅ Correct - contains both required keys

juju add-secret bad-creds user=admin pass=secret123  
# ❌ Wrong - uses 'user' and 'pass' instead of 'username' and 'password'
```
