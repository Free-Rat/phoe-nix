locals {
  name_prefix = "${var.project_name}-${var.environment}"

  tags = {
    project     = var.project_name
    environment = var.environment
    managed_by  = "terraform"
  }

  cosmosdb_account_name = "cosmos-${local.name_prefix}"
}