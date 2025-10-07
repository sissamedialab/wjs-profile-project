# Give user wjs read & execute permissions on all files/dirs under /var/log/apache2 (recursively)
setfacl -R -m u:wjs:rx /var/log/apache2

# Set default ACL so that new files/dirs in /var/log/apache2 inherit read & execute permissions for user wjs
setfacl -d -m u:wjs:rx /var/log/apache2
