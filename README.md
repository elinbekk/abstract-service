# Lecture Notes Generator

A web application that processes lecture videos from Yandex Disk and generates PDF summaries with speech-to-text conversion and AI-powered summarization.

## Architecture

The application consists of:

1. **Web Frontend** - Simple HTML interface for submitting lecture links and viewing task status
2. **Python Backend** - Flask application handling HTTP requests and task management
3. **Background Worker** - Processes video files asynchronously
4. **Yandex Database (YDB)** - Stores task status and metadata
5. **Message Queue** - Handles asynchronous processing
6. **Object Storage** - Stores generated PDF files
7. **Yandex SpeechKit** - Converts speech to text
8. **YandexGPT** - Generates lecture summaries

## Used Yandex Cloud Services

- **Yandex API Gateway** - HTTP API endpoint
- **Yandex Object Storage** - PDF file storage
- **Yandex VPC** - Network infrastructure
- **Yandex Managed Service for YDB** - Database
- **Yandex Message Queue** - Asynchronous processing
- **Yandex Container Registry** - Docker image storage
- **Yandex Serverless Containers** - Application hosting
- **Yandex Lockbox** - Secure secret storage
- **Yandex IAM** - Access management
- **Yandex SpeechKit** - Speech recognition
- **YandexGPT API** - Text summarization

## Prerequisites

1. **Yandex Cloud Account** with billing enabled
2. **Yandex Cloud CLI** installed and configured:
   ```bash
   curl https://storage.yandexcloud.net/install.sh | bash
   yc init
   ```
3. **Docker** installed locally
4. **Terraform** installed locally

## Deployment Instructions

### 1. Prepare Environment

Set up your Yandex Cloud token:
```bash
export YC_TOKEN="your-yandex-cloud-token"
```

Get your Cloud ID and Folder ID:
```bash
yc resource manager cloud list
yc resource manager folder list
```

### 2. Configure Terraform

Create a terraform.tfvars file in the `terraform/` directory:

```bash
cd terraform
cp terraform.tfvars.example terraform.tfvars
```

Edit `terraform.tfvars` with your values:
```hcl
cloud_id  = "b1gxxxxxxxxxxxxxxxxxxx"
folder_id = "b1gxxxxxxxxxxxxxxxxxxx"
prefix    = "lecture-notes"  # Optional prefix for resources
```

### 3. Deploy Infrastructure

Deploy all Yandex Cloud resources:

```bash
cd terraform
terraform init
terraform plan
terraform apply
```

This will create:
- Network and security group
- Object Storage bucket
- YDB database
- Message Queue
- Container Registry
- Serverless Containers
- API Gateway
- Service Account with proper permissions

### 4. Build and Push Docker Image

After Terraform completes, note the Container Registry ID from the output.

Build the Docker image:
```bash
export CONTAINER_REGISTRY_ID="crpXXXXXXXXXXXXXXXXXX"  # From Terraform output
docker build -t $CONTAINER_REGISTRY_ID/lecture-notes:latest .
```

Push the image:
```bash
yc container registry configure-docker
docker push $CONTAINER_REGISTRY_ID/lecture-notes:latest
```

### 5. Configure Serverless Containers

You'll need to update the Terraform configuration to use the pushed Docker image and reapply:

```bash
cd terraform
terraform apply
```

### 6. Get Application URL

The API Gateway URL will be in the Terraform output:
```bash
terraform output api_gateway_url
```

You can also view it in the Yandex Cloud Console under API Gateway.

## Manual Testing

1. **Open the application** in your browser using the API Gateway URL
2. **Submit a lecture**:
   - Enter a lecture title
   - Provide a Yandex Disk public video link
   - Click "Generate Lecture Notes"
3. **Monitor progress**:
   - The task will appear as QUEUED
   - Status will change to PROCESSING
   - Finally, it will become DONE or ERROR
4. **Download PDF** when status is DONE

## Testing Locally

You can test the application locally before deployment:

1. **Set up local environment variables**:
   ```bash
   export DB_ENDPOINT="your-local-db-endpoint"
   export DB_PATH="your-local-db-path"
   export FOLDER_ID="your-folder-id"
   export SA_KEY_ID="your-sa-key-id"
   export SA_SECRET="your-sa-secret"
   export QUEUE_URL="your-queue-url"
   export BUCKET_NAME="your-bucket-name"
   ```

2. **Run the web application**:
   ```bash
   python app.py
   ```

3. **Run the worker** (in another terminal):
   ```bash
   python worker.py
   ```

4. **Access the application** at http://localhost:8080

## Cost Considerations

- **Serverless Containers**: Pay only when processing requests
- **YDB Serverless**: Pay for operations and storage
- **Message Queue**: Minimal cost for queue operations
- **Object Storage**: Pay for stored PDFs
- **SpeechKit**: Pay per second of audio processed
- **YandexGPT**: Pay per million tokens

Expected monthly cost for moderate usage: < 1000 RUB

## Cleanup

To remove all created resources:

```bash
cd terraform
terraform destroy
```

This will remove all Yandex Cloud resources created by this project.

## Troubleshooting

### Common Issues

1. **Container build fails**:
   - Check Docker daemon is running
   - Verify requirements.txt dependencies
   - Check system dependencies installation

2. **Terraform apply fails**:
   - Verify YC_TOKEN is set correctly
   - Check service account permissions
   - Review Terraform error messages

3. **Video processing fails**:
   - Verify Yandex Disk link is public
   - Check if video format is supported
   - Monitor worker container logs

4. **Permission errors**:
   - Ensure service account has required IAM roles
   - Check Lockbox secrets if used
   - Verify container service account bindings

### Logs and Monitoring

View container logs in Yandex Cloud Console:
1. Navigate to Serverless Containers
2. Click on the container (api or worker)
3. Go to "Logs" tab

Monitor task status through the web interface or by querying YDB directly.

## Security Notes

- Service Account has admin privileges for simplicity - consider restricting for production
- Secrets are stored as environment variables - use Lockbox for production
- Public API Gateway access - consider restricting by IP if needed
- Object Storage bucket is private by default
- No rate limiting implemented - add API Gateway throttling if needed

## File Structure

```
lecture-notes-generator/
├── app.py                 # Flask web application
├── worker.py              # Background worker
├── requirements.txt       # Python dependencies
├── Dockerfile            # Container build file
├── templates/
│   └── index.html        # Web interface
├── terraform/
│   ├── main.tf          # Infrastructure configuration
│   ├── variables.tf     # Input variables
│   └── terraform.tfvars.example
└── README.md            # This file
```