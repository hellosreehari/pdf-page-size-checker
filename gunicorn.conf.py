import os

# Azure Web App sets the PORT environment variable (default 8000 or 80)
port = os.environ.get("PORT", "8000")
bind = f"0.0.0.0:{port}"

# Worker processes and timeout for PDF processing
workers = 2
timeout = 180
accesslog = "-"
errorlog = "-"
