from flask import Flask, render_template, request, redirect, url_for, flash, send_file, session, jsonify, abort
import os
import tempfile
import secrets
import logging
import time
import uuid
import shutil
import mimetypes
from io import BytesIO
from werkzeug.utils import secure_filename
from functools import wraps
from datetime import datetime, timedelta
from PIL import Image
import ffmpeg

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', secrets.token_hex(16))
app.config['TEMPLATES_AUTO_RELOAD'] = False
app.jinja_env.auto_reload = False
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 86400

# app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # Set max upload size to i.e. 100MB if wanted

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger('sftp-web-ui')

WEB_USERNAME = os.environ.get('WEB_USERNAME', 'username')
WEB_PASSWORD = os.environ.get('WEB_PASSWORD', 'password')

DATA_DIR = '/data'


def ensure_data_dir():
    """Make sure the data directory exists and has proper permissions"""
    if not os.path.exists(DATA_DIR):
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            logger.info(f"Created data directory: {DATA_DIR}")
        except Exception as e:
            logger.error(f"Error creating data directory: {e}")


ensure_data_dir()


def login_required(f):
    """Decorator to require login for a route"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'logged_in' not in session:
            flash('Please log in first', 'danger')
            return redirect(url_for('login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function


def format_file_size(size):
    """Convert file size in bytes to human-readable format"""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size < 1024.0:
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} PB"


def secure_path(path):
    """Ensure path is secure and normalized"""
    # Convert to absolute path
    if not path.startswith('/'):
        path = '/' + path

    # Normalize path
    normalized = os.path.normpath(path)

    # Ensure the path is within the DATA_DIR
    full_path = os.path.join(DATA_DIR, normalized.lstrip('/'))

    # Prevent path traversal attacks
    if not os.path.normpath(full_path).startswith(os.path.normpath(DATA_DIR)):
        logger.warning(f"Path traversal attempt detected: {path}")
        abort(403)

    return normalized


def get_full_path(path):
    """Get full filesystem path from web path"""
    path = path.lstrip('/')
    return os.path.join(DATA_DIR, path)


def is_image(filename):
    """Check if a file is an image based on its mimetype"""
    mime_type, _ = mimetypes.guess_type(filename)
    return mime_type and mime_type.startswith('image/')


def is_video(filename):
    """Check if a file is a video based on its mimetype"""
    mime_type, _ = mimetypes.guess_type(filename)
    return mime_type and mime_type.startswith('video/')


def is_pdf(filename):
    """Check if a file is a PDF based on its mimetype"""
    mime_type, _ = mimetypes.guess_type(filename)
    return mime_type == 'application/pdf'


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        if username == WEB_USERNAME and password == WEB_PASSWORD:
            session['logged_in'] = True
            session.permanent = True
            app.permanent_session_lifetime = timedelta(days=1)
            flash('Login successful', 'success')
            next_page = request.args.get('next')
            return redirect(next_page or url_for('index'))
        else:
            flash('Invalid credentials', 'danger')
    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    flash('You have been logged out', 'success')
    return redirect(url_for('login'))


@app.route('/')
@login_required
def index():
    return browse_files('/')


@app.route('/browse')
@login_required
def browse():
    path = request.args.get('path', '/')
    path = secure_path(path)
    return browse_files(path)


def browse_files(path):
    """List files in a directory using the local filesystem."""
    logger.info(f"Browsing files in directory: {path}")
    files = []

    try:
        full_path = get_full_path(path)

        # Check if path exists and is a directory
        if not os.path.exists(full_path):
            # Try to create it if it's the root folder
            if path == '/':
                try:
                    os.makedirs(full_path, exist_ok=True)
                    logger.info(f"Created root directory: {full_path}")
                except Exception as e:
                    logger.error(f"Error creating root directory: {e}")
                    flash(f"Error creating root directory: {e}", 'danger')
                    return render_template('browser.html', files=[], current_path='/', now=int(time.time()))
            else:
                flash(f"Directory does not exist: {path}", 'danger')
                return render_template('browser.html', files=[], current_path='/', now=int(time.time()))

        if not os.path.isdir(full_path):
            flash(f"Not a directory: {path}", 'danger')
            return render_template('browser.html', files=[], current_path='/', now=int(time.time()))

        # List directory
        for filename in os.listdir(full_path):
            file_path = os.path.join(full_path, filename)

            try:
                stats = os.stat(file_path)
                is_dir = os.path.isdir(file_path)

                file_info = {
                    'name': filename,
                    'is_directory': is_dir,
                    'size': format_file_size(stats.st_size) if not is_dir else '-',
                    'modified': datetime.fromtimestamp(stats.st_mtime).strftime('%Y-%m-%d %H:%M:%S'),
                    'path': os.path.join(path, filename).replace('\\', '/'),
                    'is_image': not is_dir and is_image(file_path),
                    'is_video': not is_dir and is_video(file_path),
                    'is_pdf': not is_dir and is_pdf(file_path)
                }
                files.append(file_info)
            except Exception as e:
                logger.warning(f"Error processing file {filename}: {str(e)}")

        # Sort files (directories first, then alphabetically)
        files.sort(key=lambda x: (not x['is_directory'], x['name'].lower()))

        # Add parent directory entry if not at root
        if path != '/':
            parent_path = os.path.dirname(path)
            if not parent_path:
                parent_path = '/'
            files.insert(0, {
                'name': '..',
                'is_directory': True,
                'size': '-',
                'modified': '-',
                'path': parent_path,
                'is_image': False,
                'is_video': False,
                'is_pdf': False
            })

    except Exception as e:
        logger.error(f"Error listing directory {path}: {str(e)}")
        flash('Error accessing directory. Please try again later.', 'danger')

    return render_template('browser.html', files=files, current_path=path, now=int(time.time()))


@app.route('/upload', methods=['POST'])
@login_required
def upload_file():
    current_path = secure_path(request.form.get('current_path', '/'))

    if 'file' not in request.files:
        flash('No files selected', 'danger')
        return redirect(url_for('browse', path=current_path))

    uploaded_files = request.files.getlist('file')
    if not uploaded_files or all(f.filename == '' for f in uploaded_files):
        flash('No files selected', 'danger')
        return redirect(url_for('browse', path=current_path))

    try:
        uploaded_count = 0
        full_path = get_full_path(current_path)

        # Ensure target directory exists
        if not os.path.exists(full_path):
            os.makedirs(full_path, exist_ok=True)
            logger.info(f"Created directory for upload: {full_path}")

        if not os.path.isdir(full_path):
            flash(
                f"Cannot upload to {current_path}: Not a directory", 'danger')
            return redirect(url_for('browse', path=os.path.dirname(current_path) or '/'))

        for uploaded_file in uploaded_files:
            if uploaded_file.filename == '':
                continue

            original_filename = uploaded_file.filename
            filename = secure_filename(original_filename)
            if not filename:
                logger.warning(f"Invalid filename: {original_filename}")
                continue

            file_path = os.path.join(full_path, filename)

            # Save the file directly to its destination
            uploaded_file.save(file_path)
            uploaded_count += 1
            logger.info(
                f"Successfully uploaded file: {filename} to {current_path}")

        if uploaded_count > 0:
            flash(f'{uploaded_count} file(s) uploaded successfully', 'success')
        else:
            flash('No files were uploaded', 'warning')
    except Exception as e:
        logger.error(f"Error uploading files: {str(e)}")
        flash(f'Error uploading files: {str(e)}', 'danger')

    return redirect(url_for('browse', path=current_path))


@app.route('/download')
@login_required
def download_file():
    file_path = secure_path(request.args.get('path', ''))
    if not file_path:
        flash('No file specified', 'danger')
        return redirect(url_for('index'))

    full_path = get_full_path(file_path)

    try:
        # Check if the file exists and is a file (not a directory)
        if not os.path.exists(full_path):
            flash('File not found', 'danger')
            return redirect(url_for('browse', path=os.path.dirname(file_path) or '/'))

        if os.path.isdir(full_path):
            flash('Cannot download directories', 'danger')
            return redirect(url_for('browse', path=file_path))

        filename = os.path.basename(file_path)

        # Download the file directly without creating a temporary copy
        # This allows for large files to be streamed
        return send_file(
            full_path,
            as_attachment=True,
            download_name=filename
        )
    except Exception as e:
        logger.error(f"Error downloading file {file_path}: {str(e)}")
        flash(f'Error downloading file: {str(e)}', 'danger')
        return redirect(url_for('browse', path=os.path.dirname(file_path) or '/'))


@app.route('/delete')
@login_required
def delete_file():
    file_path = secure_path(request.args.get('path', ''))
    if not file_path:
        flash('No file specified', 'danger')
        return redirect(url_for('index'))

    parent_dir = os.path.dirname(file_path) or '/'
    full_path = get_full_path(file_path)

    try:
        # Check if the path exists
        if not os.path.exists(full_path):
            flash('File or directory not found', 'danger')
            return redirect(url_for('browse', path=parent_dir))

        # Check if it's a directory or file
        if os.path.isdir(full_path):
            # Check if the directory is empty
            contents = os.listdir(full_path)
            if contents:
                flash('Cannot delete non-empty directory', 'warning')
            else:
                os.rmdir(full_path)
                logger.info(f"Deleted directory: {file_path}")
                flash('Directory deleted successfully', 'success')
        else:
            os.remove(full_path)
            logger.info(f"Deleted file: {file_path}")
            flash('File deleted successfully', 'success')
    except PermissionError:
        logger.error(f"Permission denied deleting {file_path}")
        flash('Permission denied', 'danger')
    except Exception as e:
        logger.error(f"Error deleting {file_path}: {str(e)}")
        flash(f'Error deleting file or directory: {str(e)}', 'danger')

    return redirect(url_for('browse', path=parent_dir))


@app.route('/mkdir', methods=['POST'])
@login_required
def create_directory():
    current_path = secure_path(request.form.get('current_path', '/'))
    dirname = request.form.get('dirname', '').strip()

    if not dirname:
        flash('No directory name provided', 'danger')
        return redirect(url_for('browse', path=current_path))

    # Validate directory name
    if '/' in dirname or '\\' in dirname or '..' in dirname:
        flash('Invalid directory name', 'danger')
        return redirect(url_for('browse', path=current_path))

    try:
        full_path = get_full_path(current_path)

        # Ensure parent directory exists
        if not os.path.exists(full_path):
            os.makedirs(full_path, exist_ok=True)
            logger.info(f"Created parent directory: {full_path}")

        if not os.path.isdir(full_path):
            flash(
                f"Cannot create directory in {current_path}: Not a directory", 'danger')
            return redirect(url_for('browse', path=os.path.dirname(current_path) or '/'))

        new_dir_path = os.path.join(full_path, dirname)

        if os.path.exists(new_dir_path):
            flash(f'Directory "{dirname}" already exists', 'warning')
        else:
            os.mkdir(new_dir_path)
            logger.info(
                f"Created directory: {os.path.join(current_path, dirname)}")
            flash(f'Directory "{dirname}" created successfully', 'success')
    except PermissionError:
        logger.error(f"Permission denied creating directory {dirname}")
        flash('Permission denied', 'danger')
    except Exception as e:
        logger.error(f"Error creating directory {dirname}: {str(e)}")
        flash(f'Error creating directory: {str(e)}', 'danger')

    return redirect(url_for('browse', path=current_path))


# New Thumbnail Routes

@app.route('/thumbnail')
@login_required
def thumbnail():
    """Generate a thumbnail for an image file"""
    file_path = secure_path(request.args.get('path', ''))
    width = int(request.args.get('width', 100))
    height = int(request.args.get('height', 100))
    
    if not file_path:
        abort(400, "No file specified")
    
    full_path = get_full_path(file_path)
    
    if not os.path.exists(full_path):
        abort(404, "File not found")
    
    if not is_image(full_path):
        abort(400, "Not an image file")
    
    try:
        img = Image.open(full_path)
        img.thumbnail((width, height))
        
        # Create an in-memory file
        img_io = BytesIO()
        img_format = img.format or 'JPEG'
        img.save(img_io, format=img_format)
        img_io.seek(0)
        
        return send_file(
            img_io, 
            mimetype=f'image/{img_format.lower()}',
            download_name=f"thumb_{os.path.basename(file_path)}"
        )
    except Exception as e:
        logger.error(f"Error generating thumbnail: {e}")
        abort(500, "Failed to generate thumbnail")


@app.route('/video-thumbnail')
@login_required
def video_thumbnail():
    """Generate a thumbnail for a video file"""
    file_path = secure_path(request.args.get('path', ''))
    time_pos = float(request.args.get('time', 1.0))
    width = int(request.args.get('width', 100))
    height = int(request.args.get('height', 100))
    
    if not file_path:
        abort(400, "No file specified")
    
    full_path = get_full_path(file_path)
    
    if not os.path.exists(full_path):
        abort(404, "File not found")
    
    if not is_video(full_path):
        abort(400, "Not a video file")
    
    try:
        # Create a temporary file for the thumbnail
        with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as temp_file:
            temp_path = temp_file.name
        
        # Use ffmpeg to extract a frame
        (
            ffmpeg
            .input(full_path, ss=time_pos)
            .filter('scale', width, height)
            .output(temp_path, vframes=1)
            .overwrite_output()
            .run(capture_stdout=True, capture_stderr=True)
        )
        
        # Read the thumbnail and return it
        with open(temp_path, 'rb') as f:
            img_data = BytesIO(f.read())
        
        # Clean up
        try:
            os.unlink(temp_path)
        except:
            pass
            
        img_data.seek(0)
        
        return send_file(
            img_data, 
            mimetype='image/jpeg',
            download_name=f"thumb_{os.path.basename(file_path)}.jpg"
        )
    except Exception as e:
        logger.error(f"Error generating video thumbnail: {e}")
        abort(500, "Failed to generate video thumbnail")


@app.route('/preview')
@login_required
def preview_file():
    """Show a preview of a file"""
    file_path = secure_path(request.args.get('path', ''))
    if not file_path:
        abort(400, "No file specified")
    
    full_path = get_full_path(file_path)
    
    if not os.path.exists(full_path):
        abort(404, "File not found")
    
    if os.path.isdir(full_path):
        abort(400, "Cannot preview directories")
    
    mime_type, _ = mimetypes.guess_type(full_path)
    
    # For text-based files, return the content directly
    if mime_type and (mime_type.startswith('text/') or mime_type in ['application/json', 'application/xml']):
        try:
            with open(full_path, 'r', encoding='utf-8', errors='replace') as f:
                content = f.read()
            return content
        except Exception as e:
            logger.error(f"Error reading text file {file_path}: {e}")
            abort(500, "Failed to read file")
    
    # For images, videos, and PDFs, redirect to a direct URL
    if is_image(full_path) or is_video(full_path) or is_pdf(full_path):
        return url_for('download_file', path=file_path)
    
    # For other files, suggest downloading
    abort(400, "File type not supported for preview")


@app.route('/health')
def health_check():
    # Check if data directory is accessible
    data_accessible = os.path.exists(
        DATA_DIR) and os.access(DATA_DIR, os.R_OK | os.W_OK)

    return jsonify({
        'status': 'ok' if data_accessible else 'error',
        'data_dir_accessible': data_accessible,
        'timestamp': datetime.now().isoformat()
    })


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)