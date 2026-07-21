# Pioneer Security Reinstatement (Ansible)

This directory implements the phased security reinstatement plan for:

- `pioneer-1` (`192.168.2.11`)
- `pioneer-2` (`192.168.2.10`)
- `pioneer-3` (`192.168.2.133`)

## Quick Start

```bash
cd deploy/ansible/pioneer_security
ansible-galaxy collection install -r requirements.yml
cp group_vars/all/vault.example.yml group_vars/all/vault.yml
ansible-vault encrypt group_vars/all/vault.yml
```

Update `group_vars/all/main.yml`:

- Replace `aiops_public_key` with the real public key.
- Confirm LAN CIDR, host IPs, and port settings.
- Pin `presidio_*_image` to explicit tags or digests for production.
- Keep `node_exporter_sha256` aligned with `node_exporter_version`.
- `pioneer_require_image_pinning` defaults to `true` and will fail playbook 06
  if Presidio images are unpinned (`:latest`).

Run phase playbooks in order:

```bash
ansible-playbook 01-base-hardening.yml --ask-vault-pass
ansible-playbook 02-mtls.yml --ask-vault-pass
ansible-playbook 03-nfs.yml --ask-vault-pass
ansible-playbook 04-jwt.yml --ask-vault-pass
ansible-playbook 05-audit-db.yml --ask-vault-pass
ansible-playbook 06-pii-redaction.yml --ask-vault-pass
ansible-playbook 07-vault.yml --ask-vault-pass
ansible-playbook 08-monitoring.yml --ask-vault-pass
ansible-playbook 09-ai-serving.yml --ask-vault-pass
ansible-playbook 10-ai-training.yml --ask-vault-pass
```

`04-jwt.yml` also deploys the centralized authz policy file to
`/etc/pioneer/authz_policy.yml`.

## Idempotency

Each playbook is designed to be rerun safely.

- Package installs use `state: present`.
- Files are managed declaratively (`template`, `copy`, `lineinfile`, `mount`).
- Certificate and secret generation use `creates` guards.
- Services are managed with explicit `enabled` and `state`.

## Validation Checklist

- SSH password login denied; key-only access works for `aiops`.
- `ufw status verbose` only allows expected LAN ports.
- mTLS certs present in `/etc/pioneer/ssl` on every node.
- `/etc/pioneer/jwt_secret` exists on each node with `0600` permissions.
- NFS share mounts at `/mnt/pioneer_cluster` on `pioneer-1` and `pioneer-3`.
- `pioneer_audit.audit_log` has `previous_hash` and `current_hash` populated.
- Presidio services are running on `pioneer-1` (`127.0.0.1:5001`, `127.0.0.1:5002`).
- Vault service is active on `pioneer-2` and initialized/unsealed.
- Node exporter serves HTTPS with client cert validation on port `9100`.
- Ollama serves locally on `pioneer-1` (`127.0.0.1:11434`) for ops copilot inference.
- Curated dataset and retrieval index are generated under `/mnt/pioneer_cluster/data`.
- Nightly checkpoint runs appear in `/mnt/pioneer_cluster/models/checkpoints`.
- `current` serving symlink in `/mnt/pioneer_cluster/models/ops-copilot/serving` points to a promoted checkpoint.

## Notes

- `07-vault.yml` installs Vault in production mode (not `-dev`).
- Keep Vault unseal keys and root token out of source control.
- Prometheus scrape snippet for security lane is written to:
  `/etc/prometheus/file_sd/pioneer-node-exporter-security.yml`
