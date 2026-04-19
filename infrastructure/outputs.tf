output "resource_group_name" {
  description = "Name of the resource group"
  value       = azurerm_resource_group.main.name
}

output "storage_account_name" {
  description = "Name of the logs storage account"
  value       = azurerm_storage_account.logs.name
}

output "logs_container_name" {
  description = "Name of the logs blob container"
  value       = azurerm_storage_container.logs.name
}

output "servicebus_namespace_name" {
  description = "Name of the Service Bus namespace"
  value       = azurerm_servicebus_namespace.main.name
}

output "servicebus_connection_string" {
  description = "Service Bus connection string (Shared Access Policy)"
  value       = azurerm_servicebus_namespace_authorization_rule.shared_access.primary_connection_string
  sensitive   = true
}

output "cosmosdb_endpoint" {
  description = "Cosmos DB account endpoint"
  value       = azurerm_cosmosdb_account.main.endpoint
}

output "cosmosdb_database_name" {
  description = "Cosmos DB database name"
  value       = azurerm_cosmosdb_sql_database.main.name
}

output "key_vault_name" {
  description = "Name of the Key Vault"
  value       = azurerm_key_vault.main.name
}

output "function_app_names" {
  description = "Names of all Function Apps"
  value = {
    token    = azurerm_linux_function_app.token.name
    router   = azurerm_linux_function_app.router.name
    analysis = azurerm_linux_function_app.analysis.name
    decision = azurerm_linux_function_app.decision.name
  }
}

output "managed_identity_principal_id" {
  description = "Principal ID of the user-assigned managed identity"
  value       = azurerm_user_assigned_identity.func.principal_id
}

output "application_insights_connection_string" {
  description = "Application Insights connection string"
  value       = azurerm_application_insights.main.connection_string
  sensitive   = true
}