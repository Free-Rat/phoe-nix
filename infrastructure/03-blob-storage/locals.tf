locals {
  name_prefix = "${var.project_name}-${var.environment}"

  tags = {
    project     = var.project_name
    environment = var.environment
    managed_by  = "terraform"
  }

  storage_account_name = replace("st${var.project_name}${var.environment}", "-", "")
}