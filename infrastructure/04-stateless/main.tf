data "azurerm_resource_group" "main" {
  name = "rg-${var.project_name}-${var.environment}"
}

data "azurerm_client_config" "current" {}

data "azurerm_cosmosdb_account" "main" {
  name                = local.cosmosdb_account_name
  resource_group_name = data.azurerm_resource_group.main.name
}

data "azurerm_cosmosdb_sql_database" "main" {
  name                = "project-healer"
  resource_group_name = data.azurerm_resource_group.main.name
  account_name        = data.azurerm_cosmosdb_account.main.name
}

data "azurerm_storage_account" "logs" {
  name                = local.logs_storage_account_name
  resource_group_name = data.azurerm_resource_group.main.name
}

# --- Service Bus ---

resource "azurerm_servicebus_namespace" "main" {
  name                = local.servicebus_ns_name
  location            = data.azurerm_resource_group.main.location
  resource_group_name = data.azurerm_resource_group.main.name
  sku                 = var.servicebus_sku

  tags = local.tags
}

resource "azurerm_servicebus_topic" "analysis" {
  name                  = "analysis"
  namespace_id          = azurerm_servicebus_namespace.main.id
  max_size_in_megabytes = 1024
}

resource "azurerm_servicebus_topic" "decision" {
  name                  = "decision"
  namespace_id          = azurerm_servicebus_namespace.main.id
  max_size_in_megabytes = 1024
}

resource "azurerm_servicebus_subscription" "analysis_agent" {
  name               = "analysis-agent"
  topic_id           = azurerm_servicebus_topic.analysis.id
  max_delivery_count = 5

  dead_lettering_on_message_expiration = true
}

resource "azurerm_servicebus_subscription" "decision_agent" {
  name               = "decision-agent"
  topic_id           = azurerm_servicebus_topic.decision.id
  max_delivery_count = 5

  dead_lettering_on_message_expiration = true
}

resource "azurerm_servicebus_subscription" "local_agent" {
  name               = "local-agent"
  topic_id           = azurerm_servicebus_topic.decision.id
  max_delivery_count = 5

  dead_lettering_on_message_expiration = true
}

resource "azurerm_servicebus_namespace_authorization_rule" "shared_access" {
  name         = "SharedAccessPolicy"
  namespace_id = azurerm_servicebus_namespace.main.id

  listen = true
  send   = true
  manage = false
}

# --- Key Vault ---

resource "azurerm_key_vault" "main" {
  name                       = local.keyvault_name
  location                   = data.azurerm_resource_group.main.location
  resource_group_name        = data.azurerm_resource_group.main.name
  tenant_id                  = data.azurerm_client_config.current.tenant_id
  sku_name                   = "standard"
  enabled_for_deployment     = false
  purge_protection_enabled   = true
  soft_delete_retention_days = 90

  network_acls {
    default_action = "Deny"
    bypass         = "AzureServices"
  }

  tags = local.tags
}

resource "azurerm_key_vault_access_policy" "func" {
  key_vault_id = azurerm_key_vault.main.id
  tenant_id    = data.azurerm_client_config.current.tenant_id
  object_id    = azurerm_user_assigned_identity.func.principal_id

  secret_permissions = [
    "Get",
    "List",
  ]
}

resource "azurerm_key_vault_secret" "opencode_api_key" {
  name         = "OpenCodeApiKey"
  value        = var.opencode_api_key
  key_vault_id = azurerm_key_vault.main.id

  depends_on = [azurerm_key_vault_access_policy.func]
}

resource "azurerm_key_vault_secret" "servicebus_connection" {
  name         = "ServiceBusConnection"
  value        = azurerm_servicebus_namespace_authorization_rule.shared_access.primary_connection_string
  key_vault_id = azurerm_key_vault.main.id

  depends_on = [azurerm_key_vault_access_policy.func]
}

# --- Application Insights ---

resource "azurerm_application_insights" "main" {
  name                = local.appinsights_name
  location            = data.azurerm_resource_group.main.location
  resource_group_name = data.azurerm_resource_group.main.name
  application_type    = "other"

  tags = local.tags
}

# --- Function App Infrastructure ---

resource "azurerm_user_assigned_identity" "func" {
  name                = "id-${local.name_prefix}-func"
  location            = data.azurerm_resource_group.main.location
  resource_group_name = data.azurerm_resource_group.main.name

  tags = local.tags
}

resource "azurerm_service_plan" "main" {
  name                = local.app_plan_name
  location            = data.azurerm_resource_group.main.location
  resource_group_name = data.azurerm_resource_group.main.name
  os_type             = "Linux"
  sku_name            = "Y1"

  tags = local.tags
}

resource "azurerm_storage_account" "func" {
  name                            = local.func_storage_account_name
  resource_group_name             = data.azurerm_resource_group.main.name
  location                        = data.azurerm_resource_group.main.location
  account_tier                    = "Standard"
  account_replication_type        = "LRS"
  allow_nested_items_to_be_public = false

  tags = local.tags
}

resource "azurerm_linux_function_app" "token" {
  name                = "${local.function_app_name}-token"
  location            = data.azurerm_resource_group.main.location
  resource_group_name = data.azurerm_resource_group.main.name
  service_plan_id     = azurerm_service_plan.main.id

  storage_account_name          = azurerm_storage_account.func.name
  storage_uses_managed_identity = true

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.func.id]
  }

  app_settings = {
    STORAGE_ACCOUNT_NAME                  = data.azurerm_storage_account.logs.name
    LOGS_CONTAINER_NAME                   = "logs"
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
  location            = data.azurerm_resource_group.main.location
  resource_group_name = data.azurerm_resource_group.main.name
  service_plan_id     = azurerm_service_plan.main.id

  storage_account_name          = azurerm_storage_account.func.name
  storage_uses_managed_identity = true

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
  location            = data.azurerm_resource_group.main.location
  resource_group_name = data.azurerm_resource_group.main.name
  service_plan_id     = azurerm_service_plan.main.id

  storage_account_name          = azurerm_storage_account.func.name
  storage_uses_managed_identity = true

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
  location            = data.azurerm_resource_group.main.location
  resource_group_name = data.azurerm_resource_group.main.name
  service_plan_id     = azurerm_service_plan.main.id

  storage_account_name          = azurerm_storage_account.func.name
  storage_uses_managed_identity = true

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.func.id]
  }

  app_settings = {
    SERVICEBUS_CONNECTION                 = "@Microsoft.KeyVault(SecretUri=${azurerm_key_vault_secret.servicebus_connection.versionless_id})"
    SERVICEBUS_TOPIC_DECISION_NAME        = azurerm_servicebus_topic.decision.name
    COSMOSDB_ENDPOINT                     = data.azurerm_cosmosdb_account.main.endpoint
    COSMOSDB_DATABASE_NAME                = data.azurerm_cosmosdb_sql_database.main.name
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

# --- Role Assignments ---

resource "azurerm_role_assignment" "func_storage_blob_contributor" {
  scope                = data.azurerm_storage_account.logs.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azurerm_user_assigned_identity.func.principal_id
}

resource "azurerm_role_assignment" "func_storage_account_contributor" {
  scope                = azurerm_storage_account.func.id
  role_definition_name = "Storage Account Contributor"
  principal_id         = azurerm_user_assigned_identity.func.principal_id
}

resource "azurerm_role_assignment" "func_servicebus_sender" {
  scope                = azurerm_servicebus_namespace.main.id
  role_definition_name = "Azure Service Bus Data Sender"
  principal_id         = azurerm_user_assigned_identity.func.principal_id
}

resource "azurerm_role_assignment" "func_servicebus_receiver" {
  scope                = azurerm_servicebus_namespace.main.id
  role_definition_name = "Azure Service Bus Data Receiver"
  principal_id         = azurerm_user_assigned_identity.func.principal_id
}

resource "azurerm_role_assignment" "func_cosmosdb_contributor" {
  scope                = data.azurerm_cosmosdb_account.main.id
  role_definition_name = "Cosmos DB Operator"
  principal_id         = azurerm_user_assigned_identity.func.principal_id
}

resource "azurerm_role_assignment" "func_keyvault_secrets_user" {
  scope                = azurerm_key_vault.main.id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_user_assigned_identity.func.principal_id
}

resource "azurerm_role_assignment" "func_monitoring_publisher" {
  scope                = data.azurerm_resource_group.main.id
  role_definition_name = "Monitoring Publisher"
  principal_id         = azurerm_user_assigned_identity.func.principal_id
}