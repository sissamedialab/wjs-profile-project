# JCOM redirects
# - manage old "incarnations"
# - virtual path PDF of published articles for Google Scholar
# - allow  matomo to keep track of downloads via diret link (TBV)

RewriteEngine On

# e.g. https://janeway-test.sissamedialab.it/JCOM/archive...
# oppure https://jcom-test.sissamedialab.it/archive...
RewriteRule "^/(?P<journal_code>JCOM/)?archive/(?P<volume>\d{2})/(?P<issue>\d{2})/(?P<pubid>[\w.()-]+)/(?P<rest>.*)$" "/(?P=journal_code)articles/pubid/(?P=pubid)/(?P=rest)"

# TODO: open issue di pulizia:
#       isolare i vecchi redirect che si possono elimnare (e.g. rss / struttura del sito)
#       da quelli che invece si vuole tenere (e.g. articoli/PDF)

### Tutti i rewrite delle url dal vecchio sito Plone al nuovo Drupal:
#
# Plone:    //jcom.sissa.it/jcom0303.html
# Drupal:   //jcom.sissa.it/archive/03/03/
#           OCCHIO: /quasi/ matcha l'url del WJ; funziona solamente
#                   perché nel WJ non c'è nessun file statico ".html"
RewriteRule ^/jcom(..)(..).*.html       /archive/$1/$2/ [R,L]

# Plone:    //jcom.sissa.it/comment/com020102_or.html
# Drupal:   //jcom.sissa.it/archive/02/01/C020102-en
RewriteRule ^/editorial/edit(..)(..).*  /archive/$1/$2/E$1$2-en [R,L]
RewriteRule ^/focus/foc(..)(..)(..).*   /archive/$1/$2/F$1$2$3-en [R,L]
RewriteRule ^/article/art(..)(..)(..).* /archive/$1/$2/A$1$2$3-en [R,L]
RewriteRule ^/comment/com(..)(..)(..).* /archive/$1/$2/C$1$2$3-en [R,L]
RewriteRule ^/review/rev(..)(..)(..).*  /archive/$1/$2/R$1$2$3-en [R,L]
RewriteRule ^/letter/let(..)(..)(..).*  /archive/$1/$2/L$1$2$3-en [R,L]


# from plone to drupal - manage old file urls (mg 2015-03-25)
# plone was: http://jcom.sissa.it/archive/13/04/JCOM_1304_2014_E/JCOM_1304_2014_E.pdf
# drupal is: http://jcom.sissa.it/sites/default/files/documents/JCOM_1304_2014_E.pdf
#
# {as} 2017-06-13: spostato nel backend, dove faccio /quasi/ la stessa cosa,
#                  mettendo in un meta l'url in /archive...
#                  Caveat:
#                   in plone erano:  /archive/13/04/JCOM_1304_2014_E/JCOM_1304_2014_E.pdf
#                   ma Google vuole: /archive/13/04/JCOM_1304_2014_E.pdf
#
#RewriteRule ^/archive/.+/([^/]+.pdf)$   /sites/default/files/documents/$1 [R,L]


# from plone to drupal - manage old RSS urls (mg 2015-04-01)
RewriteRule ^/all-articles/RSS$         /rss.xml [R=301,L]
RewriteRule ^/all-comments/RSS$         /rss.xml [R=301,L]
RewriteRule ^/all-editorials/RSS$       /rss.xml [R=301,L]
RewriteRule ^/all-essays/RSS$           /rss.xml [R=301,L]
RewriteRule ^/all-focus/RSS$            /rss.xml [R=301,L]
RewriteRule ^/all-letters/RSS$          /rss.xml [R=301,L]
RewriteRule ^/all-reviews/RSS$          /rss.xml [R=301,L]
RewriteRule ^/all/RSS$                  /rss.xml [R=301,L]

# In plone, we had rss for any search. The following is just a catch all.
RewriteRule ^/search_rss.*              /rss.xml? [R=301,L]

# A specific view of a plone object:
RewriteCond %{REQUEST_URI} atct_topic_view
RewriteRule ^(.*)atct_topic_view.*      $1? [R]

# no more "all" page; redirecting to archive
# NB: this is delicate; we may want to remove it
# so that a page named "all" can be created in the site
RewriteRule ^/all/.*                    /archive? [R=301,L]

# letters and focus don't have a dedicated "all-X" page any more
# NB: these are delicate; we may want to remove them
# and create the pages in the site
RewriteRule ^/all-letters.*             /documents/letter? [R=301,L]
RewriteRule ^/all-focus.*               /documents/focus? [R=301,L]

RewriteRule ^/mc-logo.png               /sites/default/files/styles/banner_260/public/banner/mc-logo.png [R=301,L]

# plone was: contentsof?Authors=Pietro%20Greco
# drupal is: /author/pietro-greco
RewriteCond %{QUERY_STRING}         "Authors=(.+)%20(.+)%20(.+)%20(.+)"
RewriteRule ^/contentsof.*              /author/%1-%2-%3-%4? [R=301,L]
RewriteCond %{QUERY_STRING}         "Authors=(.+)%20(.+)%20(.+)"
RewriteRule ^/contentsof.*              /author/%1-%2-%3? [R=301,L]
RewriteCond %{QUERY_STRING}         "Authors=(.+)%20(.+)"
RewriteRule ^/contentsof.*              /author/%1-%2? [R=301,L]
RewriteCond %{QUERY_STRING}         "Authors=(.+)\+(.+)"
RewriteRule ^/contentsof.*              /author/%1-%2? [R=301,L]

# catch all the rest (accented names, longer names, etc.)
RewriteCond %{REQUEST_URI}          ^/contentsof
RewriteRule ^/contentsof.*          /search/node [R=301,L]

# redirect old link to submission form (few requests, but can be important)
RewriteRule ^/to-submit/submissionform.* /jcom [R,L]
#
### /Plone

# TODO: verify if needed!
#
# Virtual paths per i meta Highwire
# ---------------------------------
#   Il PDF (e gli altri allegati) _devono_ stare nello stesso folder dei metadati:
#
# Physical PDF (Drupal upload dir): /sites/default/files/documents/JCOM_1601_2017_E.pdf
#
# Metadata (landing page) in:       /archive/16/01/JCOM_1601_2017_E
#                          o:       /archive/16/01/JCOM_1601_2017_C01/JCOM_1601_2017_C02
#        o (articoli vecchi):       /archive/01/01/E0101
# Virtual PDF (per Google):         /archive/16/01/JCOM_1601_2017_E.pdf
#                          o:       /archive/16/01/JCOM_1601_2017_C01/JCOM_1601_2017_C02.pdf
#        o (articoli vecchi):       /archive/01/01/jcom0101%282002%29E.pdf
#
# Soddisfiamo anche:
#      - i vecchi path Plone:       /archive/16/01/JCOM_1601_2017_E/JCOM_1601_2017_E.pdf
#
# Nota infine che pare non ci sia modo di passare il PT al dl-tracker con la url riscritta
# (senza fare un redirect visibile), quindi chiamiamo il tracker direttamente con il path
# da servire, utilizzando una env-var
# Se non ci fosse il tracker, basterebbero dei [NC,PT,L] al path fisico
#
RewriteRule   "^/archive/.*/(JCOM[^/]+_ATTACH_[^/]+)$" /dl-tracker/download.php [NC,L,E=virtual:/sites/default/files/documents/additional_file/$1]
RewriteRule   "^/archive/.*/(JCOM[^/]+\.pdf)"          /dl-tracker/download.php [NC,L,E=virtual:/sites/default/files/documents/$1]
#
# Questo lo facciamo come redirect:
RewriteRule   "^(/archive/.+/.+/.+).abstract"          https://%{SERVER_NAME}$1                          [R=302,L]

# TODO: drop this and rely on Janeway's own metrics (see
# e.g. journal.urls.urlpatterns l.35 and
# journal.views.download_galley)
#
# Direct download catcher
#------------------------
# NOTE: cannot be placed in the <Directory "/var/www/hosts/medialab"> section because
#       would be overridden by Drupal's .htaccess
RewriteCond %{REQUEST_FILENAME} ".(pdf|epub)$"
RewriteRule ^ /dl-tracker/download.php [L]