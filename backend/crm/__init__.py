import logging
import os
import sys

# Create logs directory if it doesn't exist
log_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'logs')
os.makedirs(log_dir, exist_ok=True)

# Configure a UTF-8 stream handlper to avoid UnicodeEncodeError on Windows (cp1252)
stream_handler = logging.StreamHandler(
    stream=open(sys.stderr.fileno(), mode='w', encoding='utf-8', closefd=False)
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(log_dir, 'django.log'), encoding='utf-8'),
        stream_handler
    ]
)