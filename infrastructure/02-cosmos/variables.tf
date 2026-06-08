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
  description = "Azure region for Cosmos DB resources"
  type        = string
  default     = "polandcentral"
}

variable "cosmosdb_offer_type" {
  description = "Cosmos DB pricing model (Serverless maps to a Standard offer plus the EnableServerless capability)"
  type        = string
  default     = "Serverless"
  validation {
    condition     = contains(["Standard", "Serverless"], var.cosmosdb_offer_type)
    error_message = "Cosmos DB pricing model must be Standard or Serverless."
  }
}