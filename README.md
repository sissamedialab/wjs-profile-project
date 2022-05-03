# WJS - JCOM profile

+**Experimental**

A django app for Janeway that enriches the `Account` profile with the
field "profession".

**Important**: needs JCOM graphical theme (because of some modified templates)

**Important**: switch to branch `as-plugin` an see it's readme file.


## Install & use

This is a django app that should live inside Janeway. To use it, proceed as follows:

1. Activate your Janeway's virtual environment and install in development mode:
   `pip install -e .../wjs-profile-project`

1. Migrations should be run specifying the sub-package name: `./manage.py migrate jcom_profile`


### TODO: (aka "not yet implemented"...)

1. Activate your Janeway's virtual environment and install the package
   of this app (please see
   https://gitlab.sissamedialab.it/ml-foss/omlpi/-/packages)

2. Add "wjs\_profession" to Janeway's INSTALLED\_APPS in
   `src/core/janeway\_global\_setting.py` like this::
   ```
   INSTALLED_APPS = [
       ...
       'wjs_profession',
   ]
   ```


See https://gitlab.sissamedialab.it/medialab/janeway/-/issues/7
