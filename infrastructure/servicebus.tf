resource "azurerm_servicebus_namespace" "main" {
  name                = local.servicebus_ns_name
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
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