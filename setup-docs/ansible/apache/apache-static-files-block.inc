# Local files (static files, robots.txt, ...)

# Please link these two from DocumentRoot to Janeway's src/collected-static/ and src/media/
# Serve static files directly
<Location "/static/">
    ProxyPass "!"
</Location>
# /media/ also contains static files
<Location "/media/">
    ProxyPass "!"
</Location>
# Please add robots.txt to DocumentRoot
<Location "/robots.txt">
    ProxyPass "!"
</Location>
