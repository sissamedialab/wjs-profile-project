# Intercept naive penetration attempts
# ====================================
# See specs#385
ProxyPassMatch    ".*\.php$" !
RedirectMatch 403 ".*\.php$"


# Proxy mailcatcher
# =================
ProxyPass        /mailcatcher  http://127.0.0.1:1080/mailcatcher


# Proxy to uwsgi
# ==============

# The ProxyReceiveBufferSize directive specifies an explicit
# (TCP/IP) network buffer size for proxied HTTP and FTP
# connections, for increased throughput.
# https://httpd.apache.org/docs/2.4/mod/mod_proxy.html#proxyreceivebuffersize
ProxyReceiveBufferSize 2048

# Janeway accetta e processa (sbagliando) qualsiasi header usualmente generato
# da un reverse-proxy, senza alcuna verifica di liceità (anche senza che sia
# stato configurato alcun rev-proxy in Janeway).
# Apache presenza già i dati corretti e validati a Janeway, quindi dis-impostiamo
# queste tre variabili per evitare che Janeway valuti degli header che sono in
# realtà nel controllo di chi fa la richiesta web (e che fanno crashare Janeway
# se non "ben formati")
# See
# - https://trackers.sissamedialab.it/it-help/issue2162
# - https://gitlab.sissamedialab.it/wjs/wjs-profile-project/-/issues/88#note_33025
RequestHeader unset X-Forwarded-Host
RequestHeader unset X-Forwarded-For
RequestHeader unset X-Forwarded-Server

# Please define SOCKET_NAME from the including file as follows
# (allows for multiple instances)
# Define SOCKET_NAME /var/run/uwsgi/janeway.sock

# The following are kept only for ducmentation
# Define SOCKET_NAME /home/wjs/uwsgi/janeway.sock  →  Not the "right" place; see https://uwsgi-docs.readthedocs.io/en/latest/Systemd.html?highlight=socket%20systemd#putting-sockets-in-run
# Define SOCKET_NAME /tmp/janeway.sock  →  No such file or directory (probably because of systemd's PrivateTmp=yes

# ProxyPass        /           unix:${SOCKET_NAME}|https://%{HTTP_HOST}/  →  https: failed to enable ssl support for 0.0.0.0:0 (httpd-UDS)
# ProxyPass        /           unix:${SOCKET_NAME}|uwsgi://  →  Bad Request

ProxyPass        / unix:${SOCKET_NAME}|uwsgi://%{HTTP_HOST}/
ProxyPassReverse / unix:${SOCKET_NAME}|uwsgi://%{HTTP_HOST}/
# Equivalenti a questo sopra:
# ProxyPass        / unix:${SOCKET_NAME}|uwsgi://janeway-test.sissamedialab.it/
# ProxyPass        / unix:${SOCKET_NAME}|uwsgi://janeway-test.sissamedialab.it:443/
