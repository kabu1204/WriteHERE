from flask import Flask, request, jsonify
from flask_cors import CORS
from flask_socketio import SocketIO, emit
import os
import json
import subprocess
import uuid
import tempfile
import threading
import time
import shutil
import re
import signal
import argparse
from pathlib import Path
from datetime import datetime, timedelta
import logging
from flask_jwt_extended import JWTManager, create_access_token, jwt_required, get_jwt_identity

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(os.path.join(os.path.dirname(__file__), 'server.log'))
    ]
)
logger = logging.getLogger(__name__)

# Parse command-line arguments
parser = argparse.ArgumentParser(description='Backend server for WriteHERE application')
parser.add_argument('--port', type=int, default=5001, help='Port to run the server on')
args = parser.parse_args()

app = Flask(__name__)
# JWT configuration
app.config['JWT_SECRET_KEY'] = os.environ.get('JWT_SECRET_KEY', 'writehere-dev-secret')
app.config['JWT_ACCESS_TOKEN_EXPIRES'] = timedelta(hours=8)
jwt = JWTManager(app)

@jwt.invalid_token_loader
def invalid_token_callback(error):
    logger.error(f"Invalid token error: {error}")
    return jsonify({"msg": "Invalid token", "error": error}), 401

@jwt.unauthorized_loader
def missing_token_callback(error):
    logger.error(f"Missing token error: {error}")
    return jsonify({"msg": "Missing token", "error": error}), 401

@jwt.expired_token_loader
def expired_token_callback(jwt_header, jwt_payload):
    logger.error(f"Expired token: {jwt_payload}")
    return jsonify({"msg": "Token has expired", "error": "token_expired"}), 401

# Enable CORS with more specific options
CORS(app, resources={r"/*": {"origins": "*"}}, supports_credentials=True)
# Initialize Socket.IO with broader CORS settings for development
socketio = SocketIO(app, 
                    cors_allowed_origins="*", 
                    async_mode='threading',
                    logger=False, # disable logger
                    engineio_logger=False)

# Storage for task status and results
task_storage = {}
RESULTS_DIR = os.path.join(os.path.dirname(__file__), 'results')
os.makedirs(RESULTS_DIR, exist_ok=True)

ADMIN_USERS_FILE = os.environ.get('ADMIN_USERS_FILE', os.path.join(os.path.dirname(__file__), 'admin_users.json'))
ADMIN_USERS_LAST_LOADED = None
ALLOWED_USERS = {}

def load_admin_users_from_file(path):
    if not os.path.exists(path):
        logger.warning(f"Admin users file not found at {path}. Login is disabled until the file is created.")
        return {}

    try:
        with open(path, 'r', encoding='utf-8') as f:
            raw_data = json.load(f)
    except Exception as exc:
        logger.error(f"Failed to read admin users file {path}: {exc}")
        return {}

    entries = []
    if isinstance(raw_data, dict):
        if isinstance(raw_data.get('users'), list):
            entries = raw_data['users']
        else:
            entries = [raw_data]
    elif isinstance(raw_data, list):
        entries = raw_data

    normalized = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        email = (entry.get('email') or '').strip().lower()
        password = entry.get('password')
        name = entry.get('name') or email
        if not email or not password:
            continue
        normalized[email] = {
            "password": password,
            "name": name or email
        }

    if not normalized:
        logger.warning(f"No valid admin users were found in {path}.")

    return normalized


def refresh_admin_users(force=False):
    global ALLOWED_USERS, ADMIN_USERS_LAST_LOADED
    try:
        logger.info(f"Refreshing admin users from file {ADMIN_USERS_FILE}")
        if not os.path.exists(ADMIN_USERS_FILE):
            ALLOWED_USERS = {}
            ADMIN_USERS_LAST_LOADED = None
            return ALLOWED_USERS

        last_modified = os.path.getmtime(ADMIN_USERS_FILE)
        if force or ADMIN_USERS_LAST_LOADED != last_modified:
            ALLOWED_USERS = load_admin_users_from_file(ADMIN_USERS_FILE)
            ADMIN_USERS_LAST_LOADED = last_modified
            logger.info(f"Loaded {len(ALLOWED_USERS)} admin user(s) from {ADMIN_USERS_FILE}")
    except Exception as exc:
        logger.error(f"Error refreshing admin users: {exc}")
        ALLOWED_USERS = {}
        ADMIN_USERS_LAST_LOADED = None

    return ALLOWED_USERS

refresh_admin_users(force=True)

def reload_task_storage():
    """Reload task storage from the file system"""
    global task_storage
    
    logger.info("Reloading task storage from file system")
    # Iterate through all folders in the results directory
    for task_id in os.listdir(RESULTS_DIR):
        task_dir = os.path.join(RESULTS_DIR, task_id)
        if not os.path.isdir(task_dir):
            continue
            
        # Check if this is a completed task with results
        result_file = os.path.join(task_dir, 'result.jsonl')
        done_file = os.path.join(task_dir, 'done.txt')
        
        if os.path.exists(result_file):
            # Add task to storage if not already there
            if task_id not in task_storage:
                creation_time = os.path.getctime(task_dir)
                task_storage[task_id] = {
                    "status": "completed" if os.path.exists(done_file) else "running",
                    "start_time": creation_time
                }
                
                # Try to extract model information from run.sh
                run_sh_file = os.path.join(task_dir, 'run.sh')
                if os.path.exists(run_sh_file):
                    try:
                        with open(run_sh_file, 'r') as f:
                            run_script = f.read()
                            # Extract model name from command line arguments
                            model_match = run_script.split("--model ")[1].split(" ")[0] if "--model " in run_script else None
                            if model_match:
                                task_storage[task_id]["model"] = model_match
                            
                            # Check if it's a report with search
                            if "--engine-backend " in run_script:
                                engine_backend = run_script.split("--engine-backend ")[1].split(" ")[0]
                                if engine_backend != "none":
                                    task_storage[task_id]["search_engine"] = engine_backend
                    except Exception as e:
                        logger.error(f"Error extracting model info from run.sh for {task_id}: {str(e)}")
                
                # Load result if available
                try:
                    with open(result_file, 'r') as f:
                        result_data = json.load(f)
                        task_storage[task_id]["result"] = result_data.get("result", "No result available")
                except Exception as e:
                    logger.error(f"Error loading result file for {task_id}: {str(e)}")
                    task_storage[task_id]["error"] = f"Failed to load output file: {str(e)}"

# Load existing tasks on startup
reload_task_storage()

def run_story_generation(task_id, prompt, model, api_keys, provider=None):
    """
    Run the story generation script as a subprocess
    """
    logger.info(f"Starting story generation for task {task_id} with model {model}")
    task_dir = os.path.join(RESULTS_DIR, task_id)
    os.makedirs(task_dir, exist_ok=True)
    
    # Create a records directory for nodes.json
    records_dir = os.path.join(task_dir, 'records')
    os.makedirs(records_dir, exist_ok=True)
    
    # Create a temporary input file with the prompt
    input_file = os.path.join(task_dir, 'input.jsonl')
    with open(input_file, 'w') as f:
        json.dump({
            "id": task_id,
            "field": "inputs",
            "value": prompt,
            "ori": {"example_id": task_id, "inputs": prompt, "subset": "user"}
        }, f)
        f.write('\n')
    
    output_file = os.path.join(task_dir, 'result.jsonl')
    done_file = os.path.join(task_dir, 'done.txt')
    nodes_file = os.path.join(records_dir, 'nodes.json')
    
    # Create environment file with API keys
    env_file = os.path.join(task_dir, 'api_key.env')
    with open(env_file, 'w') as f:
        if 'openai' in api_keys and api_keys['openai']:
            f.write(f"OPENAI={api_keys['openai']}\n")
        if 'claude' in api_keys and api_keys['claude']:
            f.write(f"CLAUDE={api_keys['claude']}\n")
        if 'gemini' in api_keys and api_keys['gemini']:
            f.write(f"GEMINI={api_keys['gemini']}\n")
        if 'serpapi' in api_keys and api_keys['serpapi']:
            f.write(f"SERPAPI={api_keys['serpapi']}\n")
        if 'openrouter' in api_keys and api_keys['openrouter']:
            f.write(f"OPENROUTER={api_keys['openrouter']}\n")
        if 'deepseek' in api_keys and api_keys['deepseek']:
            f.write(f"DEEPSEEK={api_keys['deepseek']}\n")
        if 'glm' in api_keys and api_keys['glm']:
            f.write(f"GLM={api_keys['glm']}\n")
    
    # Create a script to run the engine with the appropriate environment
    script_path = os.path.join(task_dir, 'run.sh')
    provider_arg = f"--provider {provider}" if provider else ""
    with open(script_path, 'w') as f:
        f.write(f"""#!/bin/bash
        cd {os.path.abspath(os.path.join(os.path.dirname(__file__), '../recursive'))}
        source {env_file}
        export TASK_ENV_FILE={env_file}
        python engine.py --filename {input_file} --output-filename {output_file} --done-flag-file {done_file} --model {model} {provider_arg} --mode story --nodes-json-file {nodes_file}
        """)
    
    os.chmod(script_path, 0o755)
    
    # Update task status to "running"
    task_storage[task_id] = {
        "status": "running", 
        "start_time": time.time(),
        "model": model,
        "provider": provider
    }
    
    # Start task progress monitoring in a background thread
    monitoring_thread = threading.Thread(
        target=monitor_task_progress,
        args=(task_id, records_dir)
    )
    monitoring_thread.daemon = True
    monitoring_thread.start()
    
    try:
        # Run the script
        logger.info(f"Executing run script for task {task_id}")
        process = subprocess.Popen(['/bin/bash', script_path], 
                                   stdout=subprocess.PIPE, 
                                   stderr=subprocess.PIPE)
        # Store the process object in task_storage for later termination
        task_storage[task_id]["process"] = process
        stdout, stderr = process.communicate()
        
        # Check if the process completed successfully
        if process.returncode == 0:
            logger.info(f"Task {task_id} completed successfully")
            task_storage[task_id]["status"] = "completed"
            # Store the result if available
            if os.path.exists(output_file) and os.path.getsize(output_file) > 0:
                try:
                    with open(output_file, 'r') as f:
                        result_data = json.load(f)
                        result_content = result_data.get("result", "No result available")
                        if isinstance(result_content, dict) and "error" in result_content:
                             task_storage[task_id]["status"] = "error"
                             task_storage[task_id]["error"] = result_content["error"]
                        else:
                             task_storage[task_id]["result"] = result_content
                except json.JSONDecodeError:
                    logger.error(f"Failed to decode JSON from {output_file}")
                    task_storage[task_id]["status"] = "error"
                    task_storage[task_id]["error"] = "Failed to decode output file"
            else:
                logger.error(f"Output file not generated or empty for task {task_id}")
                task_storage[task_id]["status"] = "error"
                task_storage[task_id]["error"] = "Output file not generated or empty"
        else:
            error_msg = stderr.decode('utf-8')
            logger.error(f"Task {task_id} failed with error: {error_msg}")
            task_storage[task_id]["status"] = "error"
            task_storage[task_id]["error"] = error_msg
    except Exception as e:
        logger.error(f"Exception during task {task_id} execution: {str(e)}")
        task_storage[task_id]["status"] = "error"
        task_storage[task_id]["error"] = str(e)

def run_report_generation(task_id, prompt, model, enable_search, search_engine, api_keys, provider=None):
    """
    Run the report generation script as a subprocess
    """
    logger.info(f"Starting report generation for task {task_id} with model {model}, search={enable_search}")
    task_dir = os.path.join(RESULTS_DIR, task_id)
    os.makedirs(task_dir, exist_ok=True)
    
    # Create a records directory for nodes.json
    records_dir = os.path.join(task_dir, 'records')
    os.makedirs(records_dir, exist_ok=True)
    
    # Create a temporary input file with the prompt
    input_file = os.path.join(task_dir, 'input.jsonl')
    with open(input_file, 'w') as f:
        json.dump({
            "topic": "",
            "intent": "",
            "domain": "",
            "id": task_id,
            "prompt": prompt
        }, f)
        f.write('\n')
    
    output_file = os.path.join(task_dir, 'result.jsonl')
    done_file = os.path.join(task_dir, 'done.txt')
    nodes_file = os.path.join(records_dir, 'nodes.json')
    
    # Create environment file with API keys
    env_file = os.path.join(task_dir, 'api_key.env')
    with open(env_file, 'w') as f:
        if 'openai' in api_keys and api_keys['openai']:
            f.write(f"OPENAI={api_keys['openai']}\n")
        if 'claude' in api_keys and api_keys['claude']:
            f.write(f"CLAUDE={api_keys['claude']}\n")
        if 'gemini' in api_keys and api_keys['gemini']:
            f.write(f"GEMINI={api_keys['gemini']}\n")
        if 'serpapi' in api_keys and api_keys['serpapi']:
            f.write(f"SERPAPI={api_keys['serpapi']}\n")
        if 'openrouter' in api_keys and api_keys['openrouter']:
            f.write(f"OPENROUTER={api_keys['openrouter']}\n")
        if 'deepseek' in api_keys and api_keys['deepseek']:
            f.write(f"DEEPSEEK={api_keys['deepseek']}\n")
        if 'glm' in api_keys and api_keys['glm']:
            f.write(f"GLM={api_keys['glm']}\n")
    
    # Create a script to run the engine with the appropriate environment
    script_path = os.path.join(task_dir, 'run.sh')
    engine_backend = search_engine if enable_search else "none"
    provider_arg = f"--provider {provider}" if provider else ""
    
    with open(script_path, 'w') as f:
        f.write(f"""#!/bin/bash
        cd {os.path.abspath(os.path.join(os.path.dirname(__file__), '../recursive'))}
        source {env_file}
        export TASK_ENV_FILE={env_file}
        python engine.py --filename {input_file} --output-filename {output_file} --done-flag-file {done_file} --model {model} {provider_arg} --engine-backend {engine_backend} --mode report --nodes-json-file {nodes_file}
        """)
    
    os.chmod(script_path, 0o755)
    
    # Update task status to "running"
    task_storage[task_id] = {
        "status": "running", 
        "start_time": time.time(),
        "model": model,
        "search_engine": engine_backend if enable_search else None,
        "provider": provider
    }
    
    # Start task progress monitoring in a background thread
    monitoring_thread = threading.Thread(
        target=monitor_task_progress,
        args=(task_id, records_dir)
    )
    monitoring_thread.daemon = True
    monitoring_thread.start()
    
    try:
        # Run the script
        logger.info(f"Executing run script for task {task_id}")
        process = subprocess.Popen(['/bin/bash', script_path], 
                                   stdout=subprocess.PIPE, 
                                   stderr=subprocess.PIPE)
        # Store the process object in task_storage for later termination
        task_storage[task_id]["process"] = process
        stdout, stderr = process.communicate()
        
        # Check if the process completed successfully
        if process.returncode == 0:
            logger.info(f"Task {task_id} completed successfully")
            task_storage[task_id]["status"] = "completed"
            # Store the result if available
            if os.path.exists(output_file) and os.path.getsize(output_file) > 0:
                try:
                    with open(output_file, 'r') as f:
                        result_data = json.load(f)
                        result_content = result_data.get("result", "No result available")
                        if isinstance(result_content, dict) and "error" in result_content:
                             task_storage[task_id]["status"] = "error"
                             task_storage[task_id]["error"] = result_content["error"]
                        else:
                             task_storage[task_id]["result"] = result_content
                except json.JSONDecodeError:
                    logger.error(f"Failed to decode JSON from {output_file}")
                    task_storage[task_id]["status"] = "error"
                    task_storage[task_id]["error"] = "Failed to decode output file"
            else:
                logger.error(f"Output file not generated or empty for task {task_id}")
                task_storage[task_id]["status"] = "error"
                task_storage[task_id]["error"] = "Output file not generated or empty"
        else:
            error_msg = stderr.decode('utf-8')
            logger.error(f"Task {task_id} failed with error: {error_msg}")
            task_storage[task_id]["status"] = "error"
            task_storage[task_id]["error"] = error_msg
    except Exception as e:
        logger.error(f"Exception during task {task_id} execution: {str(e)}")
        task_storage[task_id]["status"] = "error"
        task_storage[task_id]["error"] = str(e)


@app.route('/api/login', methods=['POST'])
def api_login():
    data = request.json or {}
    email = (data.get('email') or '').strip().lower()
    password = data.get('password') or ''
    logger.info(f"Login attempt for user {email}")

    users = refresh_admin_users()
    user = users.get(email)
    if not user or user['password'] != password:
        logger.warning(f"Invalid login attempt for user {email}")
        return jsonify({"error": "Invalid email or password"}), 401

    access_token = create_access_token(identity=email, additional_claims={"name": user['name']})
    return jsonify({
        "access_token": access_token,
        "user": {
            "email": email,
            "name": user['name']
        }
    })

@app.route('/api/generate-story', methods=['POST'])
@jwt_required()
def api_generate_story():
    data = request.json
    current_user_email = get_jwt_identity()
    logger.info(f"[{current_user_email}] Received generate-story request: {data.get('model')}")
    
    # Validate request
    required_fields = ['prompt', 'model', 'apiKeys']
    for field in required_fields:
        if field not in data:
            logger.warning(f"Missing required field in generate-story: {field}")
            return jsonify({"error": f"Missing required field: {field}"}), 400
    
    # Generate a unique task ID
    task_id = f"story-{uuid.uuid4()}"
    logger.info(f"Created task ID: {task_id}")
    
    # Start the generation in a background thread
    thread = threading.Thread(
        target=run_story_generation,
        args=(task_id, data['prompt'], data['model'], data['apiKeys'], data.get('provider'))
    )
    thread.start()
    
    return jsonify({
        "taskId": task_id,
        "status": "started"
    })

@app.route('/api/generate-report', methods=['POST'])
@jwt_required()
def api_generate_report():
    data = request.json
    current_user_email = get_jwt_identity()
    logger.info(f"[{current_user_email}] Received generate-report request: {data.get('model')}")
    
    # Validate request
    required_fields = ['prompt', 'model', 'apiKeys']
    for field in required_fields:
        if field not in data:
            logger.warning(f"Missing required field in generate-report: {field}")
            return jsonify({"error": f"Missing required field: {field}"}), 400
    
    # Set defaults
    enable_search = data.get('enableSearch', True)
    search_engine = data.get('searchEngine', 'google')
    
    # Generate a unique task ID
    task_id = f"report-{uuid.uuid4()}"
    logger.info(f"Created task ID: {task_id}")
    
    # Start the generation in a background thread
    thread = threading.Thread(
        target=run_report_generation,
        args=(task_id, data['prompt'], data['model'], enable_search, search_engine, data['apiKeys'], data.get('provider'))
    )
    thread.start()
    
    return jsonify({
        "taskId": task_id,
        "status": "started"
    })

@app.route('/api/status/<task_id>', methods=['GET'])
@jwt_required()
def api_get_status(task_id):
    # If task is not in memory, try to load it
    if task_id not in task_storage:
        task_dir = os.path.join(RESULTS_DIR, task_id)
        # logger.debug(f"Checking status for task {task_id}, not in memory")
        if os.path.isdir(task_dir):
            # Load task into memory
            result_file = os.path.join(task_dir, 'result.jsonl')
            done_file = os.path.join(task_dir, 'done.txt')
            
            if os.path.exists(result_file):
                creation_time = os.path.getctime(task_dir)
                task_storage[task_id] = {
                    "status": "completed" if os.path.exists(done_file) else "running",
                    "start_time": creation_time
                }
                
                # Load result if available
                try:
                    with open(result_file, 'r') as f:
                        result_data = json.load(f)
                        task_storage[task_id]["result"] = result_data.get("result", "No result available")
                except Exception as e:
                    logger.error(f"Error loading result file for {task_id}: {str(e)}")
                    task_storage[task_id]["error"] = f"Failed to load output file: {str(e)}"
            else:
                return jsonify({"error": "Task not found or incomplete"}), 404
        else:
            return jsonify({"error": "Task not found"}), 404
    
    task = task_storage[task_id]
    
    # Check if we need to update status from the done file
    task_dir = os.path.join(RESULTS_DIR, task_id)
    done_file = os.path.join(task_dir, 'done.txt')
    
    if task["status"] == "running" and os.path.exists(done_file):
        task["status"] = "completed"
    
    return jsonify({
        "taskId": task_id,
        "status": task["status"],
        "error": task.get("error"),
        "elapsedTime": time.time() - task["start_time"],
        "model": task.get("model", "unknown"),
        "searchEngine": task.get("search_engine")
    })

@app.route('/api/result/<task_id>', methods=['GET'])
@jwt_required()
def api_get_result(task_id):
    logger.info(f"Fetching result for task {task_id}")
    # If task is not in memory, try to load it
    if task_id not in task_storage:
        task_dir = os.path.join(RESULTS_DIR, task_id)
        if os.path.isdir(task_dir):
            # Load task into memory
            result_file = os.path.join(task_dir, 'result.jsonl')
            done_file = os.path.join(task_dir, 'done.txt')
            
            if os.path.exists(result_file):
                creation_time = os.path.getctime(task_dir)
                task_storage[task_id] = {
                    "status": "completed" if os.path.exists(done_file) else "running",
                    "start_time": creation_time
                }
                
                # Load result if available
                try:
                    with open(result_file, 'r') as f:
                        result_data = json.load(f)
                        task_storage[task_id]["result"] = result_data.get("result", "No result available")
                except Exception as e:
                    logger.error(f"Error loading result file for {task_id}: {str(e)}")
                    task_storage[task_id]["error"] = f"Failed to load output file: {str(e)}"
                    return jsonify({"error": f"Failed to load output file: {str(e)}"}), 500
            else:
                return jsonify({"error": "Task result file not found"}), 404
        else:
            return jsonify({"error": "Task not found"}), 404
    
    result_md_dir = os.path.join(RESULTS_DIR, 'records', task_id, 'report.md')
    task = task_storage[task_id]
    
    # We'll allow getting results even if status is not completed as long as we have the result data
    if "result" not in task:
        # Check if the result.md file exists
        if not os.path.exists(result_md_dir):
            return jsonify({"error": "Task result not available"}), 400
        else:
            with open(result_md_dir, 'r') as f:
                task["result"] = f.read()
        # Check if the result.md file exists
        if not os.path.exists(result_md_dir):
            return jsonify({"error": "Task result not available"}), 400
        else:
            with open(result_md_dir, 'r') as f:
                task["result"] = f.read()
    
    return jsonify({
        "taskId": task_id,
        "result": task.get("result", "No result available"),
        "model": task.get("model", "unknown"),
        "searchEngine": task.get("search_engine")
    })


def transform_node_to_graph(node, seen_nodes=None, root=False):
    """
    Transform a node from the internal format to the format expected by the frontend
    Based on the display logic in display.py
    """
    if seen_nodes is None:
        seen_nodes = set()
        
    # Get the base node data
    task_info = node.get("task_info", {})
    
    # Use nid for the ID field
    node_id = node.get("nid", "")
    
    # Skip if we've seen this node before (prevents duplication)
    if node_id in seen_nodes and not root:
        return None
    
    # Add this node to the set of seen nodes
    seen_nodes.add(node_id)
    
    # Get the node status
    status = node.get("status", "UNKNOWN")
    
    # Determine if this is an execute node
    is_execute_node = node.get("node_type") == "EXECUTE_NODE"
    
    transformed = {
        "id": node_id,
        "goal": task_info.get("goal", "Unknown"),
        "task_type": task_info.get("task_type", "unknown"),
        "status": status,
        "dependency": task_info.get("dependency", []),
        "sub_tasks": [],
        "node_type": node.get("node_type", "UNKNOWN"),
        "is_execute_node": is_execute_node,
    }
    
    # Add action information if available
    if "result" in node:
        # The node.result dictionary contains actions as keys
        # Include both the latest action and all actions
        actions = []
        latest_action_name = None
        latest_action_result = None
        
        for action_name, action_data in node.get("result", {}).items():
            raw_result = action_data.get("result", {})
            action_result = raw_result.get("result", "") if isinstance(raw_result, dict) else raw_result
            action_time = action_data.get("time", "")
            
            actions.append({
                "name": action_name,
                "result": action_result,
                "time": action_time
            })
            
            # Track the latest action by time
            if not latest_action_name or action_time > node.get("result", {}).get(latest_action_name, {}).get("time", ""):
                latest_action_name = action_name
                latest_action_result = action_result
                
        if actions:
            transformed["actions"] = actions
        
        if latest_action_name:
            transformed["latest_action"] = {
                "name": latest_action_name,
                "result": latest_action_result
            }
    
    # For task graph visualization, we need to collect and flatten all subtasks
    # from the node hierarchy
    def collect_subtasks(current_node, parent_transformed):
        """Recursively collect all subtasks from a node and its children"""
        if not current_node:
            return
            
        # Get the inner graph of the current node
        inner = current_node.get("inner_graph", {})
        if not inner or "topological_task_queue" not in inner:
            return
            
        # Get and sort tasks by ID
        tasks = inner.get("topological_task_queue", [])
        sorted_tasks = sorted(
            tasks,
            key=lambda x: int(str(x.get("nid", "0")).split(".")[-1])
        )
        
        # Process each task
        for task in sorted_tasks:
            task_id = task.get("nid", "")
            
            # Skip duplicate nodes
            if task_id in seen_nodes and task_id != current_node.get("nid"):
                continue
                
            # Mark this node as seen
            seen_nodes.add(task_id)
            
            # Create the transformed task
            task_info = task.get("task_info", {})
            is_execute = task.get("node_type") == "EXECUTE_NODE"
            
            sub_task = {
                "id": task_id,
                "goal": task_info.get("goal", "Unknown"),
                "task_type": task_info.get("task_type", "unknown"),
                "status": task.get("status", "UNKNOWN"),
                "dependency": task_info.get("dependency", []),
                "sub_tasks": [],
                "node_type": task.get("node_type", "UNKNOWN"),
                "is_execute_node": is_execute
            }
            
            # Add action information if available
            if "result" in task:
                # The task.result dictionary contains actions as keys
                # Include both the latest action and all actions
                actions = []
                latest_action_name = None
                latest_action_result = None
                
                for action_name, action_data in task.get("result", {}).items():
                    raw_result = action_data.get("result", {})
                    action_result = raw_result.get("result", "") if isinstance(raw_result, dict) else raw_result
                    
                    actions.append({
                        "name": action_name,
                        "result": action_result,
                        "time": action_time
                    })
                    
                    # Track the latest action by time
                    if not latest_action_name or action_time > task.get("result", {}).get(latest_action_name, {}).get("time", ""):
                        latest_action_name = action_name
                        latest_action_result = action_result
                        
                if actions:
                    sub_task["actions"] = actions
                
                if latest_action_name:
                    sub_task["latest_action"] = {
                        "name": latest_action_name,
                        "result": latest_action_result
                    }
            
            # Add to parent's subtasks
            parent_transformed["sub_tasks"].append(sub_task)
            
            # For task graph visualization, we don't skip execute nodes
            # Instead we process their subtasks but mark them specially
            collect_subtasks(task, sub_task)
    
    # Start collecting subtasks from this node
    collect_subtasks(node, transformed)
    
    return transformed

@app.route('/api/task-graph/<task_id>', methods=['GET'])
@jwt_required()
def api_get_task_graph(task_id):
    """
    Get the task graph data (nodes and edges) for a specific task
    """
    # Check if the task directory exists
    task_dir = os.path.join(RESULTS_DIR, task_id)
    if not os.path.isdir(task_dir):
        return jsonify({"error": "Task not found"}), 404
    
    # Possible locations for the nodes.json file
    nodes_paths = [
        os.path.join(task_dir, 'records', 'nodes.json'),
        os.path.join(RESULTS_DIR, 'records', task_id, 'nodes.json')
    ]
    
    nodes_file = None
    for path in nodes_paths:
        if os.path.exists(path):
            nodes_file = path
            break
    
    if not nodes_file:
        # Create a simple task graph if we can't find the real one
        
        # Get prompt from input file
        input_file = os.path.join(task_dir, 'input.jsonl')
        prompt = "Unknown task"
        if os.path.exists(input_file):
            try:
                with open(input_file, 'r') as f:
                    input_data = json.load(f)
                    if 'value' in input_data:
                        prompt = input_data.get('value', '')
            except Exception as e:
                logger.error(f"Error reading input file: {str(e)}")
        
        simple_graph = {
            "id": "",
            "goal": prompt,
            "task_type": "write",
            "status": "FINISH",
            "sub_tasks": [
                {
                    "id": "0",
                    "goal": "Task graph data not available",
                    "task_type": "think",
                    "status": "FINISH",
                    "sub_tasks": []
                }
            ]
        }
        
        return jsonify({
            "taskId": task_id,
            "taskGraph": simple_graph
        })
    
    try:
        with open(nodes_file, 'r') as f:
            nodes_data = json.load(f)
        
        # Transform the data to the format expected by the frontend
        transformed_graph = transform_node_to_graph(nodes_data, root=True)
        
        return jsonify({
            "taskId": task_id,
            "taskGraph": transformed_graph
        })
    except Exception as e:
        logger.error(f"Error processing nodes.json: {str(e)}")
        return jsonify({"error": f"Failed to read task graph data: {str(e)}"}), 500

@app.route('/api/reload', methods=['POST'])
@jwt_required()
def api_reload_tasks():
    """Reload all tasks from the file system"""
    reload_task_storage()
    return jsonify({
        "status": "ok",
        "message": "Task storage reloaded",
        "taskCount": len(task_storage)
    })
    
@app.route('/api/stop-task/<task_id>', methods=['POST'])
@jwt_required()
def api_stop_task(task_id):
    """Stop a running task"""
    logger.info(f"Received stop-task request for {task_id}")
    try:
        # Sanitize task_id to prevent path traversal
        if not re.match(r'^[a-zA-Z0-9_\-]+$', task_id):
            return jsonify({"status": "error", "error": "Invalid task ID format"}), 400
            
        # Check if task exists
        if task_id not in task_storage:
            return jsonify({"status": "error", "error": "Task not found"}), 404
            
        # Check if task is already completed or stopped
        if task_storage[task_id]["status"] in ["completed", "error", "stopped"]:
            return jsonify({
                "status": "ok",
                "message": f"Task {task_id} is already {task_storage[task_id]['status']}"
            })
        
          
        # Direct approach: Find the pid for the python engine.py process and kill it
        task_dir = os.path.join(RESULTS_DIR, task_id)

        # 1. Create a stop.txt file for the task to detect gracefully                                                 │ │
        stop_file = os.path.join(task_dir, 'stop.txt') 
        
        # First try to find the PID using ps command
        try:
            # For the specific task_id, find the python engine.py process
            cmd = f"ps -ef | grep '{task_id}' | grep 'engine.py' | grep -v grep | awk '{{print $2}}'"
            result = subprocess.check_output(cmd, shell=True).decode().strip()
            
            if result:
                pid = int(result)
                logger.info(f"Found Python engine.py process with PID {pid} for task {task_id}")
                
                # Kill the process and its children
                logger.info(f"Killing process {pid} and its children")
                if os.name != 'nt':  # Unix/Linux/MacOS
                    # try:
                    #     # Try to kill process group first
                    #     os.killpg(os.getpgid(pid), signal.SIGKILL)
                    #     print(f"Sent SIGKILL to process group for PID {pid}")
                    # except Exception as group_err:
                    #     print(f"Error killing process group: {str(group_err)}")
                        
                    # Also try direct kill commands
                    os.system(f"kill -9 {pid}")
                    os.system(f"pkill -P {pid}")  # Kill all child processes
                    logger.info(f"Used kill commands on PID {pid}")
                else:
                    # Windows
                    os.system(f"taskkill /F /PID {pid} /T")
                    logger.info(f"Used taskkill on PID {pid}")
            else:
                logger.warning(f"Could not find Python engine.py process for task {task_id}")
                
                # Fall back to looking for the run.sh process
                cmd = f"ps -ef | grep '{task_dir}/run.sh' | grep -v grep | awk '{{print $2}}'"
                result = subprocess.check_output(cmd, shell=True).decode().strip()
                
                if result:
                    pid = int(result)
                    logger.info(f"Found run.sh process with PID {pid} for task {task_id}")
                    
                    # Kill the process
                    if os.name != 'nt':
                        os.system(f"kill -9 {pid}")
                        # os.system(f"pkill -P {pid}")
                    else:
                        os.system(f"taskkill /F /PID {pid} /T")
                else:
                    logger.warning(f"Could not find run.sh process for task {task_id}")
                    
        except Exception as e:
            logger.error(f"Error finding or killing processes for task {task_id}: {str(e)}")
            
            # As a last resort, try to kill any processes related to the task directory
            if os.name != 'nt':
                os.system(f"pkill -f '{task_dir}'")
                logger.info(f"Attempted to kill any processes related to {task_dir}")
        
        # Create a done file to indicate the task is stopped
        with open(os.path.join(task_dir, 'done.txt'), 'w') as f:
            f.write("Stopped by user at " + datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        
        # Update task status
        task_storage[task_id]["status"] = "stopped"
        
        # Set a result message for stopped tasks
        task_storage[task_id]["result"] = "Task was stopped by user request before completion."
        
        # Emit a socket message to notify the frontend
        socketio.emit('task_update', {
            'taskId': task_id,
            'status': 'stopped',
            'message': 'Task has been stopped by user request'
        })
        
        return jsonify({
            "status": "ok",
            "message": f"Task {task_id} has been stopped"
        })
    except Exception as e:
        logger.error(f"Error stopping task {task_id}: {str(e)}")
        return jsonify({
            "status": "error",
            "error": f"Failed to stop task: {str(e)}"
        }), 500

@app.route('/api/delete-task/<task_id>', methods=['DELETE'])
@jwt_required()
def api_delete_task(task_id):
    """Delete a previously generated task and its associated files"""
    logger.info(f"Received delete-task request for {task_id}")
    try:
        # Sanitize task_id to prevent path traversal
        if not re.match(r'^[a-zA-Z0-9_\-]+$', task_id):
            return jsonify({"status": "error", "error": "Invalid task ID format"}), 400
            
        # Define paths to check for task files
        task_dir = os.path.join(RESULTS_DIR, task_id)
        records_dir = os.path.join(RESULTS_DIR, 'records', task_id)
        
        deleted = False
        
        # Check and delete from main results directory
        if os.path.isdir(task_dir):
            shutil.rmtree(task_dir)
            deleted = True
            
        # Check and delete from records subdirectory
        if os.path.isdir(records_dir):
            shutil.rmtree(records_dir)
            deleted = True
            
        # If nothing was found to delete
        if not deleted:
            return jsonify({"status": "error", "error": "Task not found"}), 404
            
        # Remove from task storage if it exists
        if task_id in task_storage:
            del task_storage[task_id]
            
        return jsonify({
            "status": "ok",
            "message": f"Task {task_id} deleted successfully"
        })
    except Exception as e:
        logger.error(f"Error deleting task {task_id}: {str(e)}")
        return jsonify({
            "status": "error",
            "error": f"Failed to delete task: {str(e)}"
        }), 500

@app.route('/api/history', methods=['GET'])
@jwt_required()
def api_get_history():
    """Get a list of previously generated tasks with their basic info"""
    # Make sure task_storage is up to date
    reload_task_storage()
    
    history_tasks = []
    
    # List all directories in the results folder
    for task_id in os.listdir(RESULTS_DIR):
        task_dir = os.path.join(RESULTS_DIR, task_id)
        if not os.path.isdir(task_dir):
            continue
            
        # Check if this is a completed task with results
        result_file = os.path.join(task_dir, 'result.jsonl')
        if not os.path.exists(result_file):
            continue
            
        # Get the input file to extract the prompt
        input_file = os.path.join(task_dir, 'input.jsonl')
        prompt = ""
        task_type = "unknown"
        
        if os.path.exists(input_file):
            try:
                with open(input_file, 'r') as f:
                    input_data = json.load(f)
                    if 'value' in input_data:
                        # Story generation input
                        prompt = input_data.get('value', '')
                        task_type = "story"
                    elif 'prompt' in input_data:
                        # Report generation input
                        prompt = input_data.get('prompt', '')
                        task_type = "report"
            except:
                # If we can't read the input file, continue anyway
                pass
        
        # Get the creation time of the result file as timestamp
        creation_time = os.path.getctime(result_file)
        creation_date = datetime.fromtimestamp(creation_time).strftime('%Y-%m-%d %H:%M:%S')
        
        # Add task info to history list
        history_tasks.append({
            "taskId": task_id,
            "prompt": prompt[:100] + "..." if len(prompt) > 100 else prompt,
            "type": task_type,
            "createdAt": creation_date
        })
    
    # Sort by creation time, newest first
    history_tasks.sort(key=lambda x: x["createdAt"], reverse=True)
    
    return jsonify({
        "history": history_tasks
    })

@app.route('/api/workspace/<task_id>', methods=['GET'])
@jwt_required()
def api_get_workspace(task_id):
    """Get the article.txt content for a task"""
    task_dir = os.path.join(RESULTS_DIR, 'records', task_id)
    article_file = os.path.join(task_dir, 'article.txt')
    
    if not os.path.exists(article_file):
        return jsonify({"error": "Workspace file not found"}), 404
    
    try:
        with open(article_file, 'r', encoding='utf-8') as f:
            content = f.read()
        return jsonify({
            "taskId": task_id,
            "workspace": content
        })
    except Exception as e:
        logger.error(f"Error reading workspace file: {str(e)}")
        return jsonify({"error": f"Failed to read workspace file: {str(e)}"}), 500

@app.route('/api/ping', methods=['GET'])
def api_ping():
    """Simple endpoint to test if the API is reachable"""
    return jsonify({
        "status": "ok",
        "message": "API server is running",
        "version": "1.0.0"
    })

def monitor_task_progress(task_id, nodes_dir):
    """
    Monitor task progress and send updates via WebSocket
    """
    try:
        logger.info(f"Starting task progress monitoring for task: {task_id}")
        logger.debug(f"Monitoring directory: {nodes_dir}")
        
        # Create a basic task structure to start with
        task_graph = {
            "id": "0",
            "goal": "Initializing task...",
            "task_type": "think",
            "status": "DOING",
            "sub_tasks": []
        }
        
        logger.debug(f"Sending initial task_update for {task_id}")
        socketio.emit('task_update', {'taskId': task_id, 'taskGraph': task_graph})
        
        # Monitor the nodes.json file for changes
        last_modified = 0
        nodes_file = os.path.join(nodes_dir, 'nodes.json')
        task_dir = os.path.dirname(nodes_dir)
        logger.debug(f"Watching for changes to: {nodes_file}")
        
        while task_storage.get(task_id, {}).get('status') not in ['completed', 'error', 'stopped']:                
            if os.path.exists(nodes_file):
                current_modified = os.path.getmtime(nodes_file)
                
                if current_modified > last_modified:
                    last_modified = current_modified
                    # logger.debug(f"Detected changes to nodes.json, reading file")
                    
                    try:
                        with open(nodes_file, 'r') as f:
                            nodes_data = json.load(f)
                        
                        # Transform the data for frontend
                        transformed_graph = transform_node_to_graph(nodes_data, root=True)
                        
                        # Send update via WebSocket
                        # logger.debug(f"Sending task_update with {len(transformed_graph.get('sub_tasks', []))} sub-tasks")
                        
                        # Debug output - check if we have action information
                        if 'latest_action' in transformed_graph:
                            logger.debug(f"Root node has latest action: {transformed_graph['latest_action']['name']}")
                        
                        # Debug the first subtask if available
                        if transformed_graph.get('sub_tasks') and len(transformed_graph.get('sub_tasks', [])) > 0:
                            first_task = transformed_graph['sub_tasks'][0]
                            if 'latest_action' in first_task:
                                logger.debug(f"First subtask has latest action: {first_task['latest_action']['name']}")
                        
                        socketio.emit('task_update', {
                            'taskId': task_id, 
                            'taskGraph': transformed_graph
                        })
                    except Exception as e:
                        logger.error(f"Error reading nodes.json: {str(e)}")
            else:
                # logger.debug(f"Waiting for nodes.json file to be created at: {nodes_file}")
                pass
            
            # Sleep for a short time to avoid high CPU usage
            time.sleep(1)
            
        logger.info(f"Task {task_id} status changed to {task_storage.get(task_id, {}).get('status')}")
        # Send one final update once the task is complete
        if os.path.exists(nodes_file):
            try:
                logger.debug(f"Reading final state from nodes.json")
                with open(nodes_file, 'r') as f:
                    nodes_data = json.load(f)
                    
                transformed_graph = transform_node_to_graph(nodes_data, root=True)
                logger.info(f"Sending final task_update with status {task_storage.get(task_id, {}).get('status')}")
                socketio.emit('task_update', {
                    'taskId': task_id, 
                    'taskGraph': transformed_graph,
                    'status': task_storage.get(task_id, {}).get('status', 'unknown')
                })
            except Exception as e:
                logger.error(f"Error reading final nodes.json: {str(e)}")
    except Exception as e:
        logger.error(f"Error monitoring task progress for {task_id}: {str(e)}")

if __name__ == '__main__':
    logger.info(f"Starting backend server on port {args.port}...")
    socketio.run(app, host='0.0.0.0', port=args.port, debug=False, allow_unsafe_werkzeug=True)
