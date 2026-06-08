locals {
  name_prefix = "${var.project_name}-${var.environment}"

  tags = {
    project     = var.project_name
    environment = var.environment
    managed_by  = "terraform"
  }

  function_app_name         = "func-${local.name_prefix}"
  servicebus_ns_name        = "sb-${local.name_prefix}-${substr(replace(data.azurerm_client_config.current.tenant_id, "-", ""), 0, 6)}"
  cosmosdb_account_name     = "cosmos-${local.name_prefix}"
  keyvault_name             = substr("kv${replace(var.project_name, "-", "")}${var.environment}${substr(replace(data.azurerm_client_config.current.tenant_id, "-", ""), 0, 6)}", 0, 24)
  appinsights_name          = "appi-${local.name_prefix}"
  app_plan_name             = "plan-${local.name_prefix}"
  logs_storage_account_name = replace("st${var.project_name}${var.environment}", "-", "")
  func_storage_account_name = replace("stfunc${var.project_name}${var.environment}", "-", "")
}