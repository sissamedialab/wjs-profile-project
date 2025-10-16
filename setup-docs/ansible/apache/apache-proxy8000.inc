# Proxy to runserver
ProxyPass        / http://127.0.0.1:8000/
ProxyPassReverse / http://127.0.0.1:8000/
ProxyPreserveHost On
