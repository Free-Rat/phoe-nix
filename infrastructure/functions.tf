resource "azurerm_user_assigned_identity" "func" {
  name                = "id-${local.name_prefix}-func"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name

  tags = local.tags
}

resource "azurerm_service_plan" "main" {
  name                = local.app_plan_name
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  os_type             = "Linux"
  sku_name            = "Y1"

  tags = local.tags
}

resource "azurerm_storage_account" "func" {
  name                            = replace("stfunc${var.project_name}${var.environment}", "-", "")
  resource_group_name             = azurerm_resource_group.main.name
  location                        = azurerm_resource_group.main.location
  account_tier                    = "Standard"
  account_replication_type        = "LRS"
  allow_nested_items_to_be_public = false

  tags = local.tags
}

resource "azurerm_linux_function_app" "token" {
  name                = "${local.function_app_name}-token"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  service_plan_id     = azurerm_service_plan.main.id

  storage_account_name                       = azurerm_storage_account.func.name
  storage_uses_managed_identity              = true

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.func.id]
  }

  app_settings = {
    STORAGE_ACCOUNT_NAME                  = azurerm_storage_account.logs.name
    LOGS_CONTAINER_NAME                   = azurerm_storage_container.logs.name
    SERVICEBUS_CONNECTION                 = "@Microsoft.KeyVault(SecretUri=${azurerm_key_vault_secret.servicebus_connection.versionless_id})"
    APPLICATIONINSIGHTS_CONNECTION_STRING = azurerm_application_insights.main.connection_string
    KEYVAULT_NAME                         = azurerm_key_vault.main.name
    OPENCODE_API_KEY_SECRET               = azurerm_key_vault_secret.opencode_api_key.name
  }

  site_config {
    application_insights_connection_string = azurerm_application_insights.main.connection_string
    application_insights_key               = azurerm_application_insights.main.instrumentation_key

    application_stack {
      python_version = "3.11"
    }
  }

  tags = local.tags
}

resource "azurerm_linux_function_app" "router" {
  name                = "${local.function_app_name}-router"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  service_plan_id     = azurerm_service_plan.main.id

  storage_account_name                       = azurerm_storage_account.func.name
  storage_uses_managed_identity              = true

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.func.id]
  }

  app_settings = {
    SERVICEBUS_CONNECTION                 = "@Microsoft.KeyVault(SecretUri=${azurerm_key_vault_secret.servicebus_connection.versionless_id})"
    SERVICEBUS_TOPIC_ANALYSIS_NAME        = azurerm_servicebus_topic.analysis.name
    APPLICATIONINSIGHTS_CONNECTION_STRING = azurerm_application_insights.main.connection_string
    KEYVAULT_NAME                         = azurerm_key_vault.main.name
  }

  site_config {
    application_insights_connection_string = azurerm_application_insights.main.connection_string
    application_insights_key               = azurerm_application_insights.main.instrumentation_key

    application_stack {
      python_version = "3.11"
    }
  }

  tags = local.tags
}

resource "azurerm_linux_function_app" "analysis" {
  name                = "${local.function_app_name}-analysis"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  service_plan_id     = azurerm_service_plan.main.id

  storage_account_name                       = azurerm_storage_account.func.name
  storage_uses_managed_identity              = true

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.func.id]
  }

  app_settings = {
    SERVICEBUS_CONNECTION                 = "@Microsoft.KeyVault(SecretUri=${azurerm_key_vault_secret.servicebus_connection.versionless_id})"
    SERVICEBUS_TOPIC_ANALYSIS_NAME        = azurerm_servicebus_topic.analysis.name
    SERVICEBUS_TOPIC_DECISION_NAME        = azurerm_servicebus_topic.decision.name
    KEYVAULT_NAME                         = azurerm_key_vault.main.name
    OPENCODE_API_KEY_SECRET               = azurerm_key_vault_secret.opencode_api_key.name
    APPLICATIONINSIGHTS_CONNECTION_STRING = azurerm_application_insights.main.connection_string
  }

  site_config {
    application_insights_connection_string = azurerm_application_insights.main.connection_string
    application_insights_key               = azurerm_application_insights.main.instrumentation_key

    application_stack {
      python_version = "3.11"
    }
  }

  tags = local.tags
}

resource "azurerm_linux_function_app" "decision" {
  name                = "${local.function_app_name}-decision"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  service_plan_id     = azurerm_service_plan.main.id

  storage_account_name                       = azurerm_storage_account.func.name
  storage_uses_managed_identity              = true

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.func.id]
  }

  app_settings = {
    SERVICEBUS_CONNECTION                 = "@Microsoft.KeyVault(SecretUri=${azurerm_key_vault_secret.servicebus_connection.versionless_id})"
    SERVICEBUS_TOPIC_DECISION_NAME        = azurerm_servicebus_topic.decision.name
    COSMOSDB_ENDPOINT                     = azurerm_cosmosdb_account.main.endpoint
    COSMOSDB_DATABASE_NAME                = azurerm_cosmosdb_sql_database.main.name
    KEYVAULT_NAME                         = azurerm_key_vault.main.name
    APPLICATIONINSIGHTS_CONNECTION_STRING = azurerm_application_insights.main.connection_string
  }

  site_config {
    application_insights_connection_string = azurerm_application_insights.main.connection_string
    application_insights_key               = azurerm_application_insights.main.instrumentation_key

    application_stack {
      python_version = "3.11"
    }
  }

  tags = local.tags
}