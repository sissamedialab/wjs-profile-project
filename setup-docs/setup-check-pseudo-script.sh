#!/bin/bash

echo "=== WARNING: PSEUDO CODE ===="
echo
echo "This started as a script but was never completed"
echo "and it is probably not worth-while."
echo
echo "Please consider it mainly as documentation."
echo

exit 1


# Wannabe script starts here ⇩

set -e

# echo in red color
error () {
    >&2 echo $(tput setaf 1)"$@"$(tput sgr0)
}
# echo in yellow color
warn () {
    >&2 echo $(tput setaf 3)"$@"$(tput sgr0)
}
# echo in blue color
info () {
    >&2 echo $(tput setaf 4)"$@"$(tput sgr0)
}

# Minimal sanity check of a Janeway instance
instance_root="$1"
if [[ ! -d "$instance_root/src" ]]
then
    error "Please provide the full path of a Janway instance (i.e. where the \"src\" folder is)"
    exit 1
fi

# Main group of wjs user is www-data
usermod -g www-data wjs

# Find and fix folders with wrong group
# =====================================
dir=/home/wjs
x=$(find "$dir" \! -group www-data)
if $?
then
    warn "Found files with wrong group:"
    echo "$x"
    read -p "Do you want to correct? " -n 1 -r
echo    # (optional) move to a new line
if [[ ! $REPLY =~ ^[Yy]$ ]]
then
    chown -R :www-data "$dir"
fi


echo
error "Please edit vassals ini files and ensure uwsgi runs as wjs:www-data and chmod-socket = 664!"
echo


# Find and fix files and folders with wrong mod
# =============================================
x=$(find "$dir" -perm "/g+w")
if $?
then
    warn "Found files with wrong permission:"
    echo "$x"
    read -p "Do you want to correct? " -n 1 -r
echo    # (optional) move to a new line
if [[ ! $REPLY =~ ^[Yy]$ ]]
then
    find "$dir" -perm "/g+w" -exec chmod "g-w" {} \;
fi


echo
error "Please ensure that cron jobs are run by the wjs user!"
echo
