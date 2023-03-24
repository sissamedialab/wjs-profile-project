# Proxy to uwsgi

# The ProxyReceiveBufferSize directive specifies an explicit
# (TCP/IP) network buffer size for proxied HTTP and FTP
# connections, for increased throughput.
# https://httpd.apache.org/docs/2.4/mod/mod_proxy.html#proxyreceivebuffersize
ProxyReceiveBufferSize 2048

Define SOCKET_NAME /var/run/uwsgi/janeway.sock
# Define SOCKET_NAME /home/wjs/uwsgi/janeway.sock  →  Not the "right" place; see https://uwsgi-docs.readthedocs.io/en/latest/Systemd.html?highlight=socket%20systemd#putting-sockets-in-run
# Define SOCKET_NAME /tmp/janeway.sock  →  No such file or directory (probably because of systemd's PrivateTmp=yes
# ProxyPass        /           unix:${SOCKET_NAME}|https://%{HTTP_HOST}/  →  https: failed to enable ssl support for 0.0.0.0:0 (httpd-UDS)
# ProxyPass        /           unix:${SOCKET_NAME}|uwsgi://  →  Bad Request
ProxyPass        / unix:${SOCKET_NAME}|uwsgi://%{HTTP_HOST}/
ProxyPassReverse / unix:${SOCKET_NAME}|uwsgi://%{HTTP_HOST}/
# Equivalenti a questo sopra:
# ProxyPass        / unix:${SOCKET_NAME}|uwsgi://janeway-test.sissamedialab.it/
# ProxyPass        / unix:${SOCKET_NAME}|uwsgi://janeway-test.sissamedialab.it:443/
