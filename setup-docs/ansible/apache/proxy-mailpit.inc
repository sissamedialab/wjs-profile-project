# Proxy mailcatcher
# =================

# Mailcatcher obsoleted in favor of mailpit
# ProxyPass        /mailcatcher  http://127.0.0.1:1080/mailcatcher

<Location /mailpit >
    ProxyPass  http://127.0.0.1:8025/mailpit
    ProxyPassReverse  http://127.0.0.1:8025/mailpit

    # Cannot be used when behind another apache reverse proxy:
    # RewriteEngine on
    # RewriteCond %{HTTP:Upgrade} websocket [NC]
    # RewriteCond %{HTTP:Connection} upgrade [NC]
    # RewriteRule ^/?(.*) "ws://127.0.0.1:8025/mailpit/$1" [P,L] # internal Mailpit address
</Location>
