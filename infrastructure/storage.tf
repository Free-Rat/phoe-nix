resource "azurerm_storage_account" "logs" {
  name                            = local.storage_account_name
  resource_group_name             = azurerm_resource_group.main.name
  location                        = azurerm_resource_group.main.location
  account_tier                    = "Standard"
  account_replication_type        = "LRS"
  min_tls_version                 = "TLS1_2"
  allow_nested_items_to_be_public = false

  blob_properties {
    versioning_enabled = false

    delete_retention_policy {
      days = var.log_retention_days
    }
  }

  tags = local.tags
}

resource "azurerm_storage_container" "logs" {
  name                  = "logs"
  storage_account_name  = azurerm_storage_account.logs.name
  container_access_type = "private"
}

resource "azurerm_storage_management_policy" "log_cleanup" {
  storage_account_id = azurerm_storage_account.logs.id

  rule {
    name    = "cleanup-old-logs"
    enabled = true

    filters {
      prefix_match = ["logs/"]
      blob_types   = ["blockBlob"]
    }

    actions {
      base_blob {
        delete_after_days_since_modification_greater_than = var.log_retention_days
      }

      snapshot {
        delete_after_days_since_creation_greater_than = var.log_retention_days
      }
    }
  }
}