# JCOMAL redirects
RewriteEngine On

# RSS
# We had:
# - Plone:
#   - /all-editorials/RSS
#   - /all-focus/RSS
#   - /all-letters/RSS
#   - /all-articles/RSS
#   - /all/RSS
# - Drupal
#   - /es/rss.xml
#   - /pt-br/rss.xml
RewriteRule "^/.*/RSS$" "/feed/articles/" [R=301,L]
RewriteRule "^/(pt-br|es)/rss.xml" "/feed/articles/" [R=301,L]


# .abstract to landing page (#331)
RewriteCond %{REQUEST_URI} "\.abstract/?$"
RewriteRule "^/(?<language>pt-br|es)/(?<volume>\d{2})/(?<issue>\d{2})/([^/]+).abstract/?$" "/article/pubid/$4/" [R=301,L]

RewriteCond %{REQUEST_URI} "\.abstract/?$"
RewriteRule "^/article/pubid/([^/]+).abstract/?$" "/article/pubid/$1/" [R=301,L]


# Important! Papers' landing page
# From: //jcomal.sissa.it/es/05/02/JCOMAL_0502_2022_A01
# To:   //jcomal.sissa.it/article/pubid/JCOMAL_0502_2022_A01
#
# Normal documents - with something at the end
RewriteCond %{REQUEST_URI} "!.*pdf$"
RewriteCond %{REQUEST_URI} "!.*epub$"
RewriteRule "^/(?<language>pt-br|es)/(?<volume>\d{2})/(?<issue>\d{2})/(?<pubid>[^/]+)/(?<rest>.*)$" "/article/pubid/$4/$5" [R=301,L]
# Normal documents - without "/" at the end
RewriteCond %{REQUEST_URI} "!.*pdf$"
RewriteCond %{REQUEST_URI} "!.*epub$"
RewriteRule "^/(?<language>pt-br|es)/(?<volume>\d{2})/(?<issue>\d{2})/(?<pubid>[^/]+)$" "/article/pubid/$4/" [R=301,L]
#
# No Sub-documents / children in JCOMAL

# Issues - managed by Janeway
# From: //jcomal.sissa.it/archive/03/03/
# To:   //jcomal.sissa.it/issue/97/info/


# Files/galleys - managed by Janeway
# From: https://jcomal.sissa.it/sites/default/files/documents/JCOMAL_1304_2014_E.pdf
# To:   https://jcomal.sissa.it/article/1134/galley/2251/download/
#
# Main galley in citation_pdf_url (for google scholar, must be sibling or the paper's landing page)
# From: https://jcomal.sissa.it/es/03/02/JCOMAL_0302_2020_A02 [*]
# To:   https://jcomal.sissa.it/article/(\d+)/galley/(\d+)/download/"
#
# [*] this seems wrong in Drupal, as it points to the landing page, not to the PDF


# Archive and volumes --> /articles
RewriteRule "^/(?<language>pt-br|es)/archive/?(\d{2}/?)?$" "/issues/" [R=301,L]

# Authors - NOT IMPLEMENTED!
# Drupal was: /author/pietro-greco
#             /author/j-f-h-kupper


# Keywords - NOT IMPLEMENTED!
# Drupal was: /keywords/public-engagement-science-and-technology


# Supplementary materials / attachments - managed by Janeway
# Drupal was: /sites/default/files/documents/supplementary_material/JCOMAL_0302_2020_A02_ATTACH_1.pdf
