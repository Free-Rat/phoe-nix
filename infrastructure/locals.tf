locals {
  name_prefix = "${var.project_name}-${var.environment}"

  tags = {
    project     = var.project_name
    environment = var.environment
    managed_by  = "terraform"
  }

  storage_account_name  = replace("st${var.project_name}${var.environment}", "-", "")
  function_app_name     = "func-${local.name_prefix}"
  servicebus_ns_name    = "sb-${local.name_prefix}"
  cosmosdb_account_name = "cosmos-${local.name_prefix}"
  keyvault_name         = "kv-${local.name_prefix}"
  appinsights_name      = "appi-${local.name_prefix}"
  app_plan_name         = "plan-${local.name_prefix}"

  topics = {
    analysis = {
      max_size_megabytes = 1024
    }
    decision = {
      max_size_megabytes = 1024
    }
  }
}