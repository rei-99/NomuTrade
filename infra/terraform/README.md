# STP platform — Terraform (AWS flavor)

Reference deployment for design decision **D-06** (see
[`docs/design/18-devops-deployment.md`](../../docs/design/18-devops-deployment.md)):
one cloud VM per environment running the `docker-compose.yml` stack, optional
managed PostgreSQL 15, and object storage for generated report files.

This is the **AWS flavor**. The cloud provider is unconfirmed (SRS TBD-11), so
the Terraform is deliberately provider-portable (constraint **C-03**): the
*shape* of the stack — network, one VM, managed PostgreSQL, object storage,
narrow security rules — is the same on any cloud; only the provider block and
resource types differ.

## What gets created

- VPC `10.0.0.0/16` with two public subnets (two AZs), internet gateway, routes.
- Security group: `80`/`443`/`8080`/`22` from `var.allowed_cidr` only; all egress.
  (`8080` because the compose `web` service maps host `8080` → nginx `80`;
  `80`/`443` are reserved for TLS termination, see design doc 17.)
- EC2 instance (default `t3.medium`, Amazon Linux 2023) whose `user_data`
  installs Docker + the compose plugin, clones `var.repo_url` into `/opt/stp`,
  writes `.env` (`POSTGRES_PASSWORD`) and runs `docker compose up -d --build`.
- Optional RDS PostgreSQL 15 `db.t3.micro` (`var.enable_rds = true`), reachable
  only from the app VM's security group. With RDS enabled, `user_data` also
  writes a `docker-compose.override.yml` pointing `api` at the RDS endpoint
  (the compose `db` container still starts but is unused).
- S3 bucket for report files: versioning enabled, SSE-AES256, all public access
  blocked. Name includes the account ID for global uniqueness.

## Usage

```bash
cd infra/terraform
cp env/dev.tfvars.example env/dev.tfvars   # fill in real values
terraform init
terraform plan  -var-file=env/dev.tfvars -var="db_password=$TF_VAR_db_password"
terraform apply -var-file=env/dev.tfvars -var="db_password=$TF_VAR_db_password"
terraform output app_url
```

`db_password` is `sensitive` and has no default — never commit it; pass it via
`TF_VAR_db_password` or `-var`. `env/*.tfvars` (but not `*.example`) and
`.terraform/` are covered by the repository `.gitignore`.

## Variables

| Name | Default | Notes |
|---|---|---|
| `aws_region` | `ap-northeast-1` | Tokyo; matches the JPY-equities demo data |
| `environment` | `dev` | Name/tag prefix; use one workspace or tfvars set per env |
| `instance_type` | `t3.medium` | Sized for the full compose stack (**not** free-tier) |
| `ssh_key_name` | `null` | EC2 key-pair name; optional |
| `allowed_cidr` | *(required)* | CIDR allowed on 22/80/443/8080 — your egress IP |
| `db_password` | *(required, sensitive)* | Compose db password; RDS master password when RDS enabled |
| `repo_url` | *(required)* | Git URL cloned by the VM and started with compose |
| `enable_rds` | `false` | `false` = compose `db` container; `true` = managed RDS |

## Azure parity (C-03)

The same deployment expressed in Azure terms — identical architecture, only
provider-specific resource types change:

| AWS (this flavor) | Azure equivalent |
|---|---|
| EC2 instance (`aws_instance`) | Azure VM (`azurerm_linux_virtual_machine`, cloud-init instead of `user_data`) |
| RDS PostgreSQL (`aws_db_instance`) | Azure Database for PostgreSQL — Flexible Server (`azurerm_postgresql_flexible_server`) |
| S3 bucket (`aws_s3_bucket`) | Blob Storage container in a Storage Account (`azurerm_storage_account` + `azurerm_storage_container`, versioning + encryption at rest) |
| Security group (`aws_security_group`) | Network Security Group (`azurerm_network_security_group`) |
| VPC + subnets (`aws_vpc`, `aws_subnet`) | Virtual network + subnets (`azurerm_virtual_network`, `azurerm_subnet`) |

Everything else — the compose stack on the VM, the environment variables, the
`.env` contract (`POSTGRES_PASSWORD`), the app URL shape — is unchanged, which
is what the C-03 portability constraint actually requires.

## Caveats

- **Not yet applied.** No Terraform or cloud access was available on the dev
  machine, so these files are statically reviewed (balanced braces, consistent
  references) but have never been through `terraform fmt`/`validate`/`apply`.
  Run `terraform fmt -check` and `terraform validate` before first use.
- **`db_password` in `user_data`.** EC2 user-data is readable by anyone with
  `DescribeInstanceAttribute`. Acceptable for the training MVP; for real use,
  fetch secrets at boot from a secrets manager (the platform's secret-provider
  abstraction, C-09, is the seam for this). Also avoid shell-significant
  characters (`'`, `$`, backtick) in the password — it is interpolated into the
  bootstrap script.
- **Single-AZ, single VM.** NFR-AVL-004 (multi-AZ) is consciously deferred
  (D-06); the stateless services keep a later move to ECS/AKS mechanical.
- **Cost.** `t3.medium` and `db.t3.micro` incur charges; destroy the workspace
  when the demo is done.
