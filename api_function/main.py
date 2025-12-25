import os
import json
import uuid
import boto3
import requests
from datetime import datetime, timezone, timedelta
import logging
from pathlib import Path
from urllib.parse import quote
import re
import html

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Environment variables
S3_ENDPOINT = os.getenv('S3_ENDPOINT', 'https://storage.yandexcloud.net')
BUCKET_NAME = os.getenv('BUCKET_NAME', 'lecture-notes-storage')
SA_KEY_ID = os.getenv('SA_KEY_ID')
SA_SECRET = os.getenv('SA_SECRET')
QUEUE_URL = os.getenv('QUEUE_URL')

# Template directory
TEMPLATE_DIR = Path(__file__).parent / 'templates'

# Initialize S3 client
s3_client = boto3.client(
    's3',
    endpoint_url=S3_ENDPOINT,
    aws_access_key_id=SA_KEY_ID,
    aws_secret_access_key=SA_SECRET,
    region_name='ru-central1'
)

# Initialize SQS client
sqs_client = boto3.client(
    'sqs',
    endpoint_url='https://message-queue.api.cloud.yandex.net',
    aws_access_key_id=SA_KEY_ID,
    aws_secret_access_key=SA_SECRET,
    region_name='ru-central1'
)


# ============================================================================
# Storage Functions
# ============================================================================

def get_tasks_from_storage():
    """Get all tasks from S3 storage"""
    try:
        response = s3_client.list_objects_v2(Bucket=BUCKET_NAME, Prefix='tasks/')
        tasks = {}

        if 'Contents' in response:
            for obj in response['Contents']:
                if obj['Key'].endswith('.json'):
                    try:
                        obj_response = s3_client.get_object(Bucket=BUCKET_NAME, Key=obj['Key'])
                        task_data = json.loads(obj_response['Body'].read().decode('utf-8'))
                        task_id = obj['Key'].replace('tasks/', '').replace('.json', '')
                        tasks[task_id] = task_data
                    except Exception as e:
                        logger.error(f"Error reading task {obj['Key']}: {e}")

        # Trigger cleanup occasionally (random chance to avoid overhead)
        import random
        if random.random() < 0.1:  # 10% chance
            try:
                cleanup_old_files()
            except Exception as e:
                logger.error(f"Cleanup failed: {e}")

        return tasks
    except Exception as e:
        logger.error(f"Error fetching tasks: {e}")
        return {}


def cleanup_old_files():
    """Clean up old files and tasks from storage (runs occasionally)"""
    try:
        # Everything expires after 1 hour
        cutoff_hours = 1
        # Use timezone-aware datetime to match S3's LastModified format
        cutoff_time = datetime.now(timezone.utc) - timedelta(hours=cutoff_hours)

        total_deleted = 0

        # All prefixes to clean up (including task metadata)
        all_prefixes = ['audio/', 'mp3/', 'abstracts/', 'transcriptions/', 'notes/', 'tasks/']

        for prefix in all_prefixes:
            try:
                paginator = s3_client.get_paginator('list_objects_v2')
                page_iterator = paginator.paginate(Bucket=BUCKET_NAME, Prefix=prefix)

                for page in page_iterator:
                    if 'Contents' not in page:
                        continue

                    objects_to_delete = [
                        {'Key': obj['Key']}
                        for obj in page['Contents']
                        if obj['LastModified'] < cutoff_time
                    ]

                    if objects_to_delete:
                        s3_client.delete_objects(
                            Bucket=BUCKET_NAME,
                            Delete={'Objects': objects_to_delete}
                        )
                        total_deleted += len(objects_to_delete)
                        logger.info(f"Cleanup: deleted {len(objects_to_delete)} from {prefix}")

            except Exception as e:
                logger.error(f"Error cleaning {prefix}: {e}")

        if total_deleted > 0:
            logger.info(f"Cleanup complete: {total_deleted} items deleted (older than {cutoff_hours}h)")

        return total_deleted

    except Exception as e:
        logger.error(f"Cleanup error: {e}")
        return 0


def save_task_to_storage(task_id, task_data):
    """Save task to S3 storage"""
    try:
        s3_client.put_object(
            Bucket=BUCKET_NAME,
            Key=f'tasks/{task_id}.json',
            Body=json.dumps(task_data),
            ContentType='application/json'
        )
        return True
    except Exception as e:
        logger.error(f"Error saving task {task_id}: {e}")
        return False


# ============================================================================
# Template Functions
# ============================================================================

def render_template(template_name, context=None):
    """Render HTML template with context"""
    template_path = TEMPLATE_DIR / template_name

    try:
        with open(template_path, 'r', encoding='utf-8') as f:
            html_content = f.read()

        if context:
            for key, value in context.items():
                placeholder = '{{ ' + key + ' }}'
                if isinstance(value, str):
                    html_content = html_content.replace(placeholder, value)

        return html_content
    except Exception as e:
        logger.error(f"Error rendering template {template_name}: {e}")
        return f"<html><body><h1>Template Error: {e}</h1></body></html>"


# ============================================================================
# Validation Functions
# ============================================================================

def validate_yandex_disk_link(video_url):
    """Validate Yandex Disk public link and get file metadata"""
    try:
        # Check if this is a Yandex Disk public link
        is_yandex_disk = bool(re.match(
            r'https://(disk\.yandex\.[a-z]+|disk\.360\.yandex\.[a-z]+|yadi\.sk)/(d|i)/',
            video_url
        ))

        if not is_yandex_disk:
            return {
                'is_valid': True,
                'is_yandex_disk': False,
                'message': 'Not a Yandex Disk link'
            }

        # Call Yandex Disk API to validate the public link
        logger.info(f"Validating Yandex Disk link: {video_url}")
        encoded_key = quote(video_url, safe='')
        api_url = f"https://cloud-api.yandex.net/v1/disk/public/resources?public_key={encoded_key}"

        headers = {}
        oauth_token = os.getenv('YANDEX_OAUTH_TOKEN')
        if oauth_token:
            headers['Authorization'] = f'OAuth {oauth_token}'

        response = requests.get(api_url, headers=headers, timeout=10)
        logger.info(f"API response status: {response.status_code}")

        if response.status_code == 200:
            metadata = response.json()

            # Check if it's a video file
            file_name = metadata.get('name', '').lower()
            video_extensions = ['.mp4', '.avi', '.mov', '.wmv', '.flv', '.webm', '.mkv', '.m4v']
            is_video = any(file_name.endswith(ext) for ext in video_extensions)

            if not is_video:
                return {
                    'is_valid': False,
                    'is_yandex_disk': True,
                    'error': 'File is not a video file',
                    'file_name': file_name,
                    'file_type': metadata.get('mime_type', 'unknown')
                }

            return {
                'is_valid': True,
                'is_yandex_disk': True,
                'file_name': metadata.get('name'),
                'file_size': metadata.get('size'),
                'file_type': metadata.get('mime_type'),
                'download_url': metadata.get('file'),
                'message': 'Yandex Disk video file validated successfully'
            }
        else:
            error_info = response.json() if response.content else {'error': 'Unknown error'}
            return {
                'is_valid': False,
                'is_yandex_disk': True,
                'error': 'Invalid or expired Yandex Disk link',
                'status_code': response.status_code,
                'details': error_info
            }

    except requests.exceptions.RequestException as e:
        logger.error(f"Network error validating Yandex Disk link: {e}")
        return {
            'is_valid': False,
            'is_yandex_disk': True,
            'error': 'Network error while validating link',
            'details': str(e)
        }
    except Exception as e:
        logger.error(f"Unexpected error validating Yandex Disk link: {e}")
        return {
            'is_valid': False,
            'is_yandex_disk': True,
            'error': 'Unexpected error during validation',
            'details': str(e)
        }


# ============================================================================
# Response Helpers
# ============================================================================

def json_response(data, status_code=200):
    """Create JSON response"""
    return {
        'statusCode': status_code,
        'headers': {'Content-Type': 'application/json'},
        'body': json.dumps(data)
    }


def html_response(content):
    """Create HTML response"""
    return {
        'statusCode': 200,
        'headers': {'Content-Type': 'text/html; charset=utf-8'},
        'body': content
    }


def redirect_response(url):
    """Create redirect response"""
    return {
        'statusCode': 302,
        'headers': {
            'Location': url,
            'Content-Type': 'text/html'
        },
        'body': ''
    }


# ============================================================================
# Page Handlers
# ============================================================================

def handle_index():
    """Serve the index page (create task form)"""
    return html_response(render_template('index.html'))


def handle_tasks_page():
    """Serve the tasks page (task list)"""
    return html_response(render_template('tasks.html'))


# ============================================================================
# API Handlers
# ============================================================================

def handle_get_all_tasks():
    """Handle GET /api/tasks"""
    tasks = get_tasks_from_storage()
    return json_response(tasks)


def handle_submit_task(event):
    """Handle POST /api/submit"""
    try:
        body = json.loads(event.get('body', '{}'))

        title = body.get('title', '').strip()
        video_url = body.get('video_url', '').strip()

        if not title or not video_url:
            return json_response({'error': 'Please provide both title and video URL'}, 400)

        # Validate Yandex Disk link
        logger.info(f"Validating video URL: {video_url}")
        validation_result = validate_yandex_disk_link(video_url)

        if not validation_result.get('is_valid', False):
            logger.error(f"Video URL validation failed: {validation_result}")
            error_msg = 'Invalid video URL'
            if validation_result.get('error'):
                error_msg += f': {validation_result["error"]}'
            return json_response({'error': error_msg, 'validation_details': validation_result}, 400)

        logger.info(f"Video URL validation successful: {validation_result.get('message', 'Valid URL')}")

        task_id = str(uuid.uuid4())
        task = {
            'task_id': task_id,
            'title': title,
            'video_url': video_url,
            'description': body.get('description', ''),
            'status': 'processing',
            'created_at': datetime.now().isoformat(),
            'progress': 10
        }

        # Save to persistent storage
        if save_task_to_storage(task_id, task):
            # Add task to queue for worker processing
            try:
                sqs_client.send_message(
                    QueueUrl=QUEUE_URL,
                    MessageBody=json.dumps(task)
                )
                logger.info(f"Task {task_id} added to queue")

                return json_response({
                    'task_id': task_id,
                    'task': task,
                    'message': 'Lecture added to queue successfully'
                })
            except Exception as e:
                logger.error(f"Failed to add task to queue: {e}")
                return json_response({'error': 'Task saved but failed to queue for processing'}, 500)
        else:
            return json_response({'error': 'Failed to save task to storage'}, 500)

    except Exception as e:
        logger.error(f"Error in handle_submit_task: {e}")
        return json_response({'error': str(e)}, 500)


def handle_task_status_lookup(task_id):
    """Handle task status lookup"""
    try:
        tasks = get_tasks_from_storage()

        if task_id in tasks:
            return json_response(tasks[task_id])
        else:
            return json_response({
                'error': 'Task not found',
                'task_id': task_id
            }, 404)
    except Exception as e:
        logger.error(f"Error in handle_task_status_lookup: {e}")
        return json_response({'error': str(e)}, 500)


def handle_delete_task(task_id):
    """Handle DELETE /api/tasks/{task_id}"""
    try:
        tasks = get_tasks_from_storage()

        if task_id not in tasks:
            return json_response({
                'error': 'Task not found',
                'task_id': task_id
            }, 404)

        # Delete task file and associated files from S3
        files_to_delete = [
            f'tasks/{task_id}.json',
            f'transcriptions/{task_id}.txt',
            f'results/{task_id}/notes.pdf',
            f'mp3/{task_id}.mp3',
            f'audio/{task_id}.mp3',
            f'abstracts/{task_id}.md'
        ]

        for file_key in files_to_delete:
            try:
                s3_client.delete_object(Bucket=BUCKET_NAME, Key=file_key)
                logger.info(f"Deleted: {file_key}")
            except Exception as e:
                logger.debug(f"No file to delete: {file_key} - {e}")

        return json_response({
            'message': 'Task deleted successfully',
            'task_id': task_id
        })

    except Exception as e:
        logger.error(f"Error in handle_delete_task: {e}")
        return json_response({'error': str(e)}, 500)


def handle_download_transcription(task_id):
    """Handle transcription download"""
    try:
        tasks = get_tasks_from_storage()

        if task_id not in tasks:
            return json_response({'error': 'Task not found', 'task_id': task_id}, 404)

        task = tasks[task_id]

        if not task.get('transcription'):
            return json_response({
                'error': 'No transcription available for this task',
                'task_id': task_id,
                'task_status': task.get('status', 'unknown')
            }, 404)

        # Prepare transcription content
        transcription_content = f"""Lecture Transcription
=====================

Title: {task.get('title', 'Unknown')}
Video URL: {task.get('video_url', 'Unknown')}
Task ID: {task_id}
Created: {task.get('created_at', 'Unknown')}
Description: {task.get('description', 'No description')}

Video Duration: {task.get('video_duration', 'Unknown')} seconds
Transcription Characters: {len(task.get('transcription', ''))}

TRANSCRIPTION:
-------------
{task.get('transcription', '')}

---
Generated by Yandex Cloud SpeechKit
Lecture Notes Generator
"""

        return {
            'statusCode': 200,
            'headers': {
                'Content-Type': 'text/plain; charset=utf-8',
                'Content-Disposition': f'attachment; filename="transcription_{task_id}.txt"',
                'Access-Control-Allow-Origin': '*'
            },
            'body': transcription_content
        }

    except Exception as e:
        logger.error(f"Error in handle_download_transcription: {e}")
        return json_response({'error': str(e)}, 500)


def handle_download_mp3(task_id):
    """Handle MP3 download"""
    try:
        tasks = get_tasks_from_storage()

        if task_id not in tasks:
            return json_response({'error': 'Task not found', 'task_id': task_id}, 404)

        task = tasks[task_id]

        if not task.get('mp3_url'):
            return json_response({
                'error': 'MP3 not available for this task',
                'task_id': task_id,
                'task_status': task.get('status', 'unknown')
            }, 404)

        return redirect_response(task.get('mp3_url'))

    except Exception as e:
        logger.error(f"Error in handle_download_mp3: {e}")
        return json_response({'error': str(e)}, 500)


def handle_download_pdf(task_id):
    """Handle PDF download"""
    import base64

    try:
        tasks = get_tasks_from_storage()

        if task_id not in tasks:
            return json_response({'error': 'Task not found', 'task_id': task_id}, 404)

        task = tasks[task_id]

        # Check if abstract_url exists - use it to generate PDF on-the-fly from abstract
        abstract_url = task.get('abstract_url')
        if not abstract_url:
            return json_response({
                'error': 'PDF not available for this task',
                'task_id': task_id,
                'task_status': task.get('status', 'unknown')
            }, 404)

        # Fetch abstract content from S3
        # URL format: https://storage.yandexcloud.net/{bucket}/{key}
        if abstract_url.startswith('https://storage.yandexcloud.net/'):
            key = abstract_url.split('/', 4)[-1]
            obj_response = s3_client.get_object(Bucket=BUCKET_NAME, Key=key)
            abstract_content = obj_response['Body'].read().decode('utf-8')
        else:
            return json_response({'error': 'Invalid abstract URL'}, 400)

        # Generate PDF on-the-fly from abstract
        from reportlab.pdfgen import canvas
        from reportlab.lib.pagesizes import letter, A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak
        from reportlab.lib.units import inch
        from reportlab.lib.enums import TA_LEFT, TA_CENTER
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from io import BytesIO

        # Create PDF in memory
        buffer = BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4)
        story = []
        styles = getSampleStyleSheet()

        # Add custom styles
        title_style = ParagraphStyle(
            'CustomTitle',
            parent=styles['Title'],
            fontSize=18,
            textColor='#000000',
            spaceAfter=20,
            alignment=TA_CENTER
        )

        heading_style = ParagraphStyle(
            'CustomHeading',
            parent=styles['Heading1'],
            fontSize=14,
            textColor='#000000',
            spaceAfter=10,
            spaceBefore=15
        )

        normal_style = ParagraphStyle(
            'CustomNormal',
            parent=styles['Normal'],
            fontSize=10,
            textColor='#000000',
            spaceAfter=8,
            leading=14
        )

        # Sanitize title for PDF (escape XML special chars)
        title = html.escape(task.get('title', 'Lecture Notes'))
        story.append(Paragraph(title, title_style))
        story.append(Spacer(1, 0.2*inch))

        # Add timestamp
        created_at = task.get('created_at', '')
        if created_at:
            story.append(Paragraph(f"<i>Created: {html.escape(created_at[:19])}</i>", normal_style))
        story.append(Spacer(1, 0.3*inch))

        # Process markdown-like content
        lines = abstract_content.split('\n')
        for line in lines:
            line = line.strip()
            if not line:
                story.append(Spacer(1, 0.1*inch))
                continue

            # Handle headers (# ## ###)
            if line.startswith('#'):
                level = len(line) - len(line.lstrip('#'))
                text = html.escape(line.lstrip('#').strip())
                if level == 1:
                    story.append(Paragraph(text, heading_style))
                else:
                    story.append(Paragraph(text, normal_style))
            # Handle bullet points
            elif line.startswith('-') or line.startswith('*'):
                text = '• ' + html.escape(line.lstrip('-*').strip())
                story.append(Paragraph(text, normal_style))
            # Handle numbered lists
            elif len(line) > 1 and line[0].isdigit() and line[1] == '.':
                story.append(Paragraph(html.escape(line), normal_style))
            # Regular text - escape special characters
            else:
                story.append(Paragraph(html.escape(line), normal_style))

        # Build PDF
        doc.build(story)

        # Get PDF content and encode as base64 for Yandex Cloud Functions
        pdf_content = buffer.getvalue()
        buffer.close()

        # Create filename from lecture title (sanitize for filename)
        lecture_title = task.get('title', 'Lecture Notes')
        # Remove/replace characters that are invalid in filenames
        safe_filename = re.sub(r'[<>:"/\\|?*]', '_', lecture_title)
        safe_filename = safe_filename[:100]  # Limit length
        safe_filename = safe_filename.strip()

        # Return PDF content with base64 encoding (required for binary responses in Yandex Cloud)
        return {
            'statusCode': 200,
            'headers': {
                'Content-Type': 'application/pdf',
                'Content-Disposition': f'attachment; filename="{safe_filename}.pdf"',
                'Access-Control-Allow-Origin': '*'
            },
            'body': base64.b64encode(pdf_content).decode('utf-8'),
            'isBase64Encoded': True
        }

    except Exception as e:
        logger.error(f"Error in handle_download_pdf: {e}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
        return json_response({'error': str(e)}, 500)


def handle_get_abstract(task_id):
    """Handle lecture abstract download as markdown"""
    try:
        tasks = get_tasks_from_storage()

        if task_id not in tasks:
            return json_response({'error': 'Task not found', 'task_id': task_id}, 404)

        task = tasks[task_id]

        # Check if abstract_url exists or if abstract is embedded in task
        abstract_content = None

        if task.get('abstract_url'):
            # Fetch from S3 using the URL
            try:
                # Extract the key from the URL
                abstract_url = task.get('abstract_url')
                # URL format: https://storage.yandexcloud.net/{bucket}/{key}
                if abstract_url.startswith('https://storage.yandexcloud.net/'):
                    key = abstract_url.split('/', 4)[-1]
                    obj_response = s3_client.get_object(Bucket=BUCKET_NAME, Key=key)
                    abstract_content = obj_response['Body'].read().decode('utf-8')
            except Exception as e:
                logger.error(f"Failed to fetch abstract from S3: {e}")
                return json_response({
                    'error': 'Failed to retrieve abstract',
                    'task_id': task_id
                }, 500)

        return {
            'statusCode': 200,
            'headers': {
                'Content-Type': 'text/markdown; charset=utf-8',
                'Content-Disposition': f'attachment; filename="brief_{task_id}.md"',
                'Access-Control-Allow-Origin': '*'
            },
            'body': abstract_content
        }

    except Exception as e:
        logger.error(f"Error in handle_get_abstract: {e}")
        return json_response({'error': str(e)}, 500)


# ============================================================================
# Main Handler
# ============================================================================

def handler(event, context):
    """Main handler for Yandex Cloud Functions"""
    if 'httpMethod' in event:
        return handle_api_gateway_request(event)
    else:
        return json_response({'message': 'Function is working'})


def handle_api_gateway_request(event):
    """Handle API Gateway requests"""
    path = event.get('path', '/')
    method = event.get('httpMethod', 'GET')

    logger.info(f"API request: {method} {path}")

    try:
        # Page routes
        if method == 'GET' and path == '/':
            return handle_index()

        if method == 'GET' and path == '/tasks':
            return handle_tasks_page()

        # API routes
        if method == 'GET' and path == '/api/tasks':
            return handle_get_all_tasks()

        if method == 'POST' and path == '/api/submit':
            return handle_submit_task(event)

        if method == 'GET' and path == '/api/status':
            query_params = event.get('queryStringParameters') or {}
            task_id = query_params.get('task_id', '')
            if task_id:
                return handle_task_status_lookup(task_id)
            return json_response({'error': 'task_id query parameter is required'}, 400)

        if method == 'POST' and path == '/api/tasks/delete':
            body = json.loads(event.get('body', '{}')) if event.get('body') else {}
            task_id = body.get('task_id') or (event.get('queryStringParameters') or {}).get('task_id', '')
            if task_id:
                return handle_delete_task(task_id)
            return json_response({'error': 'task_id is required'}, 400)

        if method == 'GET' and path == '/api/transcription':
            query_params = event.get('queryStringParameters') or {}
            task_id = query_params.get('task_id', '')
            if task_id:
                return handle_download_transcription(task_id)
            return json_response({'error': 'task_id query parameter is required'}, 400)

        if method == 'GET' and path == '/api/mp3':
            query_params = event.get('queryStringParameters') or {}
            task_id = query_params.get('task_id', '')
            if task_id:
                return handle_download_mp3(task_id)
            return json_response({'error': 'task_id query parameter is required'}, 400)

        if method == 'GET' and path == '/api/pdf':
            query_params = event.get('queryStringParameters') or {}
            task_id = query_params.get('task_id', '')
            if task_id:
                return handle_download_pdf(task_id)
            return json_response({'error': 'task_id query parameter is required'}, 400)

        if method == 'GET' and path == '/api/abstract':
            query_params = event.get('queryStringParameters') or {}
            task_id = query_params.get('task_id', '')
            if task_id:
                return handle_get_abstract(task_id)
            return json_response({'error': 'task_id query parameter is required'}, 400)

        # 404 - Not found
        return json_response({'error': 'Not found'}, 404)

    except Exception as e:
        logger.error(f"Error handling request: {e}")
        return json_response({'error': str(e)}, 500)


# ============================================================================
# Local Development
# ============================================================================

if __name__ == '__main__':
    from flask import Flask, request
    app = Flask(__name__)

    @app.route('/')
    def index():
        return render_template('index.html')

    @app.route('/tasks')
    def tasks():
        return render_template('tasks.html')

    @app.route('/api/tasks', methods=['GET'])
    def api_tasks():
        return handle_get_all_tasks()['body']

    @app.route('/api/submit', methods=['POST'])
    def api_submit():
        result = handle_submit_task({'body': request.data})
        return result['body'], result['statusCode']

    @app.route('/api/status')
    def api_status():
        task_id = request.args.get('task_id')
        result = handle_task_status_lookup(task_id)
        return result['body'], result['statusCode']

    @app.route('/api/tasks/delete', methods=['POST'])
    def api_delete():
        import flask
        result = handle_delete_task(flask.request.json.get('task_id'))
        return result['body'], result['statusCode']

    @app.route('/api/transcription')
    def api_transcription():
        task_id = request.args.get('task_id')
        result = handle_download_transcription(task_id)
        if result['statusCode'] == 200:
            return result['body']
        return result['body'], result['statusCode']

    @app.route('/api/mp3')
    def api_mp3():
        task_id = request.args.get('task_id')
        result = handle_download_mp3(task_id)
        return result['body'], result['statusCode']

    @app.route('/api/pdf')
    def api_pdf():
        import base64
        task_id = request.args.get('task_id')
        result = handle_download_pdf(task_id)
        if result['statusCode'] == 200 and result.get('isBase64Encoded'):
            # Decode base64 and return as binary response
            from flask import Response
            pdf_data = base64.b64decode(result['body'])
            return Response(
                pdf_data,
                status=200,
                headers=result['headers']
            )
        return result['body'], result['statusCode']

    @app.route('/api/abstract')
    def api_abstract():
        task_id = request.args.get('task_id')
        result = handle_get_abstract(task_id)
        return result['body'], result['statusCode']

    app.run(host='0.0.0.0', port=8080, debug=True)
