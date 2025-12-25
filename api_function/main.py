import os
import json
import uuid
import boto3
import requests
from datetime import datetime
import logging
from flask import Flask, request, jsonify
from urllib.parse import quote
import re

app = Flask(__name__)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize S3 client
S3_ENDPOINT = os.getenv('S3_ENDPOINT', 'https://storage.yandexcloud.net')
BUCKET_NAME = os.getenv('BUCKET_NAME', 'lecture-notes-storage')
SA_KEY_ID = os.getenv('SA_KEY_ID')
SA_SECRET = os.getenv('SA_SECRET')
QUEUE_URL = os.getenv('QUEUE_URL')

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

        return tasks
    except Exception as e:
        logger.error(f"Error fetching tasks: {e}")
        return {}

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

def handler(event, context):
    """Main handler for Yandex Cloud Functions"""
    if 'httpMethod' in event:
        # API Gateway request
        return handle_api_gateway_request(event)
    else:
        # Direct invocation or other trigger
        return handle_direct_request(event)

def handle_api_gateway_request(event):
    """Handle API Gateway requests"""
    path = event.get('path', '/')
    method = event.get('httpMethod', 'GET')

    logger.info("API request: " + method + " " + path + " - v2")

    try:
        if method == 'GET' and path == '/':
            return handle_index()
        elif method == 'GET' and path == '/api/tasks':
            return handle_get_all_tasks()
        elif method == 'POST' and path == '/api/submit':
            return handle_submit_task(event)
        elif method == 'DELETE' and path.startswith('/api/tasks/'):
            # Extract task_id from path parameters
            task_id = path.split('/')[-1]
            logger.info("Delete request for task_id: " + task_id + " (from path: " + path + ")")

            # Check if we got the literal "{task_id}" string (broken path parameter extraction)
            if task_id == '{task_id}':
                return {
                    'statusCode': 400,
                    'headers': {'Content-Type': 'application/json'},
                    'body': json.dumps({
                        'error': 'Yandex API Gateway path parameter extraction issue',
                        'workaround': 'Use query parameter: /api/tasks/delete?task_id=<task_id>',
                        'available_tasks': list(get_tasks_from_storage().keys())[:5]
                    })
                }

            return handle_delete_task(task_id)
        elif method == 'POST' and path == '/api/tasks/delete':
            # Query parameter workaround for delete functionality
            body = {}
            if event.get('body'):
                body = json.loads(event.get('body', '{}'))

            # Try to get task_id from JSON body first, then query params
            task_id = body.get('task_id') or event.get('queryStringParameters', {}).get('task_id', '')
            logger.info("Delete request via POST for task_id: " + task_id)

            if not task_id:
                return {
                    'statusCode': 400,
                    'headers': {'Content-Type': 'application/json'},
                    'body': json.dumps({
                        'error': 'task_id is required in request body or query parameter',
                        'example': 'POST /api/tasks/delete with {"task_id": "<task-id>"} or /api/tasks/delete?task_id=<task-id>'
                    })
                }

            return handle_delete_task(task_id)
        elif method == 'GET' and path == '/api/transcription':
            # Query parameter for transcription download
            query_params = event.get('queryStringParameters') or {}
            task_id = query_params.get('task_id', '')
            logger.info("Transcription download request for task_id: " + task_id)

            if not task_id:
                return {
                    'statusCode': 400,
                    'headers': {'Content-Type': 'application/json'},
                    'body': json.dumps({
                        'error': 'task_id query parameter is required',
                        'example': '/api/transcription?task_id=<task-id>'
                    })
                }

            return handle_download_transcription(task_id)
        elif method == 'GET' and path.startswith('/download/') and path.endswith('/transcription'):
            # Path parameter approach for transcription download
            task_id = path.split('/')[2]  # Extract from /download/{task_id}/transcription
            logger.info("Transcription download request for task_id: " + task_id + " (from path: " + path + ")")

            if task_id == '{task_id}':
                return {
                    'statusCode': 400,
                    'headers': {'Content-Type': 'application/json'},
                    'body': json.dumps({
                        'error': 'Yandex API Gateway path parameter extraction issue',
                        'workaround': 'Use query parameter: /api/transcription?task_id=<task_id>'
                    })
                }

            return handle_download_transcription(task_id)
        elif method == 'GET' and path == '/api/pdf':
            # PDF download endpoint
            query_params = event.get('queryStringParameters') or {}
            task_id = query_params.get('task_id', '')
            logger.info("PDF download request for task_id: " + task_id)

            if not task_id:
                return {
                    'statusCode': 400,
                    'headers': {'Content-Type': 'application/json'},
                    'body': json.dumps({
                        'error': 'task_id query parameter is required',
                        'example': '/api/pdf?task_id=<task-id>'
                    })
                }

            return handle_download_pdf(task_id)
        elif method == 'GET' and path == '/api/mp3':
            # MP3 download endpoint
            query_params = event.get('queryStringParameters') or {}
            task_id = query_params.get('task_id', '')
            logger.info("MP3 download request for task_id: " + task_id)

            if not task_id:
                return {
                    'statusCode': 400,
                    'headers': {'Content-Type': 'application/json'},
                    'body': json.dumps({
                        'error': 'task_id query parameter is required',
                        'example': '/api/mp3?task_id=<task-id>'
                    })
                }

            return handle_download_mp3(task_id)
        elif method == 'GET' and path == '/api/status':
            # Query parameter workaround for broken path parameter extraction
            query_params = event.get('queryStringParameters') or {}
            task_id = query_params.get('task_id', '')
            logger.info("Status request via query parameter for task_id: " + task_id)

            if not task_id:
                return {
                    'statusCode': 400,
                    'headers': {'Content-Type': 'application/json'},
                    'body': json.dumps({
                        'error': 'task_id query parameter is required',
                        'example': '/api/status?task_id=<your-task-id>',
                        'available_tasks': list(get_tasks_from_storage().keys())[:5]
                    })
                }

            # Handle the task lookup
            return handle_task_status_lookup(task_id)
        elif method == 'GET' and path.startswith('/api/status/'):
            # Path parameter approach (currently broken due to Yandex API Gateway bug)
            task_id = path.split('/')[-1]
            logger.info("Status request for task_id: " + task_id + " (from path: " + path + ")")

            # Check if we got the literal "{task_id}" string (broken path parameter extraction)
            if task_id == '{task_id}':
                return {
                    'statusCode': 400,
                    'headers': {'Content-Type': 'application/json'},
                    'body': json.dumps({
                        'error': 'Yandex API Gateway path parameter extraction issue',
                        'workaround': 'Use query parameter: /api/status?task_id=<task_id>',
                        'available_tasks': list(get_tasks_from_storage().keys())[:5]
                    })
                }

            # Handle the task lookup
            return handle_task_status_lookup(task_id)
        elif method == 'GET' and path == '/api/abstract':
            # Download lecture abstract as markdown
            query_params = event.get('queryStringParameters') or {}
            task_id = query_params.get('task_id', '')
            logger.info("Abstract request for task_id: " + task_id)

            if not task_id:
                return {
                    'statusCode': 400,
                    'headers': {'Content-Type': 'application/json'},
                    'body': json.dumps({
                        'error': 'task_id query parameter is required',
                        'example': '/api/abstract?task_id=<task-id>'
                    })
                }

            return handle_get_abstract(task_id)
        else:
            logger.info("No route found for: " + method + " " + path)
            return {
                'statusCode': 404,
                'headers': {'Content-Type': 'application/json'},
                'body': json.dumps({'error': 'Not found'})
            }
    except Exception as e:
        logger.error(f"Error handling request: {e}")
        return {
            'statusCode': 500,
            'headers': {'Content-Type': 'application/json'},
            'body': json.dumps({'error': str(e)})
        }

def handle_index():
    """Serve the frontend HTML"""
    html_content = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>🎓 Lecture Notes Generator</title>
    <style>
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
            max-width: 1200px;
            margin: 0 auto;
            padding: 20px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
        }
        .main-container {
            background: white;
            padding: 40px;
            border-radius: 15px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.2);
            margin-bottom: 30px;
        }
        .queue-container {
            background: white;
            padding: 30px;
            border-radius: 15px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.2);
        }
        h1 {
            color: #333;
            text-align: center;
            margin-bottom: 10px;
            font-size: 2.5em;
        }
        .subtitle {
            text-align: center;
            color: #666;
            margin-bottom: 30px;
            font-size: 1.2em;
        }
        .form-group {
            margin-bottom: 25px;
        }
        label {
            display: block;
            margin-bottom: 8px;
            font-weight: 600;
            color: #555;
        }
        input[type="text"], input[type="url"], textarea {
            width: 100%;
            padding: 15px;
            border: 2px solid #e1e5e9;
            border-radius: 8px;
            font-size: 16px;
            box-sizing: border-box;
            transition: border-color 0.3s;
        }
        input[type="text"]:focus, input[type="url"]:focus, textarea:focus {
            outline: none;
            border-color: #667eea;
        }
        textarea {
            height: 120px;
            resize: vertical;
        }
        .submit-btn {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 15px 30px;
            border: none;
            border-radius: 8px;
            font-size: 18px;
            font-weight: 600;
            cursor: pointer;
            width: 100%;
            transition: transform 0.2s;
        }
        .submit-btn:hover {
            transform: translateY(-2px);
        }
        .submit-btn:disabled {
            opacity: 0.6;
            cursor: not-allowed;
            transform: none;
        }
        .queue-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 20px;
            padding-bottom: 15px;
            border-bottom: 2px solid #f0f0f0;
        }
        .queue-title {
            font-size: 1.5em;
            font-weight: 600;
            color: #333;
        }
        .empty-queue {
            text-align: center;
            padding: 40px;
            color: #666;
            font-style: italic;
        }
        .queue-item {
            background: #f8f9fa;
            border: 1px solid #e9ecef;
            border-radius: 10px;
            padding: 20px;
            margin-bottom: 15px;
            transition: all 0.3s ease;
        }
        .queue-item:hover {
            box-shadow: 0 4px 12px rgba(0,0,0,0.1);
        }
        .queue-item.completed {
            background: #d4edda;
            border-color: #c3e6cb;
        }
        .queue-item.processing {
            background: #fff3cd;
            border-color: #ffeaa7;
        }
        .queue-item-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 10px;
        }
        .queue-item-title {
            font-weight: 600;
            font-size: 1.1em;
            color: #333;
        }
        .queue-item-status {
            padding: 4px 12px;
            border-radius: 20px;
            font-size: 0.8em;
            font-weight: 600;
            text-transform: uppercase;
        }
        .status-processing {
            background: #ffc107;
            color: #856404;
        }
        .status-completed {
            background: #28a745;
            color: #fff;
        }
        .queue-item-details {
            margin: 10px 0;
            font-size: 0.9em;
            color: #666;
        }
        .progress-container {
            margin: 10px 0;
        }
        .progress-bar-container {
            width: 100%;
            height: 8px;
            background: #e9ecef;
            border-radius: 4px;
            overflow: hidden;
        }
        .progress-bar {
            height: 100%;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            transition: width 0.3s ease;
            border-radius: 4px;
        }
        .progress-text {
            text-align: center;
            margin-top: 5px;
            font-size: 0.8em;
            color: #666;
        }
        .transcription-container {
            margin-top: 15px;
            padding: 15px;
            background: #f8f9fa;
            border-radius: 8px;
            border-left: 4px solid #28a745;
        }
        .transcription-title {
            font-weight: 600;
            color: #333;
            margin-bottom: 10px;
            display: flex;
            align-items: center;
            gap: 8px;
        }
        .transcription-text {
            background: white;
            padding: 15px;
            border-radius: 5px;
            border: 1px solid #e9ecef;
            max-height: 200px;
            overflow-y: auto;
            font-family: 'Courier New', monospace;
            font-size: 0.85em;
            line-height: 1.4;
            white-space: pre-wrap;
        }
        .transcription-meta {
            margin-top: 10px;
            font-size: 0.8em;
            color: #6c757d;
        }
        .view-transcription-btn {
            background: #17a2b8;
            color: white;
            padding: 6px 12px;
            text-decoration: none;
            border-radius: 4px;
            font-size: 0.8em;
            margin-left: 10px;
            transition: background 0.3s;
        }
        .view-transcription-btn:hover {
            background: #138496;
            text-decoration: none;
            color: white;
        }
        .download-btn {
            background: #28a745;
            color: white;
            padding: 8px 16px;
            text-decoration: none;
            border-radius: 5px;
            margin-right: 10px;
            display: inline-block;
            font-size: 0.9em;
            transition: background 0.3s;
        }
        .download-btn:hover {
            background: #218838;
            text-decoration: none;
            color: white;
        }
        .delete-btn {
            background: #dc3545;
            color: white;
            padding: 8px 16px;
            text-decoration: none;
            border-radius: 5px;
            display: inline-block;
            font-size: 0.9em;
            border: none;
            cursor: pointer;
            transition: background 0.3s;
        }
        .delete-btn:hover {
            background: #c82333;
            text-decoration: none;
            color: white;
        }
        .task-meta {
            font-size: 0.8em;
            color: #999;
            margin-top: 10px;
        }
    </style>
</head>
<body>
    <div class="main-container">
        <h1>🎓 Lecture Notes Generator</h1>
        <p class="subtitle">Transform video lectures into organized notes with AI</p>

        <form id="lectureForm">
            <div class="form-group">
                <label for="title">📚 Lecture Title:</label>
                <input type="text" id="title" name="title" placeholder="e.g., Introduction to Machine Learning" required>
            </div>

            <div class="form-group">
                <label for="video_url">🎥 Video URL:</label>
                <input type="url" id="video_url" name="video_url" placeholder="https://youtube.com/watch?v=..." required>
            </div>

            <div class="form-group">
                <label for="description">📝 Description (Optional):</label>
                <textarea id="description" name="description" placeholder="Additional details about the lecture..."></textarea>
            </div>

            <button type="submit" class="submit-btn" id="submitBtn">
                🚀 Generate Lecture Notes
            </button>
        </form>
    </div>

    <div class="queue-container">
        <div class="queue-header">
            <h2 class="queue-title">📋 Processing Queue</h2>
            <span id="queueCount">0 items</span>
        </div>

        <div id="queueList">
            <div class="empty-queue">
                No lectures in queue. Submit a lecture above to get started!
            </div>
        </div>
    </div>

    <script>
        let queueData = {};

        // Show notification
        function showNotification(message, type = 'success') {
            alert(message);
        }

        // Format date
        function formatDate(dateString) {
            const date = new Date(dateString);
            return date.toLocaleString();
        }

        // Create queue item HTML
        function createQueueItemHTML(taskId, task) {
            const statusClass = task.status === 'completed' ? 'completed' : 'processing';
            const statusBadgeClass = task.status === 'completed' ? 'status-completed' : 'status-processing';

            let downloadHTML = '';
            if (task.status === 'completed') {
                downloadHTML = `
                    <div style="margin-top: 15px;">
                        <a href="/download/${taskId}/notes" class="download-btn">📄 Download Notes</a>
                        <button onclick="deleteTask('${taskId}')" class="delete-btn" style="margin-left: 10px;">🗑️ Delete</button>
                    </div>
                `;
            } else {
                // Also show delete button for processing tasks
                downloadHTML = `
                    <div style="margin-top: 15px;">
                        <button onclick="deleteTask('${taskId}')" class="delete-btn">🗑️ Delete</button>
                    </div>
                `;
            }

            let progressHTML = '';
            if (task.status === 'processing') {
                progressHTML = `
                    <div class="progress-container">
                        <div class="progress-bar-container">
                            <div class="progress-bar" style="width: ${task.progress}%"></div>
                        </div>
                        <div class="progress-text">${task.progress}% Complete</div>
                    </div>
                `;
            }

            let descriptionHTML = '';
            if (task.description) {
                descriptionHTML = `<div><strong>Description:</strong> ${task.description}</div>`;
            }

            let transcriptionHTML = '';
            if (task.status === 'completed' && task.transcription) {
                const shortTranscription = task.transcription.length > 300
                    ? task.transcription.substring(0, 300) + '...'
                    : task.transcription;

                transcriptionHTML = `
                    <div class="transcription-container">
                        <div class="transcription-title">
                            🎙️ SpeechKit Transcription
                        </div>
                        <div class="transcription-text">
                            ${shortTranscription}
                        </div>
                        ${task.video_duration ? `<div class="transcription-meta">Duration: ${Math.round(task.video_duration)}s | Characters: ${task.transcription.length}</div>` : ''}
                        <div style="margin-top: 10px;">
                            <a href="/api/transcription?task_id=${taskId}" class="download-btn" download>📄 Download Transcription</a>
                            ${task.abstract_url ? `<a href="/api/abstract?task_id=${taskId}" class="download-btn" download style="background: linear-gradient(135deg, #9b59b6, #8e44ad);">📝 Download Abstract (MD)</a>` : ''}
                            ${task.pdf_url ? `<a href="${task.pdf_url}" class="download-btn" download style="background: linear-gradient(135deg, #e74c3c, #c0392b);">📋 Download PDF Notes</a>` : ''}
                        </div>
                    </div>
                `;
            } else if (task.status === 'completed' && task.mp3_url) {
                // Show MP3 download if no transcription but MP3 is available
                transcriptionHTML = `
                    <div class="transcription-container">
                        <div class="transcription-title">
                            🎵 Audio Extracted
                        </div>
                        <div class="transcription-text">
                            Video has been converted to MP3 format.
                        </div>
                        ${task.video_duration ? `<div class="transcription-meta">Duration: ${Math.round(task.video_duration)}s</div>` : ''}
                        <div style="margin-top: 10px;">
                            <a href="/api/mp3?task_id=${taskId}" class="download-btn" download style="background: linear-gradient(135deg, #3498db, #2980b9);">🎵 Download MP3</a>
                            ${task.abstract_url ? `<a href="/api/abstract?task_id=${taskId}" class="download-btn" download style="background: linear-gradient(135deg, #9b59b6, #8e44ad);">📝 Download Abstract (MD)</a>` : ''}
                        </div>
                    </div>
                `;
            }

            return `
                <div class="queue-item ${statusClass}" id="task-${taskId}">
                    <div class="queue-item-header">
                        <div class="queue-item-title">${task.title}</div>
                        <div class="queue-item-status ${statusBadgeClass}">${task.status}</div>
                    </div>
                    <div class="queue-item-details">
                        <div><strong>URL:</strong> <a href="${task.video_url}" target="_blank">${task.video_url}</a></div>
                        ${descriptionHTML}
                    </div>
                    ${progressHTML}
                    ${transcriptionHTML}
                    ${downloadHTML}
                    <div class="task-meta">
                        Task ID: ${taskId} | Created: ${formatDate(task.created_at)}
                    </div>
                </div>
            `;
        }

        // Update queue display
        function updateQueueDisplay() {
            const queueList = document.getElementById('queueList');
            const queueCount = document.getElementById('queueCount');

            const taskIds = Object.keys(queueData);
            queueCount.textContent = `${taskIds.length} item${taskIds.length !== 1 ? 's' : ''}`;

            if (taskIds.length === 0) {
                queueList.innerHTML = `
                    <div class="empty-queue">
                        No lectures in queue. Submit a lecture above to get started!
                    </div>
                `;
                return;
            }

            // Sort tasks: processing first, then completed (newest first)
            const sortedTasks = taskIds.sort((a, b) => {
                const aTask = queueData[a];
                const bTask = queueData[b];

                if (aTask.status === 'processing' && bTask.status !== 'processing') return -1;
                if (bTask.status === 'processing' && aTask.status !== 'processing') return 1;

                return new Date(bTask.created_at) - new Date(aTask.created_at);
            });

            queueList.innerHTML = sortedTasks.map(taskId =>
                createQueueItemHTML(taskId, queueData[taskId])
            ).join('');
        }

        // Fetch all tasks from server
        async function fetchAllTasks() {
            try {
                const response = await fetch('/api/tasks');
                if (response.ok) {
                    queueData = await response.json();
                    updateQueueDisplay();
                }
            } catch (error) {
                console.error('Error fetching tasks:', error);
            }
        }

        // Submit form
        document.getElementById('lectureForm').addEventListener('submit', async (e) => {
            e.preventDefault();

            const submitBtn = document.getElementById('submitBtn');
            const originalText = submitBtn.textContent;

            submitBtn.disabled = true;
            submitBtn.textContent = '⏳ Submitting...';

            const data = {
                title: document.getElementById('title').value,
                video_url: document.getElementById('video_url').value,
                description: document.getElementById('description').value
            };

            try {
                const response = await fetch('/api/submit', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify(data)
                });

                if (response.ok) {
                    const result = await response.json();
                    queueData[result.task_id] = result.task;
                    updateQueueDisplay();
                    showNotification('Lecture added to queue successfully!', 'success');
                    e.target.reset();
                } else {
                    const error = await response.json();
                    showNotification(error.error || 'Failed to submit lecture', 'error');
                }
            } catch (error) {
                console.error('Error submitting form:', error);
                showNotification('Network error. Please try again.', 'error');
            } finally {
                submitBtn.disabled = false;
                submitBtn.textContent = originalText;
            }
        });

        // Delete task
        async function deleteTask(taskId) {
            if (!confirm('Are you sure you want to delete this task? This action cannot be undone.')) {
                return;
            }

            try {
                const response = await fetch('/api/tasks/delete', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({ task_id: taskId })
                });

                if (response.ok) {
                    const result = await response.json();
                    delete queueData[taskId];
                    updateQueueDisplay();
                    showNotification('Task deleted successfully!', 'success');
                } else {
                    const error = await response.json();
                    showNotification(error.error || 'Failed to delete task', 'error');
                }
            } catch (error) {
                console.error('Error deleting task:', error);
                showNotification('Network error. Please try again.', 'error');
            }
        }

        // Update individual task status
        async function updateTaskStatus(taskId) {
            try {
                const response = await fetch(`/api/status?task_id=${taskId}`);
                if (response.ok) {
                    const updatedTask = await response.json();
                    if (queueData[taskId]) {
                        queueData[taskId] = updatedTask;
                        updateQueueDisplay();
                    }
                }
            } catch (error) {
                console.error('Error updating task status:', error);
            }
        }

        // Main update loop
        function updateLoop() {
            // Fetch all tasks periodically
            fetchAllTasks();

            // Update individual processing tasks
            Object.keys(queueData).forEach(taskId => {
                const task = queueData[taskId];
                if (task.status === 'processing') {
                    updateTaskStatus(taskId);
                }
            });
        }

        // Initialize
        fetchAllTasks();
        setInterval(updateLoop, 3000); // Update every 3 seconds
    </script>
</body>
</html>"""

    return {
        'statusCode': 200,
        'headers': {'Content-Type': 'text/html'},
        'body': html_content
    }

def handle_get_all_tasks():
    """Handle GET /api/tasks"""
    tasks = get_tasks_from_storage()
    return {
        'statusCode': 200,
        'headers': {'Content-Type': 'application/json'},
        'body': json.dumps(tasks)
    }

def validate_yandex_disk_link(video_url):
    """Validate Yandex Disk public link and get file metadata"""
    try:
        # Check if this is a Yandex Disk public link
        # Handle all formats: /d/ (download), /i/ (resource info)
        is_yandex_disk = bool(re.match(r'https://(disk\.yandex\.[a-z]+|disk\.360\.yandex\.[a-z]+|yadi\.sk)/(d|i)/', video_url))

        if not is_yandex_disk:
            # Not a Yandex Disk link, assume it's valid
            return {
                'is_valid': True,
                'is_yandex_disk': False,
                'message': 'Not a Yandex Disk link'
            }

        # Use the full URL for Yandex Disk API validation
        # The /i/ links (resource info) require the full URL to work
        # Using full URL for all link types is the safest approach
        logger.info(f"Validating Yandex Disk link (using full URL): {video_url}")

        # Call Yandex Disk API to validate the public link (URL-encode the full URL)
        encoded_key = quote(video_url, safe='')
        api_url = f"https://cloud-api.yandex.net/v1/disk/public/resources?public_key={encoded_key}"
        logger.info(f"Encoded key: {encoded_key[:100]}...")
        logger.info(f"API URL: {api_url[:150]}...")

        headers = {}
        # Add OAuth token if available for better rate limits
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

def handle_submit_task(event):
    """Handle POST /api/submit"""
    try:
        body = json.loads(event.get('body', '{}'))

        title = body.get('title')
        video_url = body.get('video_url')

        if not title or not video_url:
            return {
                'statusCode': 400,
                'headers': {'Content-Type': 'application/json'},
                'body': json.dumps({'error': 'Please provide both title and video URL'})
            }

        # Validate Yandex Disk link if applicable
        logger.info(f"Validating video URL: {video_url}")
        validation_result = validate_yandex_disk_link(video_url)

        if not validation_result.get('is_valid', False):
            logger.error(f"Video URL validation failed: {validation_result}")
            return {
                'statusCode': 400,
                'headers': {'Content-Type': 'application/json'},
                'body': json.dumps({
                    'error': 'Invalid video URL',
                    'validation_details': validation_result
                })
            }

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

                return {
                    'statusCode': 200,
                    'headers': {'Content-Type': 'application/json'},
                    'body': json.dumps({
                        'task_id': task_id,
                        'task': task,
                        'message': 'Lecture added to queue successfully'
                    })
                }
            except Exception as e:
                logger.error(f"Failed to add task to queue: {e}")
                return {
                    'statusCode': 500,
                    'headers': {'Content-Type': 'application/json'},
                    'body': json.dumps({'error': 'Task saved but failed to queue for processing'})
                }
        else:
            return {
                'statusCode': 500,
                'headers': {'Content-Type': 'application/json'},
                'body': json.dumps({'error': 'Failed to save task to storage'})
            }
    except Exception as e:
        logger.error(f"Error in handle_submit_task: {e}")
        return {
            'statusCode': 500,
            'headers': {'Content-Type': 'application/json'},
            'body': json.dumps({'error': str(e)})
        }

def handle_task_status_lookup(task_id):
    """Shared function for task status lookup"""
    try:
        tasks = get_tasks_from_storage()
        logger.info("Looking for task_id: " + task_id + " in " + str(len(tasks)) + " tasks")

        if task_id in tasks:
            logger.info("Found task: " + task_id)
            return {
                'statusCode': 200,
                'headers': {'Content-Type': 'application/json'},
                'body': json.dumps(tasks[task_id])
            }
        else:
            logger.warning("Task not found: " + task_id)
            return {
                'statusCode': 404,
                'headers': {'Content-Type': 'application/json'},
                'body': json.dumps({
                    'error': 'Task not found',
                    'task_id': task_id,
                    'available_tasks': list(tasks.keys())
                })
            }
    except Exception as e:
        logger.error("Error in handle_task_status_lookup: " + str(e))
        return {
            'statusCode': 500,
            'headers': {'Content-Type': 'application/json'},
            'body': json.dumps({'error': str(e)})
        }

def handle_delete_task(task_id):
    """Handle DELETE /api/tasks/{task_id}"""
    try:
        tasks = get_tasks_from_storage()
        logger.info("Attempting to delete task_id: " + task_id + " from " + str(len(tasks)) + " tasks")

        if task_id not in tasks:
            return {
                'statusCode': 404,
                'headers': {'Content-Type': 'application/json'},
                'body': json.dumps({
                    'error': 'Task not found',
                    'task_id': task_id,
                    'available_tasks': list(tasks.keys())
                })
            }

        # Delete task file from S3
        try:
            s3_client.delete_object(Bucket=BUCKET_NAME, Key=f'tasks/{task_id}.json')
            logger.info(f"Deleted task file: tasks/{task_id}.json")
        except Exception as e:
            logger.error(f"Failed to delete task file: {e}")
            # Continue even if file deletion fails

        # Also delete transcription file if it exists
        try:
            s3_client.delete_object(Bucket=BUCKET_NAME, Key=f'transcriptions/{task_id}.txt')
            logger.info(f"Deleted transcription file: transcriptions/{task_id}.txt")
        except Exception as e:
            logger.info(f"No transcription file to delete: {e}")

        # Also delete notes PDF if it exists
        try:
            s3_client.delete_object(Bucket=BUCKET_NAME, Key=f'results/{task_id}/notes.pdf')
            logger.info(f"Deleted notes PDF: results/{task_id}/notes.pdf")
        except Exception as e:
            logger.info(f"No notes PDF to delete: {e}")

        # Also delete MP3 if it exists
        try:
            s3_client.delete_object(Bucket=BUCKET_NAME, Key=f'mp3/{task_id}.mp3')
            logger.info(f"Deleted MP3: mp3/{task_id}.mp3")
        except Exception as e:
            logger.info(f"No MP3 to delete: {e}")

        return {
            'statusCode': 200,
            'headers': {'Content-Type': 'application/json'},
            'body': json.dumps({
                'message': 'Task deleted successfully',
                'task_id': task_id,
                'deleted_task': tasks[task_id]
            })
        }

    except Exception as e:
        logger.error("Error in handle_delete_task: " + str(e))
        return {
            'statusCode': 500,
            'headers': {'Content-Type': 'application/json'},
            'body': json.dumps({'error': str(e)})
        }

def handle_download_transcription(task_id):
    """Handle transcription download"""
    try:
        tasks = get_tasks_from_storage()

        if task_id not in tasks:
            return {
                'statusCode': 404,
                'headers': {'Content-Type': 'application/json'},
                'body': json.dumps({
                    'error': 'Task not found',
                    'task_id': task_id
                })
            }

        task = tasks[task_id]

        # Check if task has transcription
        if not task.get('transcription'):
            return {
                'statusCode': 404,
                'headers': {'Content-Type': 'application/json'},
                'body': json.dumps({
                    'error': 'No transcription available for this task',
                    'task_id': task_id,
                    'task_status': task.get('status', 'unknown')
                })
            }

        # Prepare transcription content
        transcription_content = f"""Lecture Transcription
=====================

Title: {task.get('title', 'Unknown')}
Video URL: {task.get('video_url', 'Unknown')}
Task ID: {task_id}
Status: {task.get('status', 'Unknown')}
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

        # Return as downloadable text file
        return {
            'statusCode': 200,
            'headers': {
                'Content-Type': 'text/plain',
                'Content-Disposition': f'attachment; filename="transcription_{task_id}.txt"',
                'Access-Control-Allow-Origin': '*'
            },
            'body': transcription_content
        }

    except Exception as e:
        logger.error("Error in handle_download_transcription: " + str(e))
        return {
            'statusCode': 500,
            'headers': {'Content-Type': 'application/json'},
            'body': json.dumps({'error': str(e)})
        }

def handle_download_pdf(task_id):
    """Handle PDF download"""
    try:
        tasks = get_tasks_from_storage()

        if task_id not in tasks:
            return {
                'statusCode': 404,
                'headers': {'Content-Type': 'application/json'},
                'body': json.dumps({
                    'error': 'Task not found',
                    'task_id': task_id
                })
            }

        task = tasks[task_id]

        # Check if task has PDF
        if not task.get('pdf_url'):
            return {
                'statusCode': 404,
                'headers': {'Content-Type': 'application/json'},
                'body': json.dumps({
                    'error': 'PDF not available for this task',
                    'task_id': task_id,
                    'task_status': task.get('status', 'unknown')
                })
            }

        pdf_url = task.get('pdf_url')

        # Redirect to the PDF URL in object storage
        return {
            'statusCode': 302,
            'headers': {
                'Location': pdf_url,
                'Access-Control-Allow-Origin': '*'
            },
            'body': ''
        }

    except Exception as e:
        logger.error("Error in handle_download_pdf: " + str(e))
        return {
            'statusCode': 500,
            'headers': {'Content-Type': 'application/json'},
            'body': json.dumps({'error': str(e)})
        }

def handle_download_mp3(task_id):
    """Handle MP3 download"""
    try:
        tasks = get_tasks_from_storage()

        if task_id not in tasks:
            return {
                'statusCode': 404,
                'headers': {'Content-Type': 'application/json'},
                'body': json.dumps({
                    'error': 'Task not found',
                    'task_id': task_id
                })
            }

        task = tasks[task_id]

        # Check if task has MP3
        if not task.get('mp3_url'):
            return {
                'statusCode': 404,
                'headers': {'Content-Type': 'application/json'},
                'body': json.dumps({
                    'error': 'MP3 not available for this task',
                    'task_id': task_id,
                    'task_status': task.get('status', 'unknown')
                })
            }

        mp3_url = task.get('mp3_url')

        # Redirect to the MP3 URL in object storage
        return {
            'statusCode': 302,
            'headers': {
                'Location': mp3_url,
                'Access-Control-Allow-Origin': '*'
            },
            'body': ''
        }

    except Exception as e:
        logger.error("Error in handle_download_mp3: " + str(e))
        return {
            'statusCode': 500,
            'headers': {'Content-Type': 'application/json'},
            'body': json.dumps({'error': str(e)})
        }

def handle_get_abstract(task_id):
    """Handle lecture abstract download as markdown"""
    try:
        tasks = get_tasks_from_storage()

        if task_id not in tasks:
            return {
                'statusCode': 404,
                'headers': {'Content-Type': 'application/json'},
                'body': json.dumps({
                    'error': 'Task not found',
                    'task_id': task_id
                })
            }

        task = tasks[task_id]

        # Check if task has abstract
        if not task.get('abstract_url'):
            return {
                'statusCode': 404,
                'headers': {'Content-Type': 'application/json'},
                'body': json.dumps({
                    'error': 'Abstract not available for this task',
                    'task_id': task_id,
                    'task_status': task.get('status', 'unknown'),
                    'hint': 'Abstract may still be generating or transcription failed'
                })
            }

        abstract_url = task.get('abstract_url')

        # Redirect to the abstract URL in object storage
        return {
            'statusCode': 302,
            'headers': {
                'Location': abstract_url,
                'Access-Control-Allow-Origin': '*'
            },
            'body': ''
        }

    except Exception as e:
        logger.error("Error in handle_get_abstract: " + str(e))
        return {
            'statusCode': 500,
            'headers': {'Content-Type': 'application/json'},
            'body': json.dumps({'error': str(e)})
        }

def handle_get_status(task_id):
    """Handle GET /api/status/{task_id}"""
    return handle_task_status_lookup(task_id)

def handle_direct_request(event):
    """Handle direct function invocation"""
    return {
        'statusCode': 200,
        'headers': {'Content-Type': 'application/json'},
        'body': json.dumps({'message': 'Function is working'})
    }

if __name__ == '__main__':
    # For local testing
    app.run(host='0.0.0.0', port=8080, debug=True)# Force update - Thu Dec 18 09:45:18 AM MSK 2025
# Debug update - Thu Dec 18 09:50:04 AM MSK 2025
# Simplified path parameter handling - Thu Dec 18 09:55:00 AM MSK 2025
# Switch to query parameters - Thu Dec 18 10:15:00 AM MSK 2025
# Hybrid approach for testing - Thu Dec 18 10:20:00 AM MSK 2025
# Dual-route workaround - Thu Dec 18 10:25:00 AM MSK 2025
# Inline implementation fix - Thu Dec 18 10:30:00 AM MSK 2025
# Query parameter workaround - Thu Dec 18 10:35:00 AM MSK 2025
# Add delete and transcription download - Thu Dec 18 10:40:00 AM MSK 2025
