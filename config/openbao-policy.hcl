# OpenBao policy for the physical-router test automation.
#
# Read-only, test-router credentials only. It deliberately has NO access to
# secret/fleet/mt6000-* or the production gateway's T14 key.
path "secret/data/fleet/test-routers-*" {
  capabilities = ["read"]
}

path "secret/metadata/fleet/test-routers-*" {
  capabilities = ["read", "list"]
}

# The automation may read the fleet public-key list (public keys only).
path "secret/data/fleet/ssh/authorized" {
  capabilities = ["read"]
}

path "secret/metadata/fleet/ssh" {
  capabilities = ["read", "list"]
}
