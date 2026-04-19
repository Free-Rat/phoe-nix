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