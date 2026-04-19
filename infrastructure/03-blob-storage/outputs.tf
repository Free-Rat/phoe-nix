output "logs_storage_account_name" {
  description = "Name of the logs storage account"
  value       = azurerm_storage_account.logs.name
}

output "logs_storage_account_id" {
  description = "ID of the logs storage account"
  value       = azurerm_storage_account.logs.id
}

output "logs_container_name" {
  description = "Name of the logs blob container"
  value       = azurerm_storage_container.logs.name
}