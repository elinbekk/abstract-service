terraform {
  required_providers {
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }
    yandex = {
      source  = "yandex-cloud/yandex"
      version = "~> 0.95"
    }
  }
}

# API Function Zip
data "archive_file" "api_function_zip" {
  type        = "zip"
  source_dir  = "${path.module}/../api_function"
  output_path = "${path.module}/api_function.zip"
  excludes    = ["__pycache__/*", "*.pyc", ".DS_Store"]
}

# Worker Function Zip
data "archive_file" "worker_function_zip" {
  type        = "zip"
  source_dir  = "${path.module}/../worker_function"
  output_path = "${path.module}/worker_function.zip"
  excludes    = ["__pycache__/*", "*.pyc", ".DS_Store"]
}

# API Serverless Function
resource "yandex_function" "api" {
  name               = "${var.prefix}-api"
  description        = "API for Lecture Notes Generator"
  folder_id          = var.folder_id
  runtime            = "python311"
  entrypoint         = "main.handler"
  memory             = 512
  execution_timeout  = 60
  concurrency        = 10
  service_account_id = yandex_iam_service_account.main.id

  environment = {
    S3_ENDPOINT = "https://storage.yandexcloud.net"
    BUCKET_NAME = local.bucket_name
    SA_KEY_ID = yandex_iam_service_account_static_access_key.main.access_key
    SA_SECRET = yandex_iam_service_account_static_access_key.main.secret_key
    QUEUE_URL = yandex_message_queue.main.id
  }

  content {
    zip_filename = data.archive_file.api_function_zip.output_path
  }

  user_hash = filesha256(data.archive_file.api_function_zip.output_path)

  depends_on = [
    yandex_iam_service_account.main,
    yandex_message_queue.main,
    yandex_storage_bucket.main
  ]
}

# Worker Serverless Function
resource "yandex_function" "worker" {
  name               = "${var.prefix}-worker"
  description        = "Worker for video to MP3 conversion using imageio_ffmpeg"
  folder_id          = var.folder_id
  runtime            = "python311"
  entrypoint         = "main.handler"
  memory             = 2048
  execution_timeout  = 3600
  concurrency        = 1
  service_account_id = yandex_iam_service_account.main.id

  environment = {
    FOLDER_ID = var.folder_id
    YDB_ENDPOINT = yandex_ydb_database_serverless.main.document_api_endpoint
    YDB_DATABASE = yandex_ydb_database_serverless.main.name
    STORAGE_BUCKET = local.bucket_name
    STORAGE_ACCESS_KEY = yandex_iam_service_account_static_access_key.main.access_key
    STORAGE_SECRET_KEY = yandex_iam_service_account_static_access_key.main.secret_key
    QUEUE_URL = yandex_message_queue.main.id
    SA_KEY_ID = yandex_iam_service_account_static_access_key.main.access_key
    SERVICE_ACCOUNT_ID = yandex_iam_service_account.main.id
    YC_TOKEN = var.yc_token
    SPEECHKIT_API_KEY = yandex_iam_service_account_api_key.speechkit.secret_key
  }

  content {
    zip_filename = data.archive_file.worker_function_zip.output_path
  }

  user_hash = filesha256(data.archive_file.worker_function_zip.output_path)

  depends_on = [
    yandex_iam_service_account.main
  ]
}

# Grant public invoke rights for API function
resource "yandex_function_iam_binding" "api_invoker_public" {
  function_id = yandex_function.api.id
  role        = "serverless.functions.invoker"
  members     = ["system:allUsers"]
}

# API Gateway
resource "yandex_api_gateway" "main" {
  name        = "${var.prefix}-gateway"
  description = "API Gateway for Lecture Notes Generator"
  folder_id   = var.folder_id

  spec = <<-EOF
openapi: 3.0.0
info:
  title: Lecture Notes Generator API
  version: 1.0.1
paths:
  /:
    get:
      x-yc-apigateway-integration:
        type: cloud_functions
        function_id: ${yandex_function.api.id}
      responses:
        '200':
          description: OK
  /api/tasks:
    get:
      x-yc-apigateway-integration:
        type: cloud_functions
        function_id: ${yandex_function.api.id}
      responses:
        '200':
          description: OK
  /api/submit:
    post:
      x-yc-apigateway-integration:
        type: cloud_functions
        function_id: ${yandex_function.api.id}
      responses:
        '200':
          description: OK
        '400':
          description: Bad Request
        '500':
          description: Internal Server Error
  /api/tasks/delete:
    post:
      x-yc-apigateway-integration:
        type: cloud_functions
        function_id: ${yandex_function.api.id}
      responses:
        '200':
          description: OK
        '400':
          description: Bad Request
        '404':
          description: Task not found
  /api/transcription:
    get:
      x-yc-apigateway-integration:
        type: cloud_functions
        function_id: ${yandex_function.api.id}
      responses:
        '200':
          description: OK
        '400':
          description: Bad Request
        '404':
          description: Task not found
  /api/status:
    get:
      x-yc-apigateway-integration:
        type: cloud_functions
        function_id: ${yandex_function.api.id}
      responses:
        '200':
          description: OK
        '400':
          description: Bad Request
  /api/status/{task_id}:
    get:
      parameters:
        - name: task_id
          in: path
          required: true
          schema:
            type: string
      x-yc-apigateway-integration:
        type: cloud_functions
        function_id: ${yandex_function.api.id}
      responses:
        '200':
          description: OK
        '400':
          description: Bad Request
  /download/{task_id}/notes:
    get:
      x-yc-apigateway-integration:
        type: cloud_functions
        function_id: ${yandex_function.api.id}
      parameters:
        - name: task_id
          in: path
          required: true
          schema:
            type: string
      responses:
        '200':
          description: OK
        '404':
          description: File not available
  /api/pdf:
    get:
      x-yc-apigateway-integration:
        type: cloud_functions
        function_id: ${yandex_function.api.id}
      responses:
        '200':
          description: OK
        '400':
          description: Bad Request
        '404':
          description: PDF not found
  /api/mp3:
    get:
      x-yc-apigateway-integration:
        type: cloud_functions
        function_id: ${yandex_function.api.id}
      responses:
        '200':
          description: OK
        '400':
          description: Bad Request
        '404':
          description: MP3 not found
  /api/abstract:
    get:
      x-yc-apigateway-integration:
        type: cloud_functions
        function_id: ${yandex_function.api.id}
      responses:
        '200':
          description: OK
        '400':
          description: Bad Request
        '404':
          description: Abstract not found
EOF
}

# Trigger worker function from queue
resource "yandex_function_trigger" "worker_queue_trigger" {
  name        = "${var.prefix}-worker-trigger"
  description = "Trigger worker function when message arrives in queue"
  function {
    id                = yandex_function.worker.id
    service_account_id = yandex_iam_service_account.main.id
  }
  message_queue {
    queue_id                 = yandex_message_queue.main.arn
    service_account_id      = yandex_iam_service_account.main.id
    batch_cutoff            = 10
    batch_size              = 1
  }
}

# Outputs
output "api_gateway_url" {
  value = "https://${yandex_api_gateway.main.domain}"
  description = "URL of the API Gateway"
}

output "api_function_id" {
  value = yandex_function.api.id
  description = "API Function ID"
}

output "worker_function_id" {
  value = yandex_function.worker.id
  description = "Worker Function ID"
}