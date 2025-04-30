# Monitor services via munin

Once munin has been installed and activated on the host,
- copy the plugins into the usual dir (usually `/etc/munin/plugins/`)
- add the configuration fragment to munin's config (usually `/etc/munin/plugin-conf.d/zzz-medialab.conf`)

See also related issues:
- qcluster https://gitlab.sissamedialab.it/wjs/specs/-/issues/1250
- redis https://gitlab.sissamedialab.it/wjs/specs/-/issues/1271
- jcomassistant[*] https://gitlab.sissamedialab.it/wjs/specs/-/issues/786
- yakunin[*] https://gitlab.sissamedialab.it/wjs/specs/-/issues/1303

[*] these are monitored via Nagios. Included here only for completeness.
