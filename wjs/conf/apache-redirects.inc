# JCOM redirects
RewriteEngine On

# Important! Papers' landing page
# From: //jcom.sissa.it/archive/13/04/JCOM_1304_2014_E
# To:   //jcom.sissa.it/article/pubid/JCOM_1304_2014_E
#
# Sub-documents / children
RewriteCond %{REQUEST_URI} "!.*pdf$"
RewriteCond %{REQUEST_URI} "!.*epub$"
RewriteRule "^/archive/(?<volume>\d{2})/(?<issue>\d{2})/(JCOM_\d{4}_\d{4}_)([A-Z0-9]+)/(JCOM_\d{4}_\d{4}_)([A-Z0-9]+)(?<rest>.*)$" "/article/pubid/$5$6$7" [R=301,L]
# old style - /archive/02/04/C020401/C020402
RewriteCond %{REQUEST_URI} "!.*pdf$"
RewriteCond %{REQUEST_URI} "!.*epub$"
RewriteRule "^/archive/(?<volume>\d{2})/(?<issue>\d{2})/([A-Z][0-9]{6})/([A-Z][0-9]{6})(?<rest>.*)$" "/article/pubid/$4$5" [R=301,L]
# old style - /archive/09/04/Jcom0904%282010%29C01/Jcom0904%282010%29C02
RewriteCond %{REQUEST_URI} "!.*pdf$"
RewriteCond %{REQUEST_URI} "!.*epub$"
RewriteRule "^/archive/(?<volume>\d{2})/(?<issue>\d{2})/(Jcom[A-Z0-9()]{13})/(Jcom[A-Z0-9()]{13})(?<rest>.*)$" "/article/pubid/$4$5" [R=301,L]
#
# Normal documents
RewriteCond %{REQUEST_URI} "!.*pdf$"
RewriteCond %{REQUEST_URI} "!.*epub$"
RewriteRule "^/archive/(?<volume>\d{2})/(?<issue>\d{2})/(?<pubid>[\w.()-]+)(?<rest>.*)$" "/article/pubid/$3$4" [R=301,L]
# I wasn't able to use a back-reference in the rule's pattern...
# RewriteRule "^/archive/.../(?<pubid_part>...)(?<eid_parent>...)/$3(?<eid_children>...)
#                                                                 ^^


# Drop redirects for URLs pre-2015
# E.g.
# - //jcom.sissa.it/jcom0303.html
# - //jcom.sissa.it/comment/com020102_or.html
# - //jcom.sissa.it/archive/13/04/JCOM_1304_2014_E/JCOM_1304_2014_E.pdf
# - //jcom.sissa.it/all-articles/RSS$
# - //jcom.sissa.it/search_rss.*
# - //jcom.sissa.it/.*atct_topic_view.*
# - //jcom.sissa.it/all/.*
# - //jcom.sissa.it/mc-logo.png
# - //jcom.sissa.it/contentsof.*
# - //jcom.sissa.it/to-submit/submissionform.*


# Issues - managed by Janeway
# From: //jcom.sissa.it/archive/03/03/
# To:   //jcom.sissa.it/issue/97/info/


# Files/galleys - managed by Janeway
# From: https://jcom.sissa.it/sites/default/files/documents/JCOM_1304_2014_E.pdf
# To:   https://jcom.sissa.it/article/1134/galley/2251/download/
#
# Main galley in citation_pdf_url (for google scholar, must be sibling or the paper's landing page)
# From: https://jcom.sissa.it/archive/20/02/JCOM_2002_2021_A01_en.pdf"
# To:   https://jcom.sissa.it/article/(\d+)/galley/(\d+)/download/"
#
# Main galley in citation_pdf_url for sub-documents
# The easiest thing here seams to just drop the "parent" part and proxy
# to Janeway for correct resolution
RewriteCond %{REQUEST_URI} ".*pdf$" [OR]
RewriteCond %{REQUEST_URI} ".*epub$"
RewriteRule "^/archive/(?<volume>\d{2})/(?<issue>\d{2})/(JCOM_\d{4}_\d{4}_)([A-Z0-9]+)/(JCOM_\d{4}_\d{4}_)([A-Z0-9]+)(?<rest>.*)$" "/sites/default/files/documents/$5$6$7" [PT,L]
# old style - /archive/02/04/C020401/C020402.pdf
RewriteCond %{REQUEST_URI} ".*pdf$" [OR]
RewriteCond %{REQUEST_URI} ".*epub$"
RewriteRule "^/archive/(?<volume>\d{2})/(?<issue>\d{2})/([A-Z][0-9]{6})/([A-Z][0-9]{6})(?<rest>.*)$" "/sites/default/files/documents/$4$5"  [PT,L]
# old style - /archive/09/04/Jcom0904%282010%29C01/Jcom0904%282010%29C02.pdf
RewriteCond %{REQUEST_URI} ".*pdf$" [OR]
RewriteCond %{REQUEST_URI} ".*epub$"
RewriteRule "^/archive/(?<volume>\d{2})/(?<issue>\d{2})/(Jcom[A-Z0-9()]{13})/(Jcom[A-Z0-9()]{13})(?<rest>.*)$" "/sites/default/files/documents/$4$5" [PT,L]


# RSS
# /rss.xml (no change)

# Archive and volumes --> /articles
RewriteRule "^/archive/?(\d{2}/?)?$" "/articles/" [R=301,L]

# Authors - NOT IMPLEMENTED!
# Drupal was: /author/pietro-greco
#             /author/j-f-h-kupper


# Keywords - NOT IMPLEMENTED!
# Drupal was: /keywords/public-engagement-science-and-technology


# Virtual paths per i meta Highwire (citation_pdf_url) - managed by Janeway
# Drupal was: /archive/16/01/JCOM_1601_2017_C01/JCOM_1601_2017_C02.pdf
# Now it is: /article/pubid/JCOM_1601_2017_C01/PDF-GALLEY-ID


# Supplementary materials / attachments - managed by Janeway
# Drupal was: /sites/default/files/documents/supplementary_material/JCOM_2106_2022_Y01_ATTACH_1.pdf


# Drop Drupal download tracker
# Used to capture direct links to galleys (there were served directly by apache).
# In the end it became unreliable because of corner cases related to custom bots
# that dirtied the data anyway.
# Was: "^/archive/.*/(JCOM[^/]+_ATTACH_[^/]+)$" /dl-tracker/download.php [NC,L,E=virtual:/sites/default/files/documents/additional_file/$1]
