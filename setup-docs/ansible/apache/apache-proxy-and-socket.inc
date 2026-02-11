# Intercept naive penetration attempts
# ====================================
# See specs#385
ProxyPassMatch    ".*\.php$" !
RedirectMatch 403 ".*\.php$"


# Proxy to unix socket
# ====================

# The ProxyReceiveBufferSize directive specifies an explicit
# (TCP/IP) network buffer size for proxied HTTP and FTP
# connections, for increased throughput.
# https://httpd.apache.org/docs/2.4/mod/mod_proxy.html#proxyreceivebuffersize
ProxyReceiveBufferSize 2048

# Janeway accetta e processa (sbagliando) qualsiasi header usualmente generato
# da un reverse-proxy, senza alcuna verifica di liceità (anche senza che sia
# stato configurato alcun rev-proxy in Janeway).
# Apache presenta già i dati corretti e validati a Janeway, quindi dis-impostiamo
# queste tre variabili per evitare che Janeway valuti degli header che sono in
# realtà nel controllo di chi fa la richiesta web (e che fanno crashare Janeway
# se non "ben formati")
# See
# - https://trackers.sissamedialab.it/it-help/issue2162
# - https://gitlab.sissamedialab.it/wjs/wjs-profile-project/-/issues/88#note_33025
RequestHeader unset X-Forwarded-Host
RequestHeader unset X-Forwarded-For
RequestHeader unset X-Forwarded-Server

# VirtualHosts that include this configuration should listen only on 443,
# so the connection from "outside" is secure.
# Apache will then communicate with the application server with non-TLS protocols,
# and we need to let the application server know that the connections are secure.
RequestHeader unset X-Forwarded-Proto
RequestHeader set X-Forwarded-Proto "https" env=HTTPS
# RequestHeader set X-Forwarded-Port 443

# Please define SOCKET_NAME from the including file as follows
# (allows for multiple instances)
# Define SOCKET_NAME /run/wjs/janeway.sock

ProxyPreserveHost On

# Proxy other HTTP requests,
# let the application server upgrade the procol when needed
ProxyPass / unix:${SOCKET_NAME}|http://%{HTTP_HOST}/ upgrade=websocket
ProxyPassReverse / unix:${SOCKET_NAME}|http://%{HTTP_HOST}/
