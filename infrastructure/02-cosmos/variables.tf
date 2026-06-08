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
  description = "Cosmos DB pricing model"
  type        = string
  default     = "Serverless"
}