variable "environment" {
  description = "Deployment environment (dev, prod)"
  type        = string
  default     = "dev"
}

variable "location" {
  description = "Azure region for all resources"
  type        = string
  default     = "westeurope"
}

variable "project_name" {
  description = "Project name used in resource naming"
  type        = string
  default     = "project-healer"
}

variable "opencode_api_key" {
  description = "OpenCode Go API key stored in Key Vault"
  type        = string
  sensitive   = true
}

variable "servicebus_sku" {
  description = "Service Bus pricing tier (Basic, Standard, Premium)"
  type        = string
  default     = "Standard"
  validation {
    condition     = contains(["Basic", "Standard", "Premium"], var.servicebus_sku)
    error_message = "Service Bus SKU must be Basic, Standard, or Premium."
  }
}

variable "cosmosdb_offer_type" {
  description = "Cosmos DB pricing model"
  type        = string
  default     = "Serverless"
}

variable "function_runtime" {
  description = "Azure Functions runtime stack"
  type        = string
  default     = "python"
}

variable "log_retention_days" {
  description = "Number of days to retain logs in Blob Storage before deletion"
  type        = number
  default     = 30
}