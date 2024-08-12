#!/bin/bash

# ATM (summer '24) we need to import papers from wjapp (where they are
# born) to wjs (where they are published).

# To do so, a script on wjapp side transfers a file (rsync) into wjs's
# "incoming" folder.

# We need wjs to be able to manage the transferred file, and the
# easiest way seems to have the user "wjs" be the owner of that file
# (and containing folder). Bub we also want to limit the places where
# the external script can write.

# So we create a folder "incoming" in wjs' home and setup another user
# "wjs-upload" that has write access to that dir _only_

# For additional security, we want to limit what the "wjs-upload" user can do.
# We do so by configuring the sshd daemon.
# Please see these files on wjs-prod or wjs-test:
# - /etc/ssh/sshd_config.d/user-wjs-upload.conf
#   limit the options for user wjs-upload (e.g. "PermitTTY no")
#   and force the only command that wjs-upload can issue ("ForceCommand /etc/ssh/validate-rsync")
#
# - /etc/ssh/validate-rsync
#   run rsync in server mode


# Before running me, ensure we have the correct users:
# useradd -s /bin/bash -m -d /home/wjs-upload -c "WJS rsync upload-only user" wjs-upload
# and also a link from ~wjs-upload/incoming to ~wjs/incoming/
# su - wjs-upload -c ln -s /home/wjs/incoming /home/wjs-upload/

# Please run me as root

exit 1  # just in case ;)

set -e

mkdir -p /home/wjs/{incoming,received}/{jcom,jcomal}
chown wjs:www-data /home/wjs/{incoming,received} /home/wjs/{incoming,received}/{jcom,jcomal}

# "incoming" folders are special: writable by wjs-upload also
# (the "received" folder are not special; nothing else to do with them)
chmod 0775 /home/wjs/incoming /home/wjs/incoming/{jcom,jcomal}

# set setgid bit on all incoming folders, so that created files have group www-data
chmod g+s /home/wjs/incoming /home/wjs/incoming/{jcom,jcomal}

# set the mask to read, write, and execute (this controls the maximum permissions that can be granted):
setfacl -m m::rwx /home/wjs/incoming /home/wjs/incoming/{jcom,jcomal}

# allow wjs-upload all permissions
setfacl -m g:wjs-upload:rwx /home/wjs/incoming /home/wjs/incoming/{jcom,jcomal}

# default group permissions (read, write, and execute):
setfacl -d -m u::rwx /home/wjs/incoming /home/wjs/incoming/{jcom,jcomal}
setfacl -d -m g::rwx /home/wjs/incoming /home/wjs/incoming/{jcom,jcomal}
setfacl -d -m g:wjs-upload:rwx /home/wjs/incoming /home/wjs/incoming/{jcom,jcomal}
setfacl -d -m m::rwx /home/wjs/incoming /home/wjs/incoming/{jcom,jcomal}
setfacl -d -m o::r-x /home/wjs/incoming /home/wjs/incoming/{jcom,jcomal}



## Expected:
#c# for i in /home/wjs/{incoming,received} /home/wjs/{incoming,received}/{jcom,jcomal}; do echo $i; getfacl $i; done > /tmp/serra.acls

#c# /home/wjs/incoming
#c# # file: home/wjs/incoming
#c# # owner: wjs
#c# # group: www-data
#c# # flags: -s-
#c# user::rwx
#c# group::rwx
#c# group:wjs-upload:rwx
#c# mask::rwx
#c# other::r-x
#c# default:user::rwx
#c# default:group::rwx
#c# default:group:wjs-upload:rwx
#c# default:mask::rwx
#c# default:other::r-x
#c#
#c# /home/wjs/received
#c# # file: home/wjs/received
#c# # owner: wjs
#c# # group: www-data
#c# user::rwx
#c# group::r-x
#c# other::r-x
#c#
#c# /home/wjs/incoming/jcom
#c# # file: home/wjs/incoming/jcom
#c# # owner: wjs
#c# # group: www-data
#c# # flags: -s-
#c# user::rwx
#c# group::rwx
#c# group:wjs-upload:rwx
#c# mask::rwx
#c# other::r-x
#c# default:user::rwx
#c# default:group::rwx
#c# default:group:wjs-upload:rwx
#c# default:mask::rwx
#c# default:other::r-x
#c#
#c# /home/wjs/incoming/jcomal
#c# # file: home/wjs/incoming/jcomal
#c# # owner: wjs
#c# # group: www-data
#c# # flags: -s-
#c# user::rwx
#c# group::rwx
#c# group:wjs-upload:rwx
#c# mask::rwx
#c# other::r-x
#c# default:user::rwx
#c# default:group::rwx
#c# default:group:wjs-upload:rwx
#c# default:mask::rwx
#c# default:other::r-x
#c#
#c# /home/wjs/received/jcom
#c# # file: home/wjs/received/jcom
#c# # owner: wjs
#c# # group: www-data
#c# user::rwx
#c# group::r-x
#c# other::r-x
#c#
#c# /home/wjs/received/jcomal
#c# # file: home/wjs/received/jcomal
#c# # owner: wjs
#c# # group: www-data
#c# user::rwx
#c# group::r-x
#c# other::r-x
