# Gunicorn settings for Sesh. Start: gunicorn -c deploy/gunicorn.conf.py wsgi:app
bind = "127.0.0.1:8000"          # Nginx is the only thing that talks to this
workers = 3                       # roughly 2 x CPU cores + 1; preview rendering is CPU-bound
threads = 2
timeout = 120                     # large PDF streams and previews
graceful_timeout = 30
max_requests = 1000               # recycle workers to cap slow memory growth
max_requests_jitter = 100
accesslog = "-"
errorlog = "-"
forwarded_allow_ips = "127.0.0.1"
