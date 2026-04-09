#!/bin/bash

# This script encrypts a secret and writes it to zuul.d/secrets.yaml.
# It wraps zuul-client encrypt and handles inserting or replacing secrets
# in the secrets file.
#
# Usage:
#   ./encrypt_secret.sh --secret-name <name> --field-name <field> --infile <path>
#
# Examples:
#   ./encrypt_secret.sh --secret-name utb-ssh-key --field-name value --infile ~/.ssh/id_rsa
#   ./encrypt_secret.sh --secret-name utb-launchpad --field-name value --infile launchpad-creds.json

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SECRETS_FILE="${SCRIPT_DIR}/zuul.d/secrets.yaml"

ZUUL_URL="https://zuul.opendev.org"
TENANT="openstack"
PROJECT="opendev.org/openstack/sunbeam-charms"
PUBKEY_URL="${ZUUL_URL}/api/tenant/${TENANT}/key/${PROJECT}.pub"
PUBKEY_FILE="sunbeam-charms.pub"

SECRET_NAME=""
FIELD_NAME="value"
INFILE=""
GENERATED_COMMENT=""

usage() {
    echo "Usage: $0 --secret-name <name> --field-name <field> --infile <path> [--generated <comment>]"
    echo
    echo "Options:"
    echo "  --secret-name  Name of the Zuul secret (e.g. utb-ssh-key)"
    echo "  --field-name   Field name within the secret (default: value)"
    echo "  --infile       Path to the file containing the secret data"
    echo "  --generated    Optional comment added above the encrypted data (e.g. TTL info)"
    exit 1
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --secret-name) SECRET_NAME="$2"; shift 2 ;;
        --field-name)  FIELD_NAME="$2"; shift 2 ;;
        --infile)      INFILE="$2"; shift 2 ;;
        --generated)   GENERATED_COMMENT="$2"; shift 2 ;;
        -h|--help)     usage ;;
        *)             echo "Unknown option: $1"; usage ;;
    esac
done

if [[ -z "$SECRET_NAME" || -z "$INFILE" ]]; then
    echo "Error: --secret-name and --infile are required"
    usage
fi

if [[ ! -f "$INFILE" ]]; then
    echo "Error: input file '$INFILE' not found"
    exit 1
fi

if ! command -v zuul-client &> /dev/null; then
    echo "zuul-client could not be found"
    echo "Install it with: pip install zuul-client"
    exit 1
fi

# Fetch project public key if not already present
if [[ ! -f "$PUBKEY_FILE" ]]; then
    echo "Fetching Zuul public key..."
    curl -s "$PUBKEY_URL" -o "$PUBKEY_FILE"
fi

OUTFILE=$(mktemp)
trap 'rm -f "$OUTFILE"' EXIT

echo "Encrypting secret '${SECRET_NAME}' from '${INFILE}'..."

zuul-client --zuul-url "$ZUUL_URL" encrypt \
    --public-key "$PUBKEY_FILE" \
    --tenant "$TENANT" \
    --project "$PROJECT" \
    --secret-name "$SECRET_NAME" \
    --field-name "$FIELD_NAME" \
    --infile "$INFILE" \
    --outfile "$OUTFILE"

# Inject the generated comment into zuul-client output if requested.
# zuul-client output format:
#   line 1: - secret:
#   line 2:     name: <name>
#   line 3:     data:
#   line 4+:      <field>: !encrypted/pkcs1-oaep ...
if [[ -n "$GENERATED_COMMENT" ]]; then
    sed -i "3 a\\      # Generated on $(date --iso-8601=seconds --utc) ${GENERATED_COMMENT}" "$OUTFILE"
fi

# Remove existing secret block with the same name (if any),
# then append the new one.
# Each secret block starts with "- secret:" and ends before the next "- secret:" or EOF.
if [[ -f "$SECRETS_FILE" ]] && grep -q "name: ${SECRET_NAME}" "$SECRETS_FILE"; then
    echo "Replacing existing secret '${SECRET_NAME}' in ${SECRETS_FILE}..."
    TMPFILE=$(mktemp)
    awk -v name="$SECRET_NAME" '
        /^- secret:/ { in_block=1; hold=$0; next }
        in_block && /^    name:/ {
            if ($0 ~ "name: " name "$") {
                skip=1; in_block=0; next
            } else {
                print hold; skip=0; in_block=0
            }
        }
        in_block { hold=hold ORS $0; next }
        skip && /^- secret:/ { skip=0 }
        skip { next }
        { print }
    ' "$SECRETS_FILE" > "$TMPFILE"
    mv "$TMPFILE" "$SECRETS_FILE"
fi

# Append the new secret block
if [[ -s "$SECRETS_FILE" ]] && [[ "$(tail -c 1 "$SECRETS_FILE")" != "" ]]; then
    echo "" >> "$SECRETS_FILE"
fi
cat "$OUTFILE" >> "$SECRETS_FILE"

echo "Secret '${SECRET_NAME}' written to ${SECRETS_FILE}"
