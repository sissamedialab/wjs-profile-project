# Log and SSL configuration

# RemoteIP permette di avere gli IP corretti anche in ErrorLog
# Sono attendibili solo se arrivano da questi host:
RemoteIPHeader          X-Forwarded-For
RemoteIPInternalProxy   192.168.10.10 192.168.10.11
#                       stheno.ud     caxton.ud
LogLevel warn
LogFormat "%h %l %u %t \"%r\" %>s %O \"%{Referer}i\" \"%{User-Agent}i\" \"Host:%{Host}i\" T:%{ms}Tms"  combined-proto-host-x
CustomLog ${APACHE_LOG_DIR}/${LOG_NAME}-access.log combined-proto-host-x
ErrorLog  ${APACHE_LOG_DIR}/${LOG_NAME}-error.log
ServerSignature Off

SSLEngine		        on
SSLCertificateFile	    /etc/ssl/certs/ssl-cert-snakeoil.pem
SSLCertificateKeyFile   /etc/ssl/private/ssl-cert-snakeoil.key
SSLProtocol		        all -SSLv2 -SSLv3

ProxyRequests           off
