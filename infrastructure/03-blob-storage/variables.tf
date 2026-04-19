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

variable "log_retention_days" {
  description = "Number of days to retain logs in Blob Storage before deletion"
  type        = number
  default     = 30
}