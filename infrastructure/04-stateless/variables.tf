variable "environment" {
  description = "Deployment environment (dev, prod)"
  type        = string
  default     = "dev"
}

variable "project_name" {
  description = "Project name used in resource naming"
  type        = string
  default     = "project-healer"
}

variable "location" {
  description = "Azure region for stateless resources"
  type        = string
  default     = "swedencentral"
}

variable "opencode_api_key" {
  description = "OpenCode Go API key stored in Key Vault"
  type        = string
  sensitive   = true
}

variable "node_api_key" {
  description = "Shared node API key required by the token service for POC VM uploads"
  type        = string
  sensitive   = true
  validation {
    condition     = trimspace(var.node_api_key) != ""
    error_message = "node_api_key must be a non-empty shared API key for the POC token service."
  }
}

variable "opencode_api_url" {
  description = "OpenCode Go API URL used by the analysis function"
  type        = string
  default     = "https://opencode.ai/zen/go/v1/chat/completions"
}

variable "opencode_model" {
  description = "OpenCode Go model id used by the analysis function"
  type        = string
  default     = "deepseek-v4-flash"
}

variable "servicebus_sku" {
  description = "Service Bus pricing tier (Standard, Premium)"
  type        = string
  default     = "Standard"
  validation {
    condition     = contains(["Standard", "Premium"], var.servicebus_sku)
    error_message = "Service Bus SKU must be Standard or Premium because this design requires topics."
  }
}
