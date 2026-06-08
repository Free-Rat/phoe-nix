data "azurerm_resource_group" "main" {
  name = "rg-${var.project_name}-${var.environment}"
}

resource "azurerm_cosmosdb_account" "main" {
  name                = local.cosmosdb_account_name
  location            = var.location
  resource_group_name = data.azurerm_resource_group.main.name
  offer_type          = "Standard"
  kind                = "GlobalDocumentDB"

  dynamic "capabilities" {
    for_each = var.cosmosdb_offer_type == "Serverless" ? [1] : []
    content {
      name = "EnableServerless"
    }
  }

  consistency_policy {
    consistency_level = "Session"
  }

  geo_location {
    location          = var.location
    failover_priority = 0
  }

  tags = local.tags
}

resource "azurerm_cosmosdb_sql_database" "main" {
  name                = "project-healer"
  resource_group_name = data.azurerm_resource_group.main.name
  account_name        = azurerm_cosmosdb_account.main.name
}

resource "azurerm_cosmosdb_sql_container" "observations" {
  name                  = "observations"
  resource_group_name   = data.azurerm_resource_group.main.name
  account_name          = azurerm_cosmosdb_account.main.name
  database_name         = azurerm_cosmosdb_sql_database.main.name
  partition_key_paths   = ["/node_id"]
  partition_key_version = 2
}

resource "azurerm_cosmosdb_sql_container" "node_state_current" {
  name                  = "node-state-current"
  resource_group_name   = data.azurerm_resource_group.main.name
  account_name          = azurerm_cosmosdb_account.main.name
  database_name         = azurerm_cosmosdb_sql_database.main.name
  partition_key_paths   = ["/node_id"]
  partition_key_version = 2
}

resource "azurerm_cosmosdb_sql_container" "decisions" {
  name                  = "decisions"
  resource_group_name   = data.azurerm_resource_group.main.name
  account_name          = azurerm_cosmosdb_account.main.name
  database_name         = azurerm_cosmosdb_sql_database.main.name
  partition_key_paths   = ["/node_id"]
  partition_key_version = 2
}

resource "azurerm_cosmosdb_sql_container" "execution_results" {
  name                  = "execution-results"
  resource_group_name   = data.azurerm_resource_group.main.name
  account_name          = azurerm_cosmosdb_account.main.name
  database_name         = azurerm_cosmosdb_sql_database.main.name
  partition_key_paths   = ["/node_id"]
  partition_key_version = 2
}

resource "azurerm_cosmosdb_sql_container" "config_snapshots" {
  name                  = "config-snapshots"
  resource_group_name   = data.azurerm_resource_group.main.name
  account_name          = azurerm_cosmosdb_account.main.name
  database_name         = azurerm_cosmosdb_sql_database.main.name
  partition_key_paths   = ["/node_id"]
  partition_key_version = 2
}

resource "azurerm_cosmosdb_sql_container" "repair_traces" {
  name                  = "repair-traces"
  resource_group_name   = data.azurerm_resource_group.main.name
  account_name          = azurerm_cosmosdb_account.main.name
  database_name         = azurerm_cosmosdb_sql_database.main.name
  partition_key_paths   = ["/node_id"]
  partition_key_version = 2
}

resource "azurerm_cosmosdb_sql_container" "service_status" {
  name                  = "service-status"
  resource_group_name   = data.azurerm_resource_group.main.name
  account_name          = azurerm_cosmosdb_account.main.name
  database_name         = azurerm_cosmosdb_sql_database.main.name
  partition_key_paths   = ["/node_id"]
  partition_key_version = 2
}
