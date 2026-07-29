terraform {
  required_version = ">= 1.5.0"
  required_providers {
    minio = { source = "aminueza/minio", version = "~> 3.0" }
  }
}

provider "minio" {
  minio_server   = "localhost:9000"
  minio_ssl      = false
}