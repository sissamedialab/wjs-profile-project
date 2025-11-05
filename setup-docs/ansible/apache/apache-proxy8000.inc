RequestHeader unset X-Forwarded-Proto
RequestHeader set X-Forwarded-Proto "https" env=HTTPS


# Proxy to runserver
ProxyPass        / http://127.0.0.1:8000/ upgrade=websocket
ProxyPassReverse / http://127.0.0.1:8000/
ProxyPreserveHost On
